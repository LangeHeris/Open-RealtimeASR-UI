"""文本注入。把识别到的文字写入当前焦点窗口。

方案：
- clipboard_paste（默认）：写入剪贴板 + 模拟 Ctrl+V。结果是否留在剪贴板
  由 keep_clipboard 决定：开=识别结果留在剪贴板（方便再粘，占用剪贴板）；
  关=粘贴后恢复剪贴板原内容（不占用）
- simulate_keys：逐字模拟按键（速度慢，仅作备选）

实时识别时支持中间结果替换：新中间结果是旧结果的前缀扩展时只增量追加
新增字符（不整段重打，降低逐字同步上屏的闪烁）；前缀不匹配（ASR 回头
修正）才整段退格重写。最终结果确认后不再删除。

线程模型：所有注入操作经内部队列由专用 worker 线程串行执行，调用方立即
返回。注入含 30-300ms 的 sleep 节流（目标程序就绪缓冲 / 退格防丢键 /
剪贴板恢复等待），若在 Qt 主线程同步执行，每次上屏 + LLM 修正替换会
冻结事件循环 0.5-1 秒（悬浮条动画停、菜单点击无响应）。SendInput 与
pyperclip 均线程安全；keyboard 库的热键监听在其内部 listener 线程，
与注入线程互不干扰。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import queue
import threading
import time
from typing import Callable, Optional

import keyboard
import pyperclip

logger = logging.getLogger(__name__)


def _process_name_by_pid(pid: int) -> str:
    """PID -> 进程名（如 WeChat.exe），失败返回空串。

    主路径用 QueryFullProcessImageNameW 直接查询（单次 API 调用，
    微秒级）；微信检测/诊断日志每次注入都要调用，原来每次 spawn
    tasklist 子进程（50-300ms）是逐字同步高频注入时的卡顿源。
    受保护/高权限进程 OpenProcess 会被拒，此时回退 tasklist（低频）。
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (
            ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD,
        )
        kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = (
            ctypes.c_void_p, ctypes.wintypes.DWORD,
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.wintypes.DWORD),
        )
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        # PROCESS_QUERY_LIMITED_INFORMATION：跨权限查询受限信息即可
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    name = buf.value.rsplit("\\", 1)[-1]
                    if name:
                        return name
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass
    # 回退：高权限/受保护进程拿不到句柄时用 tasklist（仅失败路径）
    try:
        import subprocess
        # CREATE_NO_WINDOW：避免弹出黑色控制台窗口抢走焦点
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=3,
            creationflags=0x08000000,
        ).stdout
        return out.strip().split('","')[-1].strip('"')
    except Exception:
        return ""


def _foreground_window_desc() -> str:
    """诊断辅助：当前前台窗口描述（hwnd + 标题）。注入失败排查用。"""
    try:
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        pid = ctypes.wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name_by_pid(pid.value) if pid.value else ""
        return f"{name or '?'}({buf.value[:30]!r})"
    except Exception:
        return "?"


def _foreground_pid_name() -> str:
    """当前前台窗口进程名（如 WeChat.exe），失败返回空串。"""
    try:
        u32 = ctypes.windll.user32
        pid = ctypes.wintypes.DWORD()
        u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(pid))
        if not pid.value:
            return ""
        return _process_name_by_pid(pid.value)
    except Exception:
        return ""


