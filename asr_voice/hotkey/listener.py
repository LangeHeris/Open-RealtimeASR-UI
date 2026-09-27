"""全局热键监听：含修饰键的组合走 Windows 原生 RegisterHotKey，普通单键走 keyboard 钩子。"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import keyboard

from . import native

logger = logging.getLogger(__name__)


class HotkeyListener:
    """全局热键监听器。

    - start() 注册热键，触发时调用 on_trigger；stop() 注销
    - 含修饰键的组合（ctrl/alt/shift/win + 主键）走 Windows 原生 RegisterHotKey：
      不挂钩子、不读写修饰键状态，因此不会出现"修饰键滞留"——历史上用 keyboard
      库注册 ctrl+shift+h 时，空按一次 Shift 再敲任意键就会让系统认为 Shift 一直
      按着（复现脚本 tests/diag_hotkey_modifier_sticky.py）。原生方式的代价：命中时
      该组合被系统吃掉、不再传给前台程序；组合已被其他程序占用时注册失败并抛异常
    - 只有单个普通键的组合（backspace/delete 这类"只监听不拦截"的键）仍用 keyboard
      钩子：RegisterHotKey 会把该键从系统里吃掉，普通键不能这么干。此时库内没有
      任何 blocking 热键，修饰键状态机不会被启用，也不会滞留修饰键
    - suppress=True 在原生路径下只能做到"组合里的主键不传给前台程序"；修饰键本身
      系统不区分。普通键路径固定按"不拦截"注册（否则会把该键从系统里吞掉）
    - 环境变量 ASR_HOTKEY_BACKEND=legacy 可把全部热键退回旧的钩子实现（回滚用）
    """

    def __init__(self, hotkey: str, on_trigger: Callable[[], None],
                 suppress: bool = True,
                 on_press: Optional[Callable[[], None]] = None):
        self.hotkey = hotkey
        self.on_trigger = on_trigger
        # 长按模式：命中瞬间的回调。提供时**取代** on_trigger（开/停分流由
        # app 侧的 HoldTracker 负责，本类只负责「按下了」这一件事）。
        # 为 None 时行为与改动前逐字一致。
        self.on_press = on_press
        self.suppress = suppress
        self.backend: Optional[str] = None  # "native" | "keyboard"，未注册时为 None
        self._handle: Optional[object] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._handle is not None:
                logger.warning("热键已注册，忽略重复 start")
                return
            spec = native.parse_hotkey(self.hotkey) if native.available() else None
            if spec is not None:
                self._start_native(spec)
            else:
                self._start_keyboard()

    def stop(self) -> None:
        with self._lock:
            if self._handle is None:
                return
            try:
                if self.backend == "native":
                    native.unregister(self._handle)
                else:
                    keyboard.remove_hotkey(self._handle)
            except Exception:
                logger.exception("注销热键失败")
            self._handle = None
            self.backend = None
            logger.info("热键已注销")

    # ---- 两种后端 ----

    def _start_native(self, spec) -> None:
        mods, vk = spec
        if not self.suppress:
            logger.warning(
                "原生热键无法做到「只监听不拦截」，%s 命中时不会传给前台程序", self.hotkey)
        try:
            self._handle = native.register(mods, vk, self._on_hotkey, self.hotkey)
        except Exception as exc:
            logger.error("注册热键失败（Windows 原生）：%s", exc)
            raise
        self.backend = "native"
        logger.info("热键已注册（Windows 原生）：%s", self.hotkey)

    def _start_keyboard(self) -> None:
        if native.available() and native.has_modifier(self.hotkey):
            # 含修饰键却没能走原生（多步组合、键名不在表内等）：提醒仍有滞留风险
            logger.warning(
                "热键 %s 含修饰键但无法走 Windows 原生（多步组合或不支持的键名），"
                "已回退 keyboard 钩子：修饰键可能被接管并滞留", self.hotkey)
        suppress = self.suppress
        if not native.has_modifier(self.hotkey) and suppress:
            logger.warning(
                "普通键 %s 不支持拦截语义，已按「只监听不拦截」注册", self.hotkey)
            suppress = False
        try:
            self._handle = keyboard.add_hotkey(
                self.hotkey, self._on_hotkey, suppress=suppress)
        except Exception as exc:
            logger.error("注册热键失败（keyboard 钩子）：%s", exc)
            raise
        self.backend = "keyboard"
        logger.info("热键已注册（keyboard 钩子，只监听）：%s", self.hotkey)

    def _on_hotkey(self) -> None:
        callback = self.on_press or self.on_trigger
        try:
            callback()
        except Exception:
            logger.exception("热键回调异常")
