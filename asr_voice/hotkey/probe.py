"""松手探针：只用 GetAsyncKeyState 的符号位判断「此刻按键是否仍按下」。

为什么不用键盘钩子：spec §5.2 记录了完整论证——低级钩子回调超时会被 Windows
静默摘除（表现为「长按时灵时不灵」），且本仓库 hotkey/native.py 开篇就论证过
「为什么不用钩子」，并留有修饰键滞留的真机复现脚本。为一个可选功能把钩子请
回来是架构倒退。

为什么只读符号位：权威文档明确写最低位（「自上次调用以来是否按过」）不可靠、
会假阴性，且可能被其他进程抢先消费。符号位（此刻是否按下）是可靠的，正是所需。

为什么轮询够用：阈值判定的起点由 WM_HOTKEY 精确给出（零误差），终点只是采样。
20ms 摊在 600ms 上是 3%，松手侧晚 20ms 关闸门人耳不可辨。延迟只花在两个布尔
判断上，不值得为它上 100 行 Raw Input。
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