def _pid_by_hwnd(hwnd: int) -> int:
    """HWND -> 所属进程 PID，失败返回 0。"""
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _token_integrity_level(token) -> Optional[int]:
    """进程令牌的完整性级别（低 0x1000 / 中 0x2000 / 高 0x3000），失败 None。"""
    advapi32 = ctypes.windll.advapi32
    TokenIntegrityLevel = 25
    size = ctypes.wintypes.DWORD()
    # 首调以缓冲区不足失败，借此取回所需缓冲区大小
    advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0,
                                 ctypes.byref(size))
    if size.value < ctypes.sizeof(ctypes.c_void_p):
        return None
    buf = (ctypes.c_byte * size.value)()
    if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf,
                                        size, ctypes.byref(size)):
        return None
    # TOKEN_MANDATORY_LABEL 首成员 Label.Sid 是指针
    sid = ctypes.c_void_p.from_buffer(buf).value
    if not sid:
        return None
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.wintypes.DWORD)
    advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.wintypes.DWORD)
    advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, ctypes.wintypes.DWORD)
    count = advapi32.GetSidSubAuthorityCount(sid).contents.value
    if count < 1:
        return None
    # 完整性级别是 SID 最后一个 SubAuthority
    return advapi32.GetSidSubAuthority(sid, count - 1).contents.value


