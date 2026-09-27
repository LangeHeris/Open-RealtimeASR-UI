"""Windows 原生全局热键（RegisterHotKey）。

为什么不用 keyboard 库的热键
---------------------------
keyboard 库（0.13.5）的 add_hotkey 走低级键盘钩子：它把热键组合里出现的修饰键
（Ctrl/Shift/Alt/Win）计入 _listener.filtered_modifiers，从而接管这些键的物理按下
与抬起，并在"空按一次修饰键"之后补发一个没有配对的合成按下——系统于是认为修饰键
一直被按住（Shift 滞留：后续输入变大写、Ctrl+V 变 Ctrl+Shift+V，再按一次该修饰键
才恢复；Ctrl 同理）。真机复现见 tests/diag_hotkey_modifier_sticky.py。

RegisterHotKey 由系统实现，不挂钩子、不读写任何键状态，命中时只投递一条 WM_HOTKEY：

- 修饰键不会被接管，不存在滞留；
- 含 Win 键的组合不再需要管理员权限（旧实现要靠钩子+提权才能捕获）；
- 组合已被别的程序注册时会失败（ERROR_HOTKEY_ALREADY_REGISTERED），能明确报错，
  而不是静默失效。

本模块只负责"原生可用"的那部分。只有单个普通键的组合（backspace/delete 这类
"只监听不拦截"的键）不能走原生——RegisterHotKey 会把该键从系统里吃掉——继续由
listener.py 走 keyboard 钩子；此时库内没有任何 blocking 热键，修饰键状态机不会被
启用，也不会滞留修饰键（见 listener.py 的说明）。

实现要点：RegisterHotKey 需要一个窗口来接收 WM_HOTKEY，且注册/注销与消息循环
最好在同一线程，因此这里起一个常驻线程创建隐藏窗口（HWND_MESSAGE）并跑消息循环，
所有注册请求都投递到该线程执行。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from typing import Callable, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # 按住不放不重复触发（与旧 add_hotkey 行为一致）

WM_HOTKEY = 0x0312
_WM_APP_SYNC = 0x8000 + 1  # WM_APP+1：唤醒热键线程执行注册/注销请求

_ERROR_CLASS_ALREADY_EXISTS = 1410
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_HWND_MESSAGE = -3  # HWND_MESSAGE：消息专用窗口，不可见、不进 Alt+Tab
_MAPVK_VSC_TO_VK_EX = 3

_ENV_BACKEND = "ASR_HOTKEY_BACKEND"

# 修饰键别名 -> RegisterHotKey 的 MOD_*。左右两侧同名（系统不区分左右修饰键）。
_MODIFIER_FLAGS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "left ctrl": MOD_CONTROL, "right ctrl": MOD_CONTROL,
    "shift": MOD_SHIFT, "left shift": MOD_SHIFT, "right shift": MOD_SHIFT,
    "alt": MOD_ALT, "left alt": MOD_ALT, "right alt": MOD_ALT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN,
    "left windows": MOD_WIN, "right windows": MOD_WIN,
}

# 键名 -> 虚拟键码（表外的名称走 _vk_from_scan_code 兜底）
_KEY_TO_VK = {
    "space": 0x20, "spacebar": 0x20,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "pgup": 0x21, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "caps lock": 0x14, "num lock": 0x90, "scroll lock": 0x91,
    "print screen": 0x2C, "pause": 0x13, "esc": 0x1B, "escape": 0x1B,
    "menu": 0x5D,
    "comma": 0xBC, ",": 0xBC, "period": 0xBE, ".": 0xBE, "slash": 0xBF, "/": 0xBF,
    "semicolon": 0xBA, ";": 0xBA, "quote": 0xDE, "'": 0xDE,
    "left bracket": 0xDB, "[": 0xDB, "right bracket": 0xDD, "]": 0xDD,
    "backslash": 0xDC, "\\": 0xDC,
    "minus": 0xBD, "-": 0xBD, "equals": 0xBB, "=": 0xBB, "grave": 0xC0,
}
_KEY_TO_VK[chr(96)] = 0xC0  # 反引号键：避免源码里出现裸反引号

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),  # ctypes.wintypes 没有 HCURSOR
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


_API_DECLARED = False


def _declare_win32_api(user32, kernel32) -> None:
    """显式声明参数/返回类型。

    ctypes 默认按 C int 传参，64 位下 HWND/HINSTANCE 这类句柄会报
    "int too long to convert"，所以每个用到的函数都要写清楚。
    """
    global _API_DECLARED
    if _API_DECLARED:
        return
    user32.RegisterClassExW.argtypes = (ctypes.POINTER(_WNDCLASSEXW),)
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p, wintypes.HINSTANCE, ctypes.c_void_p)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterHotKey.argtypes = (
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.PostThreadMessageW.argtypes = (
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _API_DECLARED = True


def backend_preference() -> str:
    """后端偏好：native（默认）| legacy。ASR_HOTKEY_BACKEND=legacy 可一键回滚。"""
    value = os.environ.get(_ENV_BACKEND, "native").strip().lower()
    return value if value in ("native", "legacy") else "native"


def available() -> bool:
    """原生热键是否可用（Windows 且未被环境变量强制回滚）。"""
    return IS_WINDOWS and backend_preference() == "native"


def _vk_from_scan_code(name: str) -> Optional[int]:
    """键名 -> 扫描码 -> 虚拟键码（借用 keyboard 库的键名表兜底）。"""
    try:
        import keyboard

        user32 = ctypes.windll.user32
        for scan in keyboard.key_to_scan_codes(name):
            if scan < 0:
                return -scan  # 库内用负数表示"这个值本身就是 VK"
            vk = user32.MapVirtualKeyW(int(scan) & 0xFF, _MAPVK_VSC_TO_VK_EX)
            if vk:
                return int(vk)
    except Exception:
        logger.debug("键名 %s 无法换算为虚拟键码", name, exc_info=True)
    return None


def _key_to_vk(name: str) -> Optional[int]:
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _KEY_TO_VK:
        return _KEY_TO_VK[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1  # VK_F1 = 0x70
    return _vk_from_scan_code(key)


def has_modifier(hotkey: str) -> bool:
    """组合里是否含修饰键（决定能否走原生 / 回退钩子时是否有滞留风险）。"""
    for part in (p.strip().lower() for p in (hotkey or "").split("+")):
        if part in _MODIFIER_FLAGS or part == "alt gr":
            return True
    return False


def parse_hotkey(hotkey: str) -> Optional[Tuple[int, int]]:
    """把 keyboard 格式的热键串解析成 RegisterHotKey 用的 (mods, vk)。

    返回 None 表示"不适合/不能走原生"：空串、没有修饰键、多个主键（多步组合）、
    键名不认识。调用方据此回退到 keyboard 钩子路径。
    """
    text = (hotkey or "").strip().lower()
    if not text:
        return None
    mods = 0
    main_key: Optional[str] = None
    for part in (p.strip() for p in text.split("+")):
        if not part:
            continue
        if part in _MODIFIER_FLAGS:
            mods |= _MODIFIER_FLAGS[part]
        elif main_key is None:
            main_key = part
        else:
            return None  # 多个主键：RegisterHotKey 只支持"修饰键+单键"
    if not mods or main_key is None or main_key in _MODIFIER_FLAGS:
        return None
    vk = _key_to_vk(main_key)
    if not vk:
        return None
    return mods, int(vk)


class _HotkeyService:
    """隐藏窗口 + 消息循环的常驻线程；所有注册/注销都在该线程执行。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._hwnd = 0
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._requests: list = []  # [(fn, box, done)]
        self._callbacks: Dict[int, Callable[[], None]] = {}
        self._registered: Set[int] = set()
        self._next_id = 1
        self._wndproc = None  # 必须保引用，否则回调被 GC 后崩溃
        self._class_name = "AsrVoiceHotkeyWindow_{}".format(os.getpid())

    # ---- 对外：注册 / 注销 ----

    def register(self, mods: int, vk: int, callback: Callable[[], None],
                 hotkey_text: str = "") -> int:
        self._ensure_started()

        def _do() -> int:
            user32 = ctypes.windll.user32
            hotkey_id = self._next_id
            self._next_id += 1
            if not user32.RegisterHotKey(self._hwnd, hotkey_id, mods | MOD_NOREPEAT, vk):
                error = ctypes.windll.kernel32.GetLastError()
                if error == _ERROR_HOTKEY_ALREADY_REGISTERED:
                    raise RuntimeError(
                        "热键 {} 已被其他程序占用，请在设置里换一个组合".format(
                            hotkey_text or "(未命名)"))
                raise OSError(error, "RegisterHotKey 失败：{}".format(hotkey_text or ""))
            self._callbacks[hotkey_id] = callback
            self._registered.add(hotkey_id)
            return hotkey_id

        return self._call_on_thread(_do)

    def unregister(self, hotkey_id: int) -> None:
        with self._lock:
            started = self._thread is not None and self._hwnd != 0
        if not started:
            return

        def _do() -> None:
            self._callbacks.pop(hotkey_id, None)
            self._registered.discard(hotkey_id)
            ctypes.windll.user32.UnregisterHotKey(self._hwnd, int(hotkey_id))

        self._call_on_thread(_do)

    # ---- 内部：线程与消息循环 ----

    def _ensure_started(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run, name="hotkey-native", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("热键线程启动超时")
        if self._start_error is not None:
            raise RuntimeError("热键窗口创建失败：{}".format(self._start_error))

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        try:
            _declare_win32_api(user32, kernel32)

            self._wndproc = _WNDPROC(self._on_message)
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = self._class_name
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                error = kernel32.GetLastError()
                if error != _ERROR_CLASS_ALREADY_EXISTS:
                    raise OSError(error, "RegisterClassExW 失败")
            hwnd = user32.CreateWindowExW(
                0, self._class_name, "ORI hotkey", 0, 0, 0, 0, 0,
                wintypes.HWND(_HWND_MESSAGE), None, wc.hInstance, None)
            if not hwnd:
                raise OSError(kernel32.GetLastError(), "CreateWindowExW 失败")
            self._hwnd = hwnd
            self._thread_id = kernel32.GetCurrentThreadId()
        except BaseException as exc:  # pragma: no cover - 环境相关
            self._start_error = exc
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            if msg.message == _WM_APP_SYNC:
                self._drain_requests()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        for hotkey_id in list(self._registered):  # 线程退出兜底
            user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._registered.clear()
        self._callbacks.clear()

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY:
            callback = self._callbacks.get(int(wparam))
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("热键回调异常：id=%s", wparam)
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _drain_requests(self) -> None:
        with self._lock:
            requests, self._requests = self._requests, []
        for fn, box, done in requests:
            try:
                box["result"] = fn()
            except BaseException as exc:
                box["exc"] = exc
            finally:
                done.set()

    def _call_on_thread(self, fn, timeout: float = 5.0):
        if threading.current_thread() is self._thread:
            return fn()
        box: dict = {}
        done = threading.Event()
        with self._lock:
            if not self._thread_id:
                raise RuntimeError("热键线程未就绪")
            self._requests.append((fn, box, done))
            thread_id = self._thread_id
        if not ctypes.windll.user32.PostThreadMessageW(thread_id, _WM_APP_SYNC, 0, 0):
            raise RuntimeError("唤醒热键线程失败")
        if not done.wait(timeout):
            raise RuntimeError("热键线程无响应")
        if "exc" in box:
            raise box["exc"]
        return box.get("result")


_service = _HotkeyService()


def register(mods: int, vk: int, callback: Callable[[], None],
             hotkey_text: str = "") -> int:
    """注册原生热键，返回句柄（注销时用）。失败抛异常（已被占用等）。"""
    return _service.register(mods, vk, callback, hotkey_text)


def unregister(handle: int) -> None:
    """注销原生热键（幂等）。"""
    _service.unregister(int(handle))
