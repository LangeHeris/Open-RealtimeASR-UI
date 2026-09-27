"""FunASR 本地流式语音识别封装（paraformer-zh-streaming）。

接口与 tencent_realtime.TencentRealtimeASR 完全一致，便于在 VoiceApp 中互换：
- start() 准备会话（重置缓存）
- send_audio(block) 推送音频块（numpy int16 数组）
- stop() 结束会话（喂入 is_final 收尾）
- is_connected() 模型是否就绪
- 回调：on_result(text, slice_type, index) / on_error(code, message) / on_state(state)

设计要点：
1. funasr/torch 延迟导入：未安装时不影响程序启动，仅在真正使用时报清晰错误。
2. 流式 chunk：chunk_size 取预设档位（CHUNK_PRESETS，默认 [0,10,5] 即 600ms 窗），
   stride=中值*960 样本。本项目 block_size=1600(100ms)，按样本缓冲累积到一个窗口喂一次。
3. 模型预热：load_model() 可在后台线程提前调用，避免首次录音卡顿。
4. 工作线程：model.generate() 耗时较长（50-200ms），放在独立线程执行，
   避免 sounddevice 音频回调线程被阻塞导致丢帧/VAD 失效。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# 复用腾讯云封装的常量语义，保持回调接口一致
from .tencent_realtime import (
    SLICE_START,
    SLICE_INTERMEDIATE,
    SLICE_FINAL,
    STATE_DISCONNECTED,
    STATE_CONNECTING,
    STATE_CONNECTED,
    STATE_ERROR,
)

logger = logging.getLogger(__name__)

ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[int, str], None]
StateCallback = Callable[[str], None]
ProgressCallback = Callable[[int, str], None]  # (percent, message)

# paraformer-zh-streaming 流式参数：chunk_size 单位 60ms 帧，[左, 中, 右]；
# stride = 中 * 960 样本。官方示例：[0,10,5](600ms) 与 [0,8,4](480ms)；
# 中值越大上下文越多、识别越稳，但逐字上屏延迟越高（实测组合见 CHUNK_PRESETS）
_CHUNK_SIZE = [0, 10, 5]          # 600ms 检测窗（默认档）
_CHUNK_STRIDE = _CHUNK_SIZE[1] * 960  # 9600 样本 = 600ms
_ENCODER_LOOK_BACK = 4
_DECODER_LOOK_BACK = 1

# 识别窗口预设：配置项 funasr.chunk_preset 的取值（中文值，设置界面原样显示）
CHUNK_PRESETS = {
    "低延迟": [0, 8, 4],    # 480ms 窗 + 240ms 前瞻，出字更快（官方示例）
    "默认": [0, 10, 5],     # 600ms 窗 + 300ms 前瞻
    "高准确": [0, 12, 6],   # 720ms 窗 + 360ms 前瞻，上下文更多（本机实测可用）
}

# 切句哨兵：本地 VAD 检测到说话停顿时，通知工作线程把当前片段 flush 成 final
_SEGMENT_END = object()


def _bundled_model_dir(model: str) -> Path | None:
    """随发行分发的模型目录（平台 depot 的 models/<model>；GitHub 版不随包）。

    候选安装根按序命中即止：
    - frozen（PyInstaller）：exe 同目录 models/<model>
    - 便携运行时（渠道版）：paths.install_dir() 命中的安装根——env/Scripts/
      python.exe（venv）与 runtime/python.exe 两种进程布局都识别
    - 兜底 cwd（launcher 未必以安装根为工作目录）

    model 取自配置，只接受单一目录名（含路径分隔符、绝对路径或 "." / ".."
    一律视为不命中），避免拼出安装根之外的路径。命中返回绝对目录，未命中
    返回 None（走现有 local_dir / 在线下载链路，行为与现状一致）。

    发行层门控：随包模型是发行 depot 的一部分，GitHub 发行物不含 models/。
    非商店版一律不探测磁盘——否则用户工作目录下恰好放了同名目录就会被
    抢先命中（优先级高于用户自己配置的 local_dir），悄悄改掉既有行为。
    """
    try:
        from ..steam_integration import is_steam_build
        if not is_steam_build():
            return None
    except Exception:
        return None

    name = (model or "").strip()
    if (not name or name in (".", "..")
            or "/" in name or "\\" in name or os.path.isabs(name)):
        return None
    if getattr(sys, "frozen", False):
        # 商店版将来若改用 PyInstaller 打包（当前走便携运行时 + launcher，
        # 非 frozen），模型随 exe 同目录投放；与下面同样受发行层门控
        roots = [Path(sys.executable).parent]
    else:
        # install_dir() 自身已吞异常并可能返回 None（布局未识别）
        from ..paths import install_dir

        root = install_dir()
        # cwd 兜底：launcher 未必以安装根为工作目录
        roots = [root] if root is not None else []
        roots.append(Path.cwd())
    seen: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
        except Exception:
            continue
        if root in seen:
            continue
        seen.add(root)
        cand = root / "models" / name
        if cand.is_dir():
            return cand
    return None


def _model_cache_root() -> Path:
    """modelscope 缓存根目录。

    新版布局 ~/.cache/modelscope/models/{org}--{model}/snapshots/...，
    旧版 ~/.cache/modelscope/hub/models/{org}/{model}——两种布局的
    模型目录名都含 "paraformer"，按目录名匹配即可。
    """
    return Path.home() / ".cache" / "modelscope"


def _model_cache_dirs() -> list[Path]:
    """所有 paraformer 模型缓存目录（限深 4 层，仅目录名匹配）。

    旧实现 rglob("*.pt") 全递归 ~/.cache/modelscope：装过多个模型后目录内
    数万小文件，扫描可卡数分钟——加载线程长时间无任何进展、UI 一直停在
    "模型加载中"的直接原因。os.walk 只遍历目录树（不 stat 文件），
    毫秒级完成。
    """
    root = _model_cache_root()
    dirs: list[Path] = []
    if not root.is_dir():
        return dirs
    try:
        for dirpath, dirnames, _filenames in os.walk(root):
            # 深度上限 4 层：models/org--model/snapshots 与
            # hub/models/org/model 两种布局都覆盖
            if len(Path(dirpath).relative_to(root).parts) >= 4:
                dirnames[:] = []
            for d in dirnames:
                if "paraformer" in d.lower():
                    dirs.append(Path(dirpath) / d)
    except OSError:
        pass
    return dirs


def _model_cache_present() -> bool:
    """paraformer 模型缓存是否已下载。"""
    return bool(_model_cache_dirs())


def _model_cache_bytes() -> int:
    """paraformer 模型缓存当前占用字节数（下载进度估算用）。

    只统计 paraformer 目录子树：下载期间目录在增长，其他模型的缓存
    不计入，进度估算更准；也避免每秒全递归整个 ~/.cache/modelscope。
    """
    total = 0
    for d in _model_cache_dirs():
        try:
            for p in d.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


class FunASRRealtime:
    """FunASR 本地流式语音识别客户端。

    模型加载较慢（CPU 3-8s，GPU 1-3s），建议在程序启动后调用 load_model() 预热。
    若未预热，首次 start() 会同步加载，期间 UI 会卡顿。
    """

    # 管线以该标记判断"stop() 同步冲刷 FINAL"（正常停止放行冲刷上屏）；
    # 腾讯 preview 分支虽有 end_segment 但 stop 走云端异步冲刷，不置此标记
    sync_stop_flush = True

    def __init__(
        self,
        model: str = "paraformer-zh-streaming",
        device: str = "auto",
        hotword: str = "",
        chunk_preset: str = "默认",
        local_dir: str = "",
        model_hub: str = "ms",
        model_revision: str = "",
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        self.model_name = model
        self.device = device
        self.hotword = hotword
        # 本地权重目录：非空且存在时直接本地加载，不联网下载（用户自行下载模型场景）
        self.local_dir = (local_dir or "").strip()
        _hub = (model_hub or "ms").strip().lower()
        self.model_hub = _hub if _hub in ("ms", "modelscope", "hf", "huggingface") else "ms"
        self.model_revision = (model_revision or "").strip()
        # 识别窗口预设：未知取值回退默认档（配置层已校验，此处防御）
        self._chunk_size = list(CHUNK_PRESETS.get(chunk_preset, _CHUNK_SIZE))
        self._chunk_stride = self._chunk_size[1] * 960
        self.on_result = on_result
        self.on_error = on_error
        self.on_state = on_state
        self.on_progress = on_progress

        self._model = None
        self._cache: dict = {}
        self._buffer = np.zeros(0, dtype=np.float32)
        self._index = 0
        self._accumulated_text = ""  # 会话内累积文本（paraformer-streaming 每个 chunk 只返回当前片段）
        self._state = STATE_DISCONNECTED
        self._lock = threading.Lock()
        self._loading = False
        self._load_error: Optional[str] = None

        # 工作线程：把 model.generate() 移出音频回调线程，避免阻塞采集
        # maxsize=100（约10秒音频）：模型处理跟不上时丢最旧块，防止内存积压
        self._audio_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(maxsize=100)
        self._worker: Optional[threading.Thread] = None
        self._stop_worker = threading.Event()
        self._session_id = 0  # 会话版本号，旧工作线程检测到变化后自动退出

    # ---- 状态查询 ----

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        """模型已加载且就绪。"""
        return self._model is not None and self._state == STATE_CONNECTED

    def is_loading(self) -> bool:
        """模型正在加载中。"""
        return self._loading

    def is_ready(self) -> bool:
        """模型已就绪，可立即开始录音。"""
        return self._model is not None and not self._loading

    # ---- 模型加载 / 预热 ----

    def load_model(self) -> bool:
        """加载模型。可在后台线程提前调用以预热。

        成功返回 True；失败返回 False 并记录错误。
        线程安全：重复调用不会重复加载。
        """
        with self._lock:
            if self._model is not None:
                return True
            if self._loading:
                # 等待正在进行的加载
                while self._loading:
                    self._lock.release()
                    time.sleep(0.05)
                    self._lock.acquire()
                return self._model is not None

            self._loading = True
            self._load_error = None

        # 提前初始化：异常发生在 stop_poll 赋值前时（依赖缺失/目录不存在），
        # except 分支的 stop_poll.set() 仍可安全执行
        stop_poll = threading.Event()
        try:
            # 依赖前置探测：find_spec 只查模块搜索路径、不执行包代码（微秒级）。
            # 必须放在任何模型缓存扫描/状态回调之前——旧顺序先做 rglob 全递归
            # 扫描 ~/.cache/modelscope（装过多个模型后可卡数分钟），打包版
            # 根本加载不了却让 UI 长时间停在"模型加载中"。
            if importlib.util.find_spec("funasr") is None:
                if getattr(sys, "frozen", False):
                    # 打包版不可能 pip install：本地引擎依赖 funasr+torch
                    # 约 2GB+，未打包进 exe，需用源码方式运行
                    raise RuntimeError(
                        "打包版不含本地引擎依赖（funasr + torch 体积过大），"
                        "请改用云端引擎，或以源码方式运行本地引擎"
                    )
                raise RuntimeError(
                    "未安装 funasr，请运行：pip install funasr torch torchaudio"
                )

            if self.on_state:
                self.on_state(STATE_CONNECTING)
            logger.info("开始加载 FunASR 模型：%s (device=%s)", self.model_name, self.device)

            # 真正导入：find_spec 通过后仍可能因内部依赖损坏失败，兜底提示
            try:
                from funasr import AutoModel
            except ImportError as exc:
                raise RuntimeError(
                    "funasr 依赖不完整，请运行：pip install funasr torch torchaudio"
                ) from exc

            # 模型引用与下载渠道解析，优先级：随发行分发的模型 > 用户配置的
            # local_dir > 按 model_hub（ms/hf）在线下载。随包模型由发行包投放
            # models/<model>，命中即离线加载（商店版零联网、无需任何配置）。
            bundled = _bundled_model_dir(str(self.model_name))
            if bundled is not None:
                model_ref: str = str(bundled.resolve())
                hub_kwargs: dict = {}
                logger.info("使用随发行分发的本地模型：%s", model_ref)
            elif self.local_dir:
                local_path = Path(self.local_dir)
                if not local_path.is_dir():
                    raise RuntimeError(
                        f"本地模型目录不存在：{self.local_dir}（请确认已下载模型权重并填写正确路径）"
                    )
                model_ref = str(local_path.resolve())
                hub_kwargs = {}
                logger.info("使用本地模型目录：%s", model_ref)
            else:
                model_ref = self.model_name
                hub_kwargs = {"hub": self.model_hub}
                if self.model_revision:
                    hub_kwargs["model_revision"] = self.model_revision
                    logger.info("锁定模型版本：%s", self.model_revision)

            # 模型下载进度轮询：仅当模型尚未下载时才启动
            # 已下载完成时扫描目录大小约等于模型大小，会误显示"下载中94%"
            model_already_downloaded = (
                bundled is not None or bool(self.local_dir) or _model_cache_present()
            )

            if self.on_progress:
                if model_already_downloaded:
                    # 模型已下载，只需显示加载中（无下载进度）
                    self.on_progress(100, "模型加载中...")
                else:
                    # 首次下载：按 paraformer 目录子树大小估算下载进度
                    # （旧实现每秒 rglob 全递归整个 ~/.cache/modelscope，
                    #   多模型环境下轮询本身就把 IO 打满）
                    def _poll():
                        expected_mb = 900
                        last_pct = -1
                        while not stop_poll.is_set():
                            try:
                                total = _model_cache_bytes()
                                pct = min(99, int(total / (expected_mb * 1024 * 1024) * 100))
                                if pct != last_pct:
                                    last_pct = pct
                                    self.on_progress(pct, f"模型下载中 {pct}%")
                            except Exception:
                                pass
                            stop_poll.wait(1.0)
                    threading.Thread(target=_poll, daemon=True, name="funasr-progress").start()

            device = self._resolve_device()
            self._model = AutoModel(model=model_ref, device=device, **hub_kwargs)
            stop_poll.set()
            logger.info("FunASR 模型加载完成：%s", self.model_name)

            with self._lock:
                self._loading = False
            if self.on_state:
                self.on_state(STATE_CONNECTED)
            return True

        except Exception as exc:
            stop_poll.set()
            logger.exception("FunASR 模型加载失败")
            self._load_error = str(exc)
            with self._lock:
                self._loading = False
            if self.on_error:
                self.on_error(-1, f"模型加载失败：{exc}")
            if self.on_state:
                self.on_state(STATE_ERROR)
            return False

    def _resolve_device(self) -> str:
        """解析实际使用的设备：auto → 优先 cuda。"""
        if self.device != "auto":
            return self.device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    # ---- 会话控制 ----

    def start(self) -> None:
        """开始一次识别会话：确保模型就绪并重置缓存。"""
        if not self.is_ready():
            ok = self.load_model()
            if not ok:
                raise RuntimeError(f"FunASR 模型未就绪：{self._load_error or '加载失败'}")

        self._cache = {}
        self._buffer = np.zeros(0, dtype=np.float32)
        self._index = 0
        self._accumulated_text = ""
        self._state = STATE_CONNECTED
        self._stop_worker.clear()
        self._session_id += 1  # 新会话：旧工作线程检测到此变化后自动退出
        # 清空旧队列（可能残留上一会话数据）
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        # 启动工作线程：model.generate() 在此线程执行，不阻塞音频回调
        self._worker = threading.Thread(
            target=self._worker_loop, args=(self._session_id,), daemon=True, name="funasr-worker"
        )
        self._worker.start()
        if self.on_state:
            self.on_state(STATE_CONNECTED)
        logger.info("FunASR 会话已开始")

    def send_audio(self, block: np.ndarray) -> None:
        """推送音频块（int16 numpy）到队列。非阻塞，供音频回调线程调用。"""
        if not self.is_ready() or self._state != STATE_CONNECTED:
            return
        # 采集回调返回 (frames, channels)，需压平为 1D
        audio = np.asarray(block).reshape(-1).astype(np.float32) / 32768.0
        try:
            self._audio_queue.put_nowait(audio)
        except queue.Full:
            # 队列积压（模型处理跟不上），丢弃最旧的一块保证实时性
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio)
            except queue.Empty:
                pass
        # 临时诊断：队列积压说明 worker 消费跟不上（卡顿定位用），限频 1 次/秒
        _qsize = self._audio_queue.qsize()
        if _qsize >= 20 and time.time() - getattr(self, "_diag_backlog_at", 0.0) > 1.0:
            self._diag_backlog_at = time.time()
            logger.warning("DIAG 队列积压 %d 块（约 %.1f 秒），worker 消费停滞？",
                           _qsize, _qsize * 0.1)

    def end_segment(self) -> None:
        """切句：通知工作线程把当前已识别片段 flush 成 final（非阻塞、线程安全）。

        供音频回调线程在本地 VAD 检测到说话停顿时调用，等效云端引擎的
        服务端 max_sentence_silence 断句，实现"边录边逐句上屏"而非只在
        录音结束后一次性上屏。哨兵与音频块由同一线程（音频回调）入队，
        顺序天然正确。
        """
        if self._state != STATE_CONNECTED:
            return
        try:
            self._audio_queue.put_nowait(_SEGMENT_END)
        except queue.Full:
            # 队列积压（模型处理跟不上）：丢弃最旧一块腾位给哨兵，切句不丢
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(_SEGMENT_END)
            except queue.Empty:
                pass

    def stop(self) -> None:
        """结束会话：等待工作线程收尾后返回（final 已发出）。

        与云端引擎行为对齐——"停止即上屏"：sentinel 入队后 join worker，
        worker 处理剩余音频、喂 is_final、发出 SLICE_FINAL 回调后才退出，
        join 返回即 final 已在注入链路上。

        join 从 toggle/删除键/超时检测线程调用（均为后台线程），不冻结 UI。
        worker 卡死时放弃等待（final 延迟到达仍会注入，只是晚一点）。
        """
        if not self.is_ready():
            self._state = STATE_DISCONNECTED
            return

        # 临时诊断：停止时的队列水位——积压大说明会话期间消费停滞
        logger.info("DIAG stop 时队列剩余 %d 块", self._audio_queue.qsize())
        self._stop_worker.set()
        try:
            self._audio_queue.put_nowait(None)  # sentinel
        except queue.Full:
            # 队列积压满（模型处理跟不上）：清最旧一块腾位给 sentinel
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(None)
            except queue.Empty:
                pass

        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)
            if worker.is_alive():
                logger.warning("DIAG stop join 超时：worker 2 秒未收尾（卡在 generate？）")

        self._worker = None
        self._state = STATE_DISCONNECTED
        if self.on_state:
            self.on_state(STATE_DISCONNECTED)
        logger.info("FunASR 会话已结束")

    @staticmethod
    def _is_silence(buf: np.ndarray) -> bool:
        """判断 float32 音频缓冲是否为纯静音（用于过滤语气词幻觉）。

        RMS < 0.02（约 -34dBFS，int16 量纲 ~655）：覆盖安静环境底噪；
        真实语音能量远高于此，不会被误判。空缓冲视为静音。
        """
        if buf is None or buf.size == 0:
            return True
        try:
            rms = float(np.sqrt(np.mean(np.square(buf, dtype=np.float64))))
        except Exception:
            return True
        return rms < 0.02

    def _flush_final(self, session_id: int) -> None:
        """把当前缓冲 flush 成 final（切句/停止收尾）。

        若本段既无识别文本、缓冲又是纯静音（VAD 误判停顿，或超时/手动
        停止时只剩静音残段），则跳过 flush——避免 paraformer 对静音/底噪
        残段 flush 出"嗯/啊"等语气词幻觉，被误注入输入框。
        """
        if self._accumulated_text == "" and self._is_silence(self._buffer):
            self._buffer = np.zeros(0, dtype=np.float32)
            return
        if len(self._buffer) > 0:
            self._feed(self._buffer, is_final=True, session_id=session_id)
        else:
            self._feed(np.zeros(self._chunk_stride, dtype=np.float32),
                       is_final=True, session_id=session_id)
        self._buffer = np.zeros(0, dtype=np.float32)

    # ---- 工作线程：从队列取音频、累积、喂入模型 ----

    def _worker_loop(self, my_session_id: int) -> None:
        """工作线程主循环：从队列取音频块，累积到一个识别窗口长度后喂入模型。

        my_session_id: 启动时的会话版本号，若与 self._session_id 不一致说明
        已被新会话取代，应立即退出，避免新旧线程同时操作 model.generate。
        """
        while True:
            # 会话已被新 start() 取代，立即退出
            if my_session_id != self._session_id:
                logger.debug("工作线程检测到会话变更，退出")
                return

            try:
                block = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                # 超时但未收到结束信号，继续等
                if self._stop_worker.is_set():
                    # 已停止但 sentinel 未到（极端：队列满未入队成功）——
                    # 同样喂 is_final 收尾，保证 final 不丢
                    self._flush_final(my_session_id)
                    break
                continue

            # 取到数据后再次检查会话一致性（防止 stop→start 期间数据残留）
            if my_session_id != self._session_id:
                logger.debug("工作线程检测到会话变更，退出")
                return

            if block is _SEGMENT_END:
                # 切句：flush 当前片段为 final（等效云端服务端断句），
                # 重置流式 cache 与累积文本后继续识别下一句
                self._flush_final(my_session_id)
                self._cache = {}
                continue

            if block is None:
                # sentinel：处理剩余缓冲后退出
                self._flush_final(my_session_id)
                break

            # 累积到一个窗口长度后喂一次
            self._buffer = np.concatenate([self._buffer, block])
            while len(self._buffer) >= self._chunk_stride:
                chunk = self._buffer[:self._chunk_stride]
                self._buffer = self._buffer[self._chunk_stride:]
                self._feed(chunk, is_final=False, session_id=my_session_id)

    # ---- 内部：喂入模型 ----

    def _feed(self, chunk: np.ndarray, is_final: bool, session_id: int = 0) -> None:
        """喂入一个 chunk 并分发识别结果。

        session_id: 调用方的工作线程会话ID，防止旧线程在 generate 期间
        被新会话取代后继续写 cache 导致竞态。
        """
        # generate 是耗时操作，调用前再次确认会话一致性
        if session_id != self._session_id:
            logger.debug("_feed 检测到会话变更，跳过本次 generate")
            return
        # 临时诊断：定位会话中停顿——记录每次 generate 耗时
        _gen_t0 = time.time()
        try:
            res = self._model.generate(
                input=chunk,
                cache=self._cache,
                is_final=is_final,
                chunk_size=self._chunk_size,
                encoder_chunk_look_back=_ENCODER_LOOK_BACK,
                decoder_chunk_look_back=_DECODER_LOOK_BACK,
                hotword=self.hotword,
            )
        except Exception as exc:
            logger.exception("FunASR 识别异常")
            if self.on_error:
                self.on_error(-1, f"识别异常：{exc}")
            return
        finally:
            _gen_dt = time.time() - _gen_t0
            if _gen_dt > 0.5:
                logger.warning(
                    "DIAG generate 慢：%.1fs final=%s 队列剩余=%d",
                    _gen_dt, is_final, self._audio_queue.qsize(),
                )

        # generate 返回后再次检查，避免旧线程写脏数据
        if session_id != self._session_id:
            logger.debug("_feed generate 后检测到会话变更，丢弃结果")
            return

        if not res:
            return

        text = res[0].get("text", "").strip()
        # paraformer-streaming 每个 chunk 只返回当前片段文本，需累积
        if text:
            self._accumulated_text += text

        if is_final:
            # 最终结果：发送累积的完整文本
            final_text = self._accumulated_text
            self._accumulated_text = ""  # 重置，供下次会话使用
            if final_text:
                self._index += 1
                if self.on_result:
                    self.on_result(final_text, SLICE_FINAL, self._index)
        else:
            # 中间结果：仅当本 chunk 有新增文本时才发送（paraformer-streaming
            # 增量模式下静音 chunk 无新文本）。原先"累积文本非空就重发"会让
            # 静音期每 600ms 重复发旧文本，进而不断刷新超时计时导致本地模型
            # 永不超时，同时高频重绘悬浮条
            if text:
                self._index += 1
                if self.on_result:
                    self.on_result(self._accumulated_text, SLICE_INTERMEDIATE, self._index)
