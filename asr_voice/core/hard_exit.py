"""退出路径终点：清理后直接结束进程，不依赖解释器收尾。

`ExitProcess` 系退出要为每个已加载 DLL 跑 detach 且要 loader lock，原生库
（Qt/torch/steam_api 等）任一卡住就退不掉；`TerminateProcess` 不跑 detach、
不 join 线程，是唯一不会挂的手段。日志与写盘须在调用前完成；调试设
`ORI_NO_HARD_EXIT=1` 退回普通退出；`steamworks._terminate_self` 为同手段
独立实现，两边不互相 import。
"""
from __future__ import annotations

import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)

# 退出兜底时限（秒）：宽于任何正常收尾，只在卡住时响。
EXIT_TIMEOUT_S = 15.0

# 调试逃生口：置 1 退回普通退出（重新暴露 detach 挂死）。
NO_HARD_EXIT_ENV = "ORI_NO_HARD_EXIT"

_watchdog_lock = threading.Lock()
_watchdog_armed = False
_watchdog_stop = threading.Event()


def hard_exit_disabled() -> bool:
    """是否显式关闭了硬退（仅调试用；未设置＝硬退生效）。"""
    return os.environ.get(NO_HARD_EXIT_ENV, "").strip() not in ("", "0", "false", "False", "no")


def terminate_self(rc: int = 0) -> None:
    """立即结束本进程，**不返回**。`os._exit` 仅作 TerminateProcess 失败时的兜底。"""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), ctypes.c_uint(rc))
    except Exception:
        logger.debug("TerminateProcess 不可用，退回 os._exit", exc_info=True)
    os._exit(rc)


def finish_and_terminate(rc: int = 0) -> None:
    """收尾后强制结束进程：先 flush 日志再 `terminate_self`；退出码 0 = 正常（Steam 据此区分崩溃）。"""
    if hard_exit_disabled():
        logger.info("已设置 %s，按普通方式退出（调试用）", NO_HARD_EXIT_ENV)
        return
    # 退出终点分界线：它在 = 此后进程必定消失；不在 = 上一条日志即卡点
    logger.info("收尾完成，强制结束进程（退出码 %s，不再依赖解释器收尾）", rc)
    # 先解除看门狗，避免与 flush 同瞬重复硬退
    disarm_exit_watchdog()
    try:
        logging.shutdown()
    except Exception:
        logger.debug("logging.shutdown 失败，忽略", exc_info=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:      # pragma: no cover - 流可能已关闭
            pass
    terminate_self(rc)


def arm_exit_watchdog(timeout_s: float = EXIT_TIMEOUT_S) -> bool:
    """武装退出兜底：`timeout_s` 后进程还在就硬退；幂等（退出路径会走两次）。"""
    global _watchdog_armed
    if hard_exit_disabled():
        return False
    with _watchdog_lock:
        if _watchdog_armed:
            return False
        _watchdog_armed = True
        _watchdog_stop.clear()
    threading.Thread(target=_watchdog_wait, args=(timeout_s,), daemon=True,
                     name="exit-watchdog").start()
    return True


def disarm_exit_watchdog() -> None:
    """解除退出兜底（幂等；从没武装过也是 no-op）。"""
    global _watchdog_armed
    with _watchdog_lock:
        if not _watchdog_armed:
            return
        _watchdog_armed = False
    _watchdog_stop.set()


def _watchdog_wait(timeout_s: float) -> None:
    if _watchdog_stop.wait(timeout_s):
        return
    # 超时硬退。退出码给 0：卡住不是崩溃，避免 Steam 按崩溃上报
    logger.warning("退出收尾超过 %.0f 秒仍未结束，强制结束进程（见 hard_exit 说明）",
                   timeout_s)
    finish_and_terminate(0)
