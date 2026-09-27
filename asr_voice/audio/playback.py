"""下行音频播放：24kHz 单声道 int16 PCM 流式输出。

S2S 对话的下行音频由 StartSession 的 `tts.audio_config.format="pcm_s16le"` 指定为
裸 PCM（默认是 OGG/Opus，项目无解码器），因此这里只需一个环形缓冲 +
sounddevice OutputStream，**零新依赖**。

线程模型：`write()` / `clear()` 由 client 的 asyncio 线程调用（barge-in 要求毫秒级，
刻意不经 Qt 主线程），`_pull()` 由 sounddevice 的播放回调线程调用，
两者用一把 `threading.Lock` 保护 deque。

设备兼容：部分老声卡/虚拟音频设备不支持 24000Hz，`start()` 捕获 PortAudioError 后
降级用 16000Hz 打开设备，并在 `write()` 里用 numpy 线性插值重采样 24k→16k。

与 capture.py 的对称：capture 是 InputStream + on_block 回调向外推，
playback 是 OutputStream + _pull 回调向内拉，两者不共享任何状态。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 24000      # 对应 tts.audio_config.sample_rate
FALLBACK_SAMPLE_RATE = 16000     # 设备不支持 24k 时的降级采样率
DEFAULT_BLOCKSIZE = 480          # 20ms@24k；clear() 后最多这么久扬声器就静音
BYTES_PER_SAMPLE = 2             # int16


class AudioPlayer:
    """24k PCM 流式播放器。

    流**常驻**：进入对话 `start()`、退出 `stop()`，不在每次 TTS 开关流
    （开流有 50~200ms 延迟，会造成每句首字卡顿）。WASAPI 共享模式不阻止
    其他程序发声。
    """

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = 1,
                 device: Optional[str] = None, blocksize: int = DEFAULT_BLOCKSIZE):
        self.requested_rate = sample_rate
        self.sample_rate = sample_rate      # 实际打开的采样率（降级后可能是 16000）
        self.channels = channels
        self.device = device or None        # sounddevice 按子串匹配；None=系统默认输出
        self.blocksize = blocksize
        self._buf: deque = deque()
        self._buf_bytes = 0
        self._lock = threading.Lock()
        self._stream: Optional[sd.OutputStream] = None
        self._downsample = False            # True=需 24k→16k 重采样
        self._written = 0                   # 累计写入字节（日志用）
        self._underruns = 0                 # 累计补零次数（缓冲不足）

    # ---- 生命周期 ----

    def start(self) -> None:
        """打开输出流。24k 失败时降级到 16k + 重采样；两者都失败才抛。"""
        if self._stream is not None:
            logger.warning("播放流已在运行，忽略重复 start")
            return
        rates = [self.requested_rate]
        if FALLBACK_SAMPLE_RATE != self.requested_rate:
            rates.append(FALLBACK_SAMPLE_RATE)
        for idx, rate in enumerate(rates):
            is_last = idx == len(rates) - 1
            stream = None
            try:
                stream = sd.OutputStream(
                    samplerate=rate,
                    channels=self.channels,
                    dtype="int16",
                    # 帧数随采样率缩放，保持 20ms 恒定时延（见 DEFAULT_BLOCKSIZE 注释）
                    blocksize=max(1, int(self.blocksize * rate / self.requested_rate)),
                    device=self.device,
                    callback=self._pull,
                )
                stream.start()
            except sd.PortAudioError as exc:
                if stream is not None:
                    # 构造成功但 start 失败：句柄必须还回去，否则可能拖累降级重试
                    try:
                        stream.close()
                    except Exception:
                        logger.debug("关闭未启动的播放流失败", exc_info=True)
                if is_last:
                    logger.error("无法打开播放设备（%dHz）：%s", rate, exc)
                    raise
                logger.warning("%dHz 输出设备不支持（%s），降级到 %dHz + 重采样",
                               rate, exc, rates[idx + 1])
                continue
            self._stream = stream
            self.sample_rate = rate
            self._downsample = rate != self.requested_rate
            logger.info("播放流已启动 %dHz %dch block=%d device=%s%s",
                        rate, self.channels, stream.blocksize,
                        self.device or "（系统默认输出）",
                        "（降级重采样）" if self._downsample else "")
            return

    def stop(self) -> None:
        """清缓冲并关流。重复调用安全。"""
        with self._lock:
            self._buf.clear()
            self._buf_bytes = 0
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.warning("关闭播放流失败", exc_info=True)
            logger.info("播放流已停止（累计写入 %d 字节，补零 %d 次）",
                        self._written, self._underruns)

    def is_running(self) -> bool:
        return self._stream is not None

    # ---- 数据 ----

    def write(self, pcm: bytes) -> None:
        """追加待播 PCM（int16 小端，采样率 = `requested_rate`）。线程安全。

        由 client 的 asyncio 线程直接调用，不经 Qt 主线程：音频约 40ms 一包，
        绕主线程排队会积压并引入不可控延迟。未开流时静默丢弃（不缓存，避免
        开流前堆积一大堆陈旧音频一开就全喷出来）。
        """
        if not pcm or self._stream is None:
            return
        if self._downsample:
            pcm = self._resample(pcm)
        with self._lock:
            self._buf.append(pcm)
            self._buf_bytes += len(pcm)
        self._written += len(pcm)

    def clear(self) -> None:
        """barge-in：立即丢弃全部未播缓冲。

        由 asyncio 线程在收到 `ASRInfo(450)` 时同步调用（打断时序要求毫秒级，
        这是设计文档里刻意不绕主线程的三处之一）。
        """
        with self._lock:
            dropped = self._buf_bytes
            self._buf.clear()
            self._buf_bytes = 0
        if dropped:
            logger.debug("barge-in 清空播放缓冲 %d 字节（约 %d ms）", dropped,
                         dropped * 1000
                         // max(1, self.sample_rate * self.channels * BYTES_PER_SAMPLE))

    @property
    def pending_ms(self) -> int:
        """缓冲剩余毫秒数。SPEAKING→LISTENING 的排空判定用（主线程 50ms 轮询）。"""
        with self._lock:
            nbytes = self._buf_bytes
        denom = self.sample_rate * self.channels * BYTES_PER_SAMPLE
        return nbytes * 1000 // denom if denom else 0

    # ---- 内部 ----

    def _resample(self, pcm: bytes) -> bytes:
        """线性插值重采样 `requested_rate` → `sample_rate`（仅降级路径用）。"""
        src = np.frombuffer(pcm, dtype="<i2")
        if src.size == 0:
            return b""
        dst_len = max(1, int(round(src.size * self.sample_rate / self.requested_rate)))
        if src.size < 2:
            # 单采样无斜率可言，直接复制成目标长度即可
            return np.repeat(src, dst_len).astype("<i2").tobytes()
        x_old = np.linspace(0.0, 1.0, num=src.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
        return np.interp(x_new, x_old, src.astype(np.float64)).astype("<i2").tobytes()

    def _pull(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 播放回调：从缓冲拼够 frames 个采样，不足补零。

        多取的字节切回缓冲头部（不能丢，否则丢字）；不阻塞、不抛异常。
        """
        if status:
            logger.debug("播放状态：%s", status)
        need = frames * self.channels * BYTES_PER_SAMPLE
        with self._lock:
            chunks = []
            got = 0
            while self._buf and got < need:
                chunk = self._buf.popleft()
                got += len(chunk)
                if got > need:
                    split = len(chunk) - (got - need)
                    chunks.append(chunk[:split])
                    self._buf.appendleft(chunk[split:])   # 剩余部分还回，字节数不减
                    got = need
                else:
                    chunks.append(chunk)
            self._buf_bytes -= got
            data = b"".join(chunks)
        if len(data) < need:
            self._underruns += 1
            data += b"\x00" * (need - len(data))
        outdata[:] = np.frombuffer(data, dtype="<i2").reshape(frames, self.channels)