def _process_integrity_level(pid: int) -> Optional[int]:
    """PID -> 该进程的完整性级别；失败（受保护进程、权限不足等）返回 None。"""
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        token = ctypes.wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, 0x0008,
                                         ctypes.byref(token)):  # TOKEN_QUERY
            return None
        try:
            return _token_integrity_level(token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


def _current_integrity_level() -> Optional[int]:
    """本进程完整性级别（与前台目标比较用）。"""
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    # GetCurrentProcess 返回伪句柄 -1：不声明 restype 时 ctypes 按 32 位
    # c_int 截断再传参，64 位下句柄失效、OpenProcessToken 必败——权限差检测
    # 恒 None、UIPI 警告从未响过（2026-09-10 实测 own=None/fg=8192 定因）
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = (
        ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL
    token = ctypes.wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,
                                     ctypes.byref(token)):
        return None
    try:
        return _token_integrity_level(token)
    finally:
        kernel32.CloseHandle(token)


_WECHAT_PROCESS_HINTS = ("wechat", "weixin", "wxwork", "微信")

# 终端类进程名：Ctrl+C 在其中是中断信号（会杀掉正在跑的命令），
# 语音命令的"读选中文本"（模拟 Ctrl+C）绝不能发往这些进程
_TERMINAL_PROCESS_HINTS = (
    "windowsterminal", "cmd.exe", "powershell", "pwsh", "conhost", "mintty",
)


def _is_wechat_foreground() -> bool:
    """判断当前前台窗口是否微信系（WeChat/Weixin/WXWork）。

    微信自绘输入框对高频 Unicode 键击（simulate_keys 每字 5ms）会丢字，
    检测到后 _write 自动改走剪贴板粘贴，保证整段完整上屏。
    """
    name = _foreground_pid_name().lower()
    return any(h in name for h in _WECHAT_PROCESS_HINTS)


def _send_unicode_text(text: str, delay: float = 0.0) -> None:
    """通过 Windows SendInput + KEYEVENTF_UNICODE 直接发送 Unicode 字符。

    相比 keyboard.write 用 Alt+小键盘 方式，可靠性大幅提升：
    - 不会触发目标程序的 Alt 菜单快捷键（首字丢失的根因）
    - 不依赖 Num Lock 状态
    - 中英文/标点同等对待，首字与后续字行为一致

    delay：每字之间间隔（秒）。0 = 最快。

    重要：INPUT 是 union（ki/mi/hi 三选一），64 位下最大成员 MOUSEINPUT
    占 32 字节，sizeof(INPUT) 必须 = 40 字节（type 4 + padding 4 + 32）。
    之前只用 KEYBDINPUT 凑出的 28 字节结构体会让 SendInput 读到错位数据，
    所有事件被静默丢弃——文字进不去目标程序。
    """
    if not text:
        return

    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.wintypes.LONG),
            ("dy", ctypes.wintypes.LONG),
            ("mouseData", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.wintypes.DWORD),
            ("wParamL", ctypes.wintypes.WORD),
            ("wParamH", ctypes.wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("_u",)
        _fields_ = [
            ("type", ctypes.wintypes.DWORD),
            ("_u", _INPUT_UNION),
        ]

    # 校验大小：64 位 Windows 上 INPUT 必须为 40 字节
    expected_64 = 40
    expected_32 = 28
    actual = ctypes.sizeof(INPUT)
    if actual not in (expected_64, expected_32):
        raise RuntimeError(
            f"INPUT 结构体大小异常: {actual}, 期望 32 位 {expected_32} / 64 位 {expected_64}"
        )

    user32 = ctypes.windll.user32
    sizeof_input = ctypes.sizeof(INPUT)

    for i, ch in enumerate(text):
        if delay > 0 and i > 0:
            time.sleep(delay)
        code = ord(ch)
        ki_down = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
        ki_up = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        # INPUT 用了 _anonymous_=("_u",)，union 成员可直接作为 INPUT 字段构造
        inp_down = INPUT(type=INPUT_KEYBOARD, ki=ki_down)
        inp_up = INPUT(type=INPUT_KEYBOARD, ki=ki_up)
        arr = (INPUT * 2)(inp_down, inp_up)
        sent = user32.SendInput(2, arr, sizeof_input)
        if sent != 2:
            # 静默失败排查：返回 0 是最常见症状
            err = ctypes.get_last_error()
            logger.warning(
                "SendInput 投递失败: 期望 2 事件, 实际 %d, last_error=%d (字符 '%s' U+%04X)",
                sent, err, ch, code,
            )


class TextInjector:
    """文本注入器。

    所有注入操作（inject/replace/cancel_intermediate/reset）经内部队列
    由专用 worker 线程串行执行，调用方立即返回——注入节流的 sleep 若在
    Qt 主线程同步执行会冻结事件循环（详见模块 docstring）。
    """

    def __init__(
        self,
        method: str = "clipboard_paste",
        paste_delay_ms: int = 50,
        keep_clipboard: bool = True,
        game_mode: bool = False,
    ):
        self.method = method
        # 容错：配置里可能是字符串数字（如 '50'），统一转数值
        try:
            self.paste_delay = int(float(paste_delay_ms)) / 1000.0
        except (TypeError, ValueError):
            self.paste_delay = 0.05
        # True=识别结果保留在剪贴板（粘贴后不恢复原内容，用户可随时 Ctrl+V 再粘贴）
        # False=粘贴后恢复剪贴板原内容（不占用剪贴板）
        self.keep_clipboard = bool(keep_clipboard)
        # 游戏模式：强制剪贴板粘贴注入（不降级逐字、粘贴后不恢复剪贴板），
        # 用于逐字输入无效的游戏等自绘输入框（右键菜单切换，配置持久化）
        self.game_mode = bool(game_mode)
        self._last_len = 0    # 上次注入的中间结果字符数（仅注入线程读写）
        self._last_text = ""  # 上次注入的中间结果原文（用于增量追加判断）
        # read_selection 时保存的用户原剪贴板内容（同注入线程读写；
        # AI 命令粘贴完成后据此恢复，配合 keep_clipboard=False 语义）
        self.selection_prev_clip: str | None = None
        # 注入队列 + 专用线程：见类 docstring
        self._jobs: "queue.Queue" = queue.Queue()
        threading.Thread(
            target=self._worker_loop, daemon=True, name="inject-worker"
        ).start()

    # ---- 线程模型 ----

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            try:
                job()
            except Exception:
                logger.exception("注入任务执行失败")

    def _submit(self, job: Callable[[], None]) -> None:
        self._jobs.put(job)

    @staticmethod
    def _run_done_callback(done_callback: Optional[Callable[[], None]]) -> None:
        if done_callback is None:
            return
        try:
            done_callback()
        except Exception:
            logger.exception("注入完成回调执行失败")

    def cancel_pending(self) -> None:
        """丢弃队列中尚未开始执行的注入任务（会话打断时防残留上屏）。"""
        dropped = 0
        while True:
            try:
                self._jobs.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            logger.info("已丢弃 %d 个排队中的注入任务", dropped)

    # ---- 注入操作（均排队执行） ----

    def inject(self, text: str, is_final: bool = True,
               done_callback: Optional[Callable[[], None]] = None) -> None:
        """注入文本（提交到注入线程，立即返回）。

        - is_final=True：注入最终结果，之后不再删除。
        - is_final=False：注入中间结果，记录长度，下次注入会先删除。
        - done_callback：注入完成后在注入线程回调（如记录上屏时前台窗口）。
        """
        if not text:
            return

        def _job():
            prev_len = self._last_len
            prev_text = self._last_text
            # 与上次中间结果逐字相同：跳过退格重写，避免逐字同步时
            # 整段删除重打的一次闪烁（ASR 偶发重发相同 slice）
            if not is_final and prev_text == text:
                self._run_done_callback(done_callback)
                return
            # 增量追加：新中间结果是旧中间结果的前缀扩展时，只追加新增字符，
            # 不整段退格重打——退格+重写是逐字同步上屏闪烁/卡顿的主要来源。
            # 前缀不匹配（ASR 回头修正了前面字）才回退到整段替换。
            incremental = (
                not is_final
                and bool(prev_text)
                and text.startswith(prev_text)
                and len(text) > len(prev_text)
            )
            if incremental:
                # 只追加本次多出来的新字符，光标自动跟在末尾。
                # 保留剪贴写入用完整累积文本（只传增量会把剪贴板
                # 留成残缺片段，_write 按 clipboard_text 写）
                self._write(text[len(prev_text):], clipboard_text=text)
            else:
                if prev_len > 0:
                    self._backspace(prev_len)
                self._write(text)

            if is_final:
                self._last_len = 0
                self._last_text = ""
            else:
                self._last_len = len(text)
                self._last_text = text

            self._run_done_callback(done_callback)

        self._submit(_job)

    def replace(self, delete_count: int, text: str,
                done_callback: Optional[Callable[[], None]] = None) -> None:
        """删除 delete_count 个字符后写入 text（LLM 修正替换用）。

        退格 + 重写合并为单个任务原子执行，避免两句间被其他注入插队。
        不改动 _last_len（与旧 _backspace+_write 直调行为一致）。
        """
        def _job():
            if delete_count > 0:
                self._backspace(delete_count)
            if text:
                self._write(text)
            self._run_done_callback(done_callback)

        self._submit(_job)

    def cancel_intermediate(self) -> None:
        """放弃当前中间结果（删除已注入的中间文本）。排队执行。"""
        def _job():
            if self._last_len > 0:
                self._backspace(self._last_len)
                self._last_len = 0
                self._last_text = ""

        self._submit(_job)

    def send_key(self, key: str) -> None:
        """模拟按键（如 enter，语音命令"发送"用）。排队到注入线程。"""
        self._submit(lambda: keyboard.send(key))

    def read_selection(
        self,
        done_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> None:
        """读取前台窗口当前选中的文字，经 done_callback(sel) 回传。

        排队到注入线程串行执行（与注入互不插队）。无选中/不支持返回
        None。哨兵法防"复制失败读到旧剪贴板"：先写唯一随机串再 Ctrl+C，
        读回仍是哨兵 = 复制没发生（无选中或目标不支持），此时恢复用户
        原剪贴板内容，不留垃圾哨兵串。有选中则不恢复（AI 链路随后要
        写处理结果进剪贴板，中间恢复无意义）。

        终端类前台进程直接放弃（Ctrl+C 是中断信号，见
        _TERMINAL_PROCESS_HINTS），不碰剪贴板。
        """
        def _job():
            sel: Optional[str] = None
            try:
                name = _foreground_pid_name().lower()
                if any(t in name for t in _TERMINAL_PROCESS_HINTS):
                    logger.info("前台是终端类进程，跳过读取选中文本")
                else:
                    try:
                        prev = pyperclip.paste()
                    except Exception:
                        prev = None  # 剪贴板为非文本（图片等），无法恢复
                    # 暂存给 AI 命令链路（同注入线程），粘贴完成后按需恢复
                    self.selection_prev_clip = prev
                    sentinel = f"__asr_sel_{time.time():.6f}__"
                    pyperclip.copy(sentinel)
                    keyboard.send("ctrl+c")
                    time.sleep(0.12)  # 等目标程序完成复制
                    got = pyperclip.paste()
                    if got and got != sentinel:
                        sel = got
                    elif prev is not None:
                        # 无选中/复制失败：恢复原剪贴板，不留哨兵垃圾
                        try:
                            pyperclip.copy(prev)
                        except Exception:
                            logger.debug("恢复剪贴板失败", exc_info=True)
            except Exception:
                logger.exception("读取选中文本失败")
                sel = None
            if done_callback:
                try:
                    done_callback(sel)
                except Exception:
                    logger.exception("读取选中回调执行失败")

        self._submit(_job)

    def reset(self) -> None:
        """重置状态（不删除已注入的最终文本）。排队执行，任意线程可调。"""
        self._submit(lambda: (
            setattr(self, "_last_len", 0),
            setattr(self, "_last_text", ""),
        ))

    @staticmethod
    def _clipboard_restorable() -> bool:
        """剪贴板当前内容是否可以安全保存并还原。

        仅含纯文本格式（CF_TEXT/CF_OEMTEXT/CF_UNICODETEXT/CF_LOCALE）时可用
        剪贴板注入：粘贴后能用 pyperclip 完整还原原内容。
        含图片（CF_DIB）、文件（CF_HDROP）、富文本（RTF/HTML 等注册格式）
        时 pyperclip 无法还原，返回 False——此时不动剪贴板，降级逐字输入，
        避免覆盖用户复制的图片/文件。
        """
        import ctypes

        u32 = ctypes.windll.user32
        # 纯文本族格式：1=CF_TEXT 7=CF_OEMTEXT 13=CF_UNICODETEXT 16=CF_LOCALE
        # 注册格式（RTF/HTML Format 等）编号 >= 0xC000，视为不可还原
        restorable_formats = {1, 7, 13, 16}
        if not u32.OpenClipboard(None):
            # 剪贴板被其他程序锁住：不硬抢，降级逐字输入
            return False
        try:
            fmt = 0
            while True:
                fmt = u32.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                if fmt not in restorable_formats:
                    return False
            return True
        finally:
            u32.CloseClipboard()

    @staticmethod
    def _clipboard_has_text() -> bool:
        """剪贴板是否含文本格式（CF_TEXT/CF_OEMTEXT/CF_UNICODETEXT）。

        keep_clipboard 写结果的判定：只要剪贴板里有文本（含浏览器复制
        的富文本——HTML Format 只是附带格式，本质仍是文本）就允许覆盖；
        仅图片/文件（无任何文本格式）时不覆盖，保护用户复制的内容。
        """
        import ctypes

        u32 = ctypes.windll.user32
        text_formats = {1, 7, 13}  # CF_TEXT / CF_OEMTEXT / CF_UNICODETEXT
        if not u32.OpenClipboard(None):
            return False  # 被锁住：不硬抢，跳过写入
        try:
            fmt = 0
            while True:
                fmt = u32.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                if fmt in text_formats:
                    return True
            return False
        finally:
            u32.CloseClipboard()

    def _write(self, text: str, clipboard_text: Optional[str] = None) -> None:
        # 游戏模式：无条件「写剪贴板 -> Ctrl+V」。不做可还原性检查、不降级
        # 逐字、粘贴后不恢复剪贴板（语义等同保留剪贴=开）：游戏等自绘输入
        # 框不读无扫描码的合成键击，逐字路径必然失效，粘贴是唯一可行路径；
        # 写剪贴板失败时不降级 keyboard.write（游戏里同样无效），仅记日志
        if self.game_mode:
            logger.info(
                "注入开始[game_mode] %d字 目标=%s", len(text),
                _foreground_window_desc(),
            )
            try:
                pyperclip.copy(text)
            except Exception:
                logger.exception("游戏模式写入剪贴板失败")
                return
            time.sleep(self.paste_delay)
            # 发送时刻再记一次前台：Ctrl+V 比「注入开始」晚 paste_delay 才发，
            # 这期间焦点可能已变（如点了悬浮条），粘错窗口时日志才能看出来。
            # 权限差同刻取证：UIPI 丢弃合成输入是无痕迹的（日志只有「注入完成」），
            # True=目标高权限必被丢、None=查不到令牌同样可疑（2026-09-10 星际战甲
            # 实测前台正确却不出字，当时无此字段无法定因）
            logger.info(
                "游戏模式发送 Ctrl+V %d字 前台=%s 权限差=%s", len(text),
                _foreground_window_desc(), self.foreground_elevation_gap(),
            )
            # 模拟手动按键：游戏常按帧轮询键状态（GetAsyncKeyState），零时长
            # 的 down+up 会在两帧轮询之间被漏掉；IDE 等事件驱动程序收
            # WM_KEYDOWN 不受影响——同为前台正确、Qoder 出字而星际战甲
            # 不出字的差异解释（2026-09-10 实测）。改为按下-保持-释放的
            # 真实按键节奏，轮询型输入管道才能捕到
            keyboard.press("ctrl")
            time.sleep(0.05)
            keyboard.press("v")
            time.sleep(0.05)
            keyboard.release("v")
            time.sleep(0.02)
            keyboard.release("ctrl")
            logger.info("注入完成[game_mode] %d字", len(text))
            # 连续注入竞态防护（同保留剪贴=开分支）：队列还有任务时等一拍
            if self._jobs.qsize() > 0:
                time.sleep(max(self.paste_delay, 0.25))
            return
        # 「剪贴板能否安全还原」只在保留剪贴=关（粘贴后需恢复原内容）时
        # 才参与路由：保留剪贴=开本来就是直接覆盖写入、无需还原，
        # 不该因为剪贴板里有富文本/图片/文件而降级成逐字输入
        # （修复：默认配置下复制过浏览器文字后全部注入被静默降级）。
        restorable = (
            self._clipboard_restorable() if not self.keep_clipboard else True
        )
        # 微信系前台：自绘输入框对高频 Unicode 键击丢字（每字 5ms 连发
        # 必吞），即使配置逐字输入也强制改走剪贴板粘贴，整段完整上屏
        wechat = _is_wechat_foreground()
        if (self.method == "simulate_keys" or not restorable) and not wechat:
            # SendInput + KEYEVENTF_UNICODE 直发：根除 keyboard.write 的
            # Alt+小键盘 方案触发目标程序菜单快捷键导致首字丢失的问题。
            if text:
                time.sleep(0.03)  # 给目标窗口 30ms 就绪缓冲
                logger.info(
                    "注入开始[simulate_keys] %d字 目标=%s", len(text),
                    _foreground_window_desc(),
                )
                # 首字符单独发送 + 50ms 间隔：微信等程序在注入启动瞬间
                # （焦点/输入法状态切换）会吞掉第一组键击，首字连发必丢；
                # 单发首字等目标就绪后再发剩余，可恢复
                _send_unicode_text(text[0])
                time.sleep(0.05)
                if len(text) > 1:
                    _send_unicode_text(text[1:], delay=0.005)
                logger.info("注入完成[simulate_keys] %d字", len(text))
            # keep_clipboard：逐字输入（含剪贴板粘贴模式降级逐字的路径）
            # 时结果也放进剪贴板（只写不粘），保证"说过的话可随时 Ctrl+V"。
            # 含文本格式即可写（富文本也算文本）；仅图片/文件时不覆盖，
            # 保护用户已复制的内容。增量注入时 clipboard_text 传完整累积
            # 文本，避免剪贴板只留下最后一段增量片段（BUG 修复）
            if self.keep_clipboard and self._clipboard_has_text():
                try:
                    pyperclip.copy(clipboard_text or text)
                except Exception:
                    logger.debug("结果写入剪贴板失败", exc_info=True)
        elif self.keep_clipboard:
            # 识别结果保留在剪贴板：直接写入 -> 粘贴，粘贴后不恢复原内容。
            # 比保存/恢复方案更快（省 250ms 恢复等待），也无恢复竞态；
            # 用户随时可 Ctrl+V 重复粘贴本次识别结果。
            logger.info(
                "注入开始[clipboard_paste] %d字 目标=%s", len(text),
                _foreground_window_desc(),
            )
            try:
                pyperclip.copy(text)
            except Exception:
                logger.exception("写入剪贴板失败，降级为逐字输入")
                keyboard.write(text, delay=0.01)
                return
            time.sleep(self.paste_delay)
            keyboard.send("ctrl+v")
            logger.info("注入完成[clipboard_paste] %d字", len(text))
            # 连续注入竞态防护：ctrl+v 是异步消息，若队列里还有后续注入
            # （LLM 多句替换、紧挨的 final），慢目标程序可能还没读完本次
            # 剪贴板内容，下一句的 copy 会覆盖它，导致粘出旧文本/空文本。
            # 有排队任务时等一拍再放行；单句注入不排队则零开销（保快）
            if self._jobs.qsize() > 0:
                time.sleep(max(self.paste_delay, 0.25))
        else:
            # 保存剪贴板原内容：粘贴完成后恢复，不占用用户剪贴板，
            # 避免每次听写都覆盖用户已复制的内容
            logger.info(
                "注入开始[clipboard_paste] %d字 目标=%s", len(text),
                _foreground_window_desc(),
            )
            try:
                prev = pyperclip.paste()
            except Exception:
                prev = None  # 剪贴板是非文本内容（如图片）时读不出，无法恢复
            try:
                pyperclip.copy(text)
            except Exception:
                logger.exception("写入剪贴板失败，降级为逐字输入")
                keyboard.write(text, delay=0.01)
                return
            time.sleep(self.paste_delay)
            keyboard.send("ctrl+v")
            logger.info("注入完成[clipboard_paste] %d字", len(text))
            if prev is not None:
                # 粘贴后至少等 250ms 再恢复剪贴板：ctrl+v 是异步消息，
                # 慢程序（Word/浏览器/远程桌面）50ms 内可能尚未读取剪贴板，
                # 提前恢复会粘出旧内容或空文本（吞字）；多句连续注入时
                # 下一句的写入也会与本句的读取竞态
                time.sleep(max(self.paste_delay, 0.25))
                try:
                    pyperclip.copy(prev)
                except Exception:
                    logger.debug("恢复剪贴板原内容失败", exc_info=True)

    @staticmethod
    def current_window_hwnd() -> int:
        """当前前台窗口句柄（LLM 修正替换前校验焦点是否仍在上屏窗口）。"""
        try:
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return 0

    def foreground_elevation_gap(self) -> Optional[bool]:
        """前台窗口进程是否以更高完整性级别运行（管理员权限差检测）。

        True=目标高权限（如管理员运行的游戏），本进程的 SendInput 合成输入
        会被 Windows UIPI 静默丢弃；False=无权限差；None=无法判定
        （无前台窗口 / 受保护进程查不到令牌等）。
        """
        try:
            hwnd = self.current_window_hwnd()
            if not hwnd:
                return None
            pid = _pid_by_hwnd(hwnd)
            if not pid:
                return None
            target_il = _process_integrity_level(pid)
            own_il = _current_integrity_level()
            if target_il is None or own_il is None:
                return None
            return target_il > own_il
        except Exception:
            return None

    def _backspace(self, count: int) -> None:
        # 退格加小间隔：keyboard.send 连发无间隔时，中文输入法/部分程序
        # 会丢键（尤其连按 5+ 次），导致删不干净原文——LLM 修正替换时
        # 残留原文 + 重写修正版 = 看起来像"重复文本"。每 3 次让出 10ms。
        for i in range(count):
            keyboard.send("backspace")
            if i % 3 == 2:
                time.sleep(0.010)
