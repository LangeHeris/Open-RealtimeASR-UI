"""暂停播放/全局静音：录音期间中断后台音频，停止后自动恢复。

设计文档 docs/superpowers/specs/2026-08-31-pause-background-audio-design.md。

- 暂停播放（SMTC）：逐个暂停正在播放的媒体会话，恢复时断点续播；
- 全局静音（WASAPI）：静音本进程以外的全部渲染会话，恢复时解除。
两类动作可同时生效；恢复只恢复本次会话动过的，且恢复前复查状态。

winrt / pycaw 均为延迟导入：未安装只影响本功能的可用性探测，
不影响程序其余部分运行。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

logger = logging.getLogger(__name__)

ENGAGE_TIMEOUT = 2.0   # 秒：暂停/静音动作等待上限
RESUME_TIMEOUT = 1.5   # 秒：恢复动作等待上限


# ---- WASAPI 逐会话静音（pycaw） ----

def _all_sessions() -> list:
    """枚举默认播放设备的全部渲染会话（延迟导入）。"""
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetAllSessions()


def _session_volume(session):
    """取会话的 ISimpleAudioVolume（延迟导入）。"""
    from ctypes import POINTER, cast
    from pycaw.pycaw import ISimpleAudioVolume
    return cast(session, POINTER(ISimpleAudioVolume))


def _wasapi_mute_all(exclude_pid: int) -> list:
    """静音除 exclude_pid 外的全部会话，返回我们亲手静音的会话清单。

    已被静音的（含用户手动静音）跳过不记录——恢复时只解除我们静音过的。
    """
    muted = []
    for session in _all_sessions():
        proc = getattr(session, "Process", None)
        if proc is not None and proc.pid == exclude_pid:
            continue
        try:
            vol = _session_volume(session)
            if not vol.GetMute():
                vol.SetMute(1, None)
                muted.append(session)
        except Exception:
            logger.debug("静音某个会话失败", exc_info=True)
    return muted


def _wasapi_unmute(sessions: list) -> None:
    """解除静音（幂等；会话已随进程退出时静默跳过）。"""
    for session in sessions:
        try:
            _session_volume(session).SetMute(0, None)
        except Exception:
            logger.debug("解除某个会话静音失败", exc_info=True)


# ---- SMTC 媒体会话真暂停（winrt） ----

async def _resolve(value):
    """兼容不同 pywinrt 投影：async 方法可能返回 awaitable 或已解析结果。"""
    if hasattr(value, "__await__"):
        return await value
    return value


def _smtc_pause_playing() -> list:
    """暂停所有正在播放的媒体会话，返回被暂停会话的引用清单。

    pywinrt 投影没有 SessionId 属性（其元数据早于该属性加入 SDK），
    故持有会话对象引用用于恢复；已消失的会话在恢复时按异常静默跳过。
    """
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )

    async def _run() -> list:
        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        sessions = await _resolve(manager.get_sessions())
        paused: list = []
        for s in sessions:
            info = await _resolve(s.get_playback_info())
            if info.playback_status != GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                continue
            if await _resolve(s.try_pause_async()):
                paused.append(s)
        return paused

    return asyncio.run(asyncio.wait_for(_run(), timeout=ENGAGE_TIMEOUT))


def _smtc_resume(sessions: list) -> None:
    """恢复清单内的会话：已消失的跳过；当前非暂停态（用户手动播放过）也跳过。"""
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )

    async def _run() -> None:
        for s in sessions:
            try:
                info = await _resolve(s.get_playback_info())
                if info.playback_status != GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED:
                    continue
                await _resolve(s.try_play_async())
            except Exception:
                logger.debug("恢复某个媒体会话失败（可能已退出）", exc_info=True)

    asyncio.run(asyncio.wait_for(_run(), timeout=RESUME_TIMEOUT))


class MediaPauser:
    """暂停播放/全局静音执行器：状态自持，做不做、做什么由调用方决定。

    录音开始调 `engage()`（后台线程，不阻塞录音），录音停止/程序退出调
    `resume()`（同步，与本次会话实际做过的动作严格配对）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False            # 当前会话是否中断着后台音频
        self._paused_sessions: list = []
        self._muted_sessions: list = []
        self._engage_done = threading.Event()
        self._engage_done.set()
        self._pause_available: bool | None = None
        self._mute_available: bool | None = None

    # ---- 可用性探测（导入探测，结果缓存） ----

    def pause_available(self) -> bool:
        """SMTC 媒体会话接口可用（winrt 已安装）。"""
        if self._pause_available is None:
            try:
                import winrt.windows.media.control  # noqa: F401
                self._pause_available = True
            except Exception:
                self._pause_available = False
        return self._pause_available

    def mute_available(self) -> bool:
        """WASAPI 逐会话音量接口可用（pycaw 已安装）。"""
        if self._mute_available is None:
            try:
                import pycaw.pycaw  # noqa: F401
                self._mute_available = True
            except Exception:
                self._mute_available = False
        return self._mute_available

    # ---- 动作入口 ----

    def engage(self, do_pause: bool, do_mute: bool) -> None:
        """后台线程执行所需动作；已生效中（快速连按）忽略重入。"""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._engage_done.clear()
        threading.Thread(
            target=self._engage_worker, args=(do_pause, do_mute),
            daemon=True, name="media-engage",
        ).start()

    def _engage_worker(self, do_pause: bool, do_mute: bool) -> None:
        try:
            if do_pause and self.pause_available():
                try:
                    self._paused_sessions = _smtc_pause_playing()
                    logger.info("暂停播放：已暂停 %d 个媒体会话",
                                len(self._paused_sessions))
                except Exception:
                    logger.warning("暂停播放：暂停媒体会话失败（不影响录音）",
                                   exc_info=True)
            if do_mute and self.mute_available():
                try:
                    self._muted_sessions = _wasapi_mute_all(os.getpid())
                    logger.info("全局静音：已静音 %d 个后台会话",
                                len(self._muted_sessions))
                except Exception:
                    logger.warning("全局静音：静音后台声音失败（不影响录音）",
                                   exc_info=True)
        finally:
            self._engage_done.set()

    def resume(self) -> None:
        """同步恢复本次会话动过的；无状态时为空操作。"""
        with self._lock:
            if not self._active:
                return
            self._active = False
            paused, muted = self._paused_sessions, self._muted_sessions
            self._paused_sessions, self._muted_sessions = [], []
        # 快速启停时工作线程可能未完成：等它结束再恢复，防止漏恢复
        self._engage_done.wait(ENGAGE_TIMEOUT)
        if paused and self.pause_available():
            try:
                _smtc_resume(paused)
                logger.info("暂停播放：已恢复媒体播放")
            except Exception:
                logger.warning("暂停播放：恢复媒体播放失败", exc_info=True)
        if muted and self.mute_available():
            try:
                _wasapi_unmute(muted)
                logger.info("全局静音：已解除后台静音")
            except Exception:
                logger.warning("全局静音：解除后台静音失败", exc_info=True)
