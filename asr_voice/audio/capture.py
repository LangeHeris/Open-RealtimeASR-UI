"""麦克风音频采集。基于 sounddevice，在独立线程中通过回调推送音频块。"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

AudioCallback = Callable[[np.ndarray], None]


class AudioCapture:
    """麦克风采集器。

    通过 sd.InputStream 在后台线程采集，每收集到一个 block 就调用 on_block。
    block 为 numpy 数组，shape=(frames, channels)，dtype 由配置决定（默认 int16）。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_size: int = 1600,
        dtype: str = "int16",
        on_block: Optional[AudioCallback] = None,
        device: Optional[str] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.dtype = dtype
        self.on_block = on_block
        # 设备名（sounddevice 按子串匹配）；None/空=系统默认输入设备
        self.device = device or None
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                logger.warning("音频流已在运行，忽略重复 start")
                return
            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    blocksize=self.block_size,
                    dtype=self.dtype,
                    callback=self._callback,
                    device=self.device,
                )
                self._stream.start()
                logger.info(
                    "音频采集已启动 %dHz %dch block=%d device=%s",
                    self.sample_rate,
                    self.channels,
                    self.block_size,
                    self.device or "（系统默认）",
                )
            except sd.PortAudioError as exc:
                logger.error("无法打开音频设备：%s", exc)
                raise

    def stop(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("音频采集已停止")

    def is_running(self) -> bool:
        return self._stream is not None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("音频状态：%s", status)
        if self.on_block is not None:
            # 复制后传出，避免 sounddevice 内部缓冲复用导致数据被覆盖
            self.on_block(indata.copy())
