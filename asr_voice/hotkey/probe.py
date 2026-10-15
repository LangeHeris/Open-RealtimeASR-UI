"""松手探针：只用 GetAsyncKeyState 的符号位判断「此刻按键是否仍按下」。

不用键盘钩子：低级钩子回调超时会被 Windows 静默摘除（表现为「长按时灵时不灵」）。
只读符号位：最低位（自上次调用以来是否按过）不可靠、会假阴性且可能被其他进程抢先消费；
符号位（此刻是否按下）可靠。轮询够用：阈值起点由 WM_HOTKEY 精确给出，终点只是采样，
20ms 松手延迟人耳不可辨。
"""

from __future__ import annotations

import ctypes
import logging
import os

from .native import backend_preference

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"


def available() -> bool:
    """探针是否可用：Windows 且热键未被强制回滚到钩子后端。"""
    return IS_WINDOWS and backend_preference() == "native"


def _get_async_key_state(vk: int) -> int:
    """调用 user32.GetAsyncKeyState，返回带符号 SHORT。测试里被 mock 替换。"""
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return int(user32.GetAsyncKeyState(int(vk)))


def is_key_down(vk: int) -> bool | None:
    """此刻按键是否按下。

    返回 None 表示调用失败（调用方按「未按下」处理：宁可提前结束，不可永久按住）。
    """
    try:
        state = _get_async_key_state(int(vk))
    except Exception:
        logger.debug("GetAsyncKeyState 调用失败 vk=%s", vk, exc_info=True)
        return None
    return state < 0
