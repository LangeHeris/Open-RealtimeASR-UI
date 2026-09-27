"""浮动状态条 UI（PySide6）：无边框置顶半透明，启停按钮 + 状态点 + 识别文字预览。

左键拖拽窗口、拖右下角缩放；右键菜单管引擎/主题/设置之类，退出与最小化走托盘。
"""

from __future__ import annotations

import json
import logging
import sys

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, Signal, QObject, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from .bar_widgets import (ConfirmButtons, SpeechBubble, draw_caret_marker,
                          elide_left, elide_right, reassert_topmost)
from .icons import (
    close_icon,
    play_icon,
    spinner_pixmap,
    stop_icon,
    voice_icon,
)
from .presets import engine_group_of, get_engines
from .theme_features import get_feature
from .theme_registry import available_themes
from .fonts import get_font
from ..core.dialog import (
    DIALOG_UI_CONNECTING,
    DIALOG_UI_LISTENING,
    DIALOG_UI_SPEAKING,
    DIALOG_UI_STATES,
    DIALOG_UI_THINKING,
    ROLE_AI,
    ROLE_USER,
)
from ..i18n import t
from ..paths import state_dir

logger = logging.getLogger(__name__)

# 窗口位置/尺寸持久化：独立状态文件，不写 config.yaml 以免触发热加载
_STATE_DIR = state_dir()
_STATE_FILE = _STATE_DIR / "state.json"


def _load_window_state() -> dict:
    """读取持久化的窗口状态。失败返回空 dict。"""
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("读取窗口状态失败", exc_info=True)
    return {}


def _save_window_state(x: int, y: int, w: int, h: int) -> None:
    """保存窗口位置和尺寸。失败仅记日志，不影响使用。"""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = _load_window_state()
        data.update({"window_x": x, "window_y": y, "window_width": w, "window_height": h})
        _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logger.debug("保存窗口状态失败", exc_info=True)

# 状态
UI_IDLE = "idle"
UI_ARMED = "armed"          # 长按已按下、未达阈值：准备中（可能被撤销，见 spec §3.2.1）
UI_LISTENING = "listening"
UI_RECOGNIZING = "recognizing"
UI_ERROR = "error"

# 默认配色（可被 config 覆盖）
DEFAULT_BG = "#1c1c20"
DEFAULT_TEXT = "#eeeeee"
DEFAULT_ACCENT = "#5696e8"

DOT_COLOR_MAP = {
    UI_IDLE: "accent_dim",
    # 长按准备中：暗红——一眼看出「红了但还没到位」，与录音红的区别是
    # 它可能被撤销（轻点丢弃），不能用正式录音色，否则红一下又消失像出错
    UI_ARMED: "#a03c3c",
    UI_LISTENING: "#e85656",
    UI_RECOGNIZING: "#e85656",  # 识别中也保持红色
    UI_ERROR: "#e8a83c",
    # 语音对话三态（spec「悬浮条」小节）：与录音红/错误琥珀/accent 全部拉开；
    # 思考与播报必须分色——同属「AI 侧」但一个在等一个在放，同色会像卡住
    DIALOG_UI_LISTENING: "#4ec9a0",   # 青绿：聆听用户
    DIALOG_UI_THINKING: "#5696e8",    # 蓝：等模型
    DIALOG_UI_SPEAKING: "#b07ce8",    # 紫：播报中
    DIALOG_UI_CONNECTING: "#5696e8",  # 连接中与思考同为 AI 侧等待色
}

# 对话四态全集（状态分流/会话文本清零判定用；单一事实源在 core.dialog，
# 变量名沿用历史私有名，模块内外引用点不动）
_DIALOG_STATES = DIALOG_UI_STATES

# 对话文本刷新间隔（毫秒）：流式回复的 delta 频率远高于显示所需，逐帧直接重排 +
# 强制重绘会把主线程占满 —— Hermes 长回复实测：气泡几千字时 Windows 直接报
# 「Python 未响应」、第二轮对话进不去。33ms ≈ 30fps，观感仍是实时，重排次数被
# 压到帧率上限（无论 provider 推多密）。
DIALOG_REFRESH_MS = 33

# ---- 语音模式文本显示 ----
# 角色色带 / 标签定义在 bar_widgets（见 ROLE_BAND_COLORS）。
# 内容一律画在气泡里：条内文本区实测约 210px，放不下两条消息 + 角色区分，
# 条只负责状态与控制（见 _draw_text 的对话态分流）。

# 按钮样式（基础占位，实际由 _apply_theme 按主题动态生成）
_TOGGLE_BTN_PLACEHOLDER = "QPushButton { background: transparent; border: none; }"

ICON_BTN_W = 38
BTN_H = 38
BTN_GAP = 4
PADDING = 10
BTN_DOT_GAP = 12             # 启停按钮 -> 状态点间距
DOT_D = 10                    # 状态点直径
DOT_TEXT_GAP = 12             # 状态点 -> 文本间距
DOT_OFFSET = ICON_BTN_W + BTN_DOT_GAP   # 状态点相对启停按钮的偏移
EXIT_BTN_W = 20                # 退出按钮宽度
EXIT_BTN_H = 20                # 退出按钮高度
RESIZE_HANDLE = 14             # 右下角调整尺寸手柄区域边长
MIN_W = 260                    # 最小宽度
MIN_H = 60                     # 非凯尔特主题最小高度：启停按钮 38px + 上下各 ~11px
                               # 呼吸空间；凯尔特主题另用 _knot_min_height()（含链带）
# 默认尺寸（与 config defaults.py 的 bar_width / bar_height 保持一致）：
# 跟随当前窗口大小调整（极简主题为正方形，不走此默认）
DEFAULT_BAR_W = 326
DEFAULT_BAR_H = 78
VOICE_SIDE = 83                # 声纹主题正方形默认边长
VOICE_MIN_SIDE = 56            # 声纹主题最小边长（波形 + ✕ 仍清晰可辨）
# 像素主题专属默认边长：取用户实际使用并确认的 70×70（persist 在
# state.json 里的 window_width/height）。像素主题不跟随 VOICE_SIDE——用户
# 的需求是「把我现在这个窗口大小设为默认」，即 70 而非通用的 83。
# 不改 VOICE_SIDE 本身，避免波及极简等其它方窗主题。
PIXEL_DEFAULT_SIDE = 70


def _rgba(hex_color: str, alpha: int) -> str:
    """#rrggbb + alpha -> #AARRGGBB 8 位 hex。Qt6 的 QColor 不认 "rgba(r,g,b,a)" 字符串。"""
    return f"#{alpha:02x}{hex_color.lstrip('#')}"


class BarSignals(QObject):
    """跨线程信号。"""

    state_changed = Signal(str)
    level_changed = Signal(float)
    text_updated = Signal(str, bool)
    error_occurred = Signal(str)
    show_requested = Signal()
    hide_requested = Signal()
    toggle_requested = Signal()
    interrupt_requested = Signal()  # 左键打断（app 丢弃本会话未上屏文字后停止）
    exit_requested = Signal()
    engine_changed = Signal(str)       # 引擎切换
    theme_changed = Signal(str)        # 主题切换（预设名）
    auto_hide_toggled = Signal(bool)   # 自动隐藏开关
    always_on_top_toggled = Signal(bool)  # 永远置顶开关（app 据此持久化）
    settings_requested = Signal()       # 打开设置对话框
    history_window_requested = Signal()  # 打开历史记录独立窗口
    font_file_changed = Signal(str)    # 字体文件切换（绝对路径，空字符串=恢复默认）
    transient_hint = Signal(str, float)  # 临时提示（文本，秒数）
    dialog_turn = Signal(int, str, str, bool, bool)  # 语音模式一轮文本（结构化）
    inject_requested = Signal(str)     # 主线程注入请求（final 文本）
    model_loading_changed = Signal(bool)  # 本地模型加载状态（加载中时 IDLE 提示替换热键提示）
    model_ready_changed = Signal(bool)    # 本地模型预热完成（就绪后回到默认提示，不再额外宣告）
    injection_method_changed = Signal(str)  # 注入方式切换（clipboard_paste / simulate_keys）
    keep_clipboard_toggled = Signal(bool)   # 保留剪贴开关
    game_mode_toggled = Signal(bool)        # 游戏模式开关（强制剪贴板粘贴注入；不降级、不恢复剪贴板）
    live_intermediate_toggled = Signal(bool)  # 逐字同步开关（app 持久化 + 即时生效；AI 修正启用或剪贴输入时被逻辑锁强制关闭）

    llm_enable_toggled = Signal(bool)       # AI 修正开关（右键菜单注入逻辑处即时启停）
    # 语音命令模式（触发词+本地命令表+自定义短语+「帮我」AI 路径，见 core/voice_commands.py）
    command_action = Signal(str, str)  # 本地命令执行（action, arg；ASR 线程 emit -> 主线程执行）
    command_ai = Signal(str)           # 「帮我」AI 命令（指令文本，主线程发起读选中+LLM 链路）
    command_phrase = Signal(str)       # 自定义短语命中（短语内容，ASR 线程 emit -> 主线程整段注入）
    command_standby = Signal(bool)     # 命令待命态进入/退出（单说触发词后等下一句）
    commands_toggled = Signal(bool)    # 语音命令总开关（右键菜单切换，app 持久化）
    bar_confirm = Signal(str, bool)         # 内嵌确认回答（类型：llm_wait / live_sync / conflict_llm / conflict_paste / conflict_commands / conflict_game_mode / conflict_method_game / conflict_live / game_mode_elevate；True=确认）
    interrupt_delete_toggled = Signal(bool)  # 删除键打断开关切换（app 据此启停监听）
    autostart_toggled = Signal(bool)         # 开机自启开关（app 写/删 HKCU Run 键）
    pause_bg_audio_toggled = Signal(bool)   # 暂停播放开关（录音时暂停后台媒体；app 持久化）
    hold_to_talk_toggled = Signal(bool)     # 长按录音开关（右键菜单切换，app 持久化）
    elevate_requested = Signal()            # 程序提权（app 拉起管理员实例接管后退出当前实例）
    # 语音模式入口（spec：只加这 1 个信号）：右键菜单 checkable「语音模式」、
    # 对话态下启停按钮/声纹主题整窗点击。checked=当前是否对话中；app 侧等价于按对话热键
    dialog_requested = Signal(bool)


class FloatingBar(QWidget):
    """浮动状态条。"""

    def __init__(self, ui_config):
        super().__init__()
        self.cfg = ui_config
        # 完整配置（含各密钥节）；self.cfg 只是 cfg.ui 子节，密钥字段不在其中
        self._full_cfg = None
        self.signals = BarSignals()

        # 「修正后上屏」确认状态：文字画在条内（极简主题走自带气泡），按钮是条外的迷你按钮条
        self._llm_wait_text = ""
        self._llm_wait_active = False
        self._confirm_kind = "llm_wait"   # 当前询问类型（llm_wait / live_sync / conflict_*）
        # 确认文字跑马灯状态（宽度不足时循环滚动显示）
        self._marquee_timer: QTimer | None = None
        self._marquee_phase = 0.0
        self._llm_wait_timer: QTimer | None = None
        self._confirm_btns: ConfirmButtons | None = None

        # 读取持久化的窗口状态，无记录则用 config 默认
        saved = _load_window_state()
        self._saved_x = int(saved.get("window_x", getattr(ui_config, "default_x", 100)))
        self._saved_y = int(saved.get("window_y", getattr(ui_config, "default_y", 100)))
        self._saved_w = int(saved.get("window_width", getattr(ui_config, "bar_width", DEFAULT_BAR_W)))
        self._saved_h = int(saved.get("window_height", getattr(ui_config, "bar_height", DEFAULT_BAR_H)))
        # 首次运行（无保存位置且未显式指定坐标）默认放到主屏正中心，
        # 比左上角 (100,100) 更易发现；用户移动后写入 state.json 持久化
        if "window_x" not in saved and "window_y" not in saved:
            if self._saved_x < 0 or self._saved_y < 0:
                try:
                    from PySide6.QtWidgets import QApplication
                    screen = QApplication.primaryScreen()
                    if screen is not None:
                        geo = screen.availableGeometry()
                        self._saved_x = geo.x() + (geo.width() - self._saved_w) // 2
                        self._saved_y = geo.y() + (geo.height() - self._saved_h) // 2
                except Exception:
                    logger.debug("计算默认窗口位置失败，回落左上角", exc_info=True)
                    self._saved_x, self._saved_y = 100, 100

        self._state = UI_IDLE
        self._level = 0.0
        self._text = ""
        self._text_is_final = False  # 当前文本是否最终结果（中间结果半透明显示）
        self._hint = ""
        self._default_hint = ""
        self._error_msg = ""
        self._error_until = 0.0
        self._model_loading = False  # 本地模型加载中，IDLE 提示换加载文案
        self._model_ready = False    # 本地模型预热完成（就绪后回到默认提示）
        self._drag_pos = None
        self._resize_pos = None  # 调整尺寸起始全局点
        self._press_pos = None   # 左键按下位置（区分"点击打断"与"拖动窗口"）
        self._current_engine = ""
        self._dot_x = PADDING + DOT_OFFSET
        self._flash_msg = ""
        self._flash_until = 0.0
        # 提示/错误到期要主动重绘：IDLE 下无重绘源（脉冲、电平都停了），
        # 不重绘提示就永远留在屏上
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._on_flash_expired)
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._on_error_expired)
        # IDLE 时间戳，用于过滤停止录音后 PortAudio 残留信号
        self._idle_since = 0.0

        # "识别中→聆听中"回切的延迟确认定时器，避免停止录音时残留信号闪烁
        self._pending_listening_timer: QTimer | None = None

        # 聆听中红点呼吸光晕
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)  # 20fps
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

        # 空闲自动隐藏到托盘（0=不启用）
        try:
            _ah = int(float(getattr(ui_config, "auto_hide_seconds", 0)))
            self._auto_hide_ms = _ah * 1000
        except (TypeError, ValueError):
            self._auto_hide_ms = 0
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(lambda: self.signals.hide_requested.emit())
        self._mouse_inside = False
        if self._auto_hide_ms > 0:
            self._auto_hide_timer.start(self._auto_hide_ms)

        # 可配置颜色
        self._bg_color = QColor(getattr(ui_config, "bg_color", DEFAULT_BG))
        self._text_color = QColor(getattr(ui_config, "text_color", DEFAULT_TEXT))
        self._accent_color = QColor(getattr(ui_config, "accent_color", DEFAULT_ACCENT))
        self._accent_dim = QColor(self._accent_color)
        self._accent_dim.setAlpha(120)
        # 用户自定义不透明度（0=跟随主题默认浅色 235/深色 235；40~99=百分比）
        self._bar_opacity = int(getattr(ui_config, "bar_opacity", 0) or 0)
        # 主题可选扩展色：窗口描边 / 顶部霓虹横线（不提供时由 _is_light 派生）
        self._border_color: QColor | None = None
        self._top_line_color: QColor | None = None
        # 当前主题预设名（用于字体变更后重绘画布/菜单）
        self._current_theme_name: str = ""
        # 凯尔特主题：启停按钮使用凯尔特结图 + 录音中旋转动画
        self._knot_icon_mode: bool = False
        self._knot_angle: float = 0.0  # 当前旋转角度（度）
        # 极简主题：正方形悬浮窗 + 声纹波形 + 上方气泡文本
        self._voice_mode: bool = False
        self._pre_voice_size: tuple | None = None  # 进入极简前的 (w, h)，切走时还原
        self._hover: bool = False  # 极简主题整窗即按钮：悬停提亮反馈
        # 像素主题（voice_print 布局之上的像素皮肤）：阶梯边框/均衡器/像素气泡
        self._pixel_mode: bool = False
        # 本会话 final 文本累积：气泡显示全文，避免频繁断句时气泡跳变
        self._session_text: str = ""
        # 语音模式对话显示（两行字幕式）：上行=上一轮，下行=当前轮。
        # 轮次号变化即新的一轮 —— 上一轮归档到上行，而不是被覆盖掉。
        # 上一轮**不限角色**（P2）：用户自己的识别文本同样要留在屏上，
        # 不能被 AI 的回复顶掉。
        self._dialog_turn_id: int = 0
        self._dialog_prev: str = ""           # 上一轮的完整文本（上行）
        self._dialog_prev_role: str = ""      # 上一轮的说话人（user / ai）
        self._dialog_prev_struck: bool = False
        self._dialog_cur: str = ""            # 当前这一轮（下行）
        self._dialog_cur_role: str = ""
        self._dialog_cur_final: bool = False
        self._dialog_cur_struck: bool = False  # 这条被打断（划线）
        # H61：默认**自动展开**（按屏高反算），点击变成"收起"（3 行紧凑视图）。
        # 用户诉求是"超长自动弹开、不用手点"，所以默认值从 False 翻成 True。
        self._dialog_expanded: bool = True     # 卡片处于自动展开态（点击收起/再展开）
        # 流式刷新帧合并（见 _schedule_dialog_refresh）：脏标记 + 单次定时器
        self._dialog_dirty: bool = False
        self._dialog_refresh_timer: QTimer | None = None
        # 凯尔特链纹边框缓存：(w, h, 颜色名) -> QPixmap，尺寸/主题变化才重建
        self._knot_border_cache: tuple | None = None
        self._injection_method = "clipboard_paste"  # 注入方式（app 同步真实配置）
        self._game_mode = False  # 游戏模式开关（app 同步真实配置）
        self._live_intermediate = True  # 逐字同步开关（app 同步生效值，AI 修正启用时锁定为 False）
        self._knot_rotate_timer: QTimer | None = None
        # 设置对话框打开期间禁用右键菜单（app._open_settings 维护）
        self._settings_dlg_open = False
        # 本地模型加载中：状态点位置显示旋转 spinner（IDLE 无重绘源，
        # spinner 必须自驱动 QTimer 每 tick 触发重绘）
        self._loading = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(50)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)
        # 置顶保活：Windows 置顶组内"后弹出者居上"，任务管理器/贴图/游戏浮层
        # 等置顶窗口后出现会压住本窗（标志仍在，视觉上"置顶失效"）。定时
        # 重设 HWND_TOPMOST 提回置顶组顶部（_assert_topmost 只在 show 时跑一次，
        # 覆盖不了别人后弹出的场景）。
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(2000)
        self._topmost_timer.timeout.connect(self._assert_topmost)

        self._init_window()
        self._init_buttons()
        self._connect_signals()
        # 气泡须在 apply_theme_name 之前创建：主题切换要同步它的配色/内容
        self._bubble = SpeechBubble(self)
        # 配置了主题预设名则整表还原（含 border/top_line 扩展色），否则用基础三色
        _theme_name = getattr(ui_config, "theme_name", "") or ""
        if _theme_name and _theme_name in available_themes():
            self.apply_theme_name(_theme_name)
        self._apply_theme()

    def _init_window(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # 配置关闭"永远置顶"时去掉 StaysOnTop 标志
        if not bool(getattr(self.cfg, "always_on_top", True)):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        else:
            self._topmost_timer.start()  # 置顶开启：启动保活定时器
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 防止显示时抢焦点
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # 悬停也发 mouseMove（光标随位置实时切换，见 mouseMoveEvent）
        self.setMouseTracking(True)
        # 使用持久化的位置和尺寸（无记录则用 config 默认）
        self.resize(self._saved_w, self._saved_h)
        self.move(self._saved_x, self._saved_y)

    def set_always_on_top(self, enabled: bool) -> None:
        """切换窗口置顶标志（右键菜单"永远置顶"）。

        setWindowFlags 会隐藏窗口，需重新 show。
        """
        flags = self.windowFlags()
        flags = (flags | Qt.WindowStaysOnTopHint) if enabled else (
            flags & ~Qt.WindowStaysOnTopHint
        )
        if enabled:
            self._topmost_timer.start()  # 置顶开：保活定时器随开随启
        else:
            self._topmost_timer.stop()   # 置顶关：停掉保活定时器
        if flags != self.windowFlags():
            self.setWindowFlags(flags)
            self.show()
        # 附属顶层小窗（气泡 / 确认按钮条）的 TOPMOST 是各自构造时写死的，必须一并
        # 同步：否则关掉置顶后条能让位、气泡却仍钉在最上层——用户关这个开关就是要
        # 整个悬浮 UI 让位，留一个气泡在前面等于开关没生效（录音中尤其明显，那时
        # 气泡是唯一的内容载体）。
        for child in (getattr(self, "_bubble", None),
                      getattr(self, "_confirm_btns", None)):
            if child is None:
                continue
            cflags = child.windowFlags()
            cflags_new = (cflags | Qt.WindowStaysOnTopHint) if enabled else (
                cflags & ~Qt.WindowStaysOnTopHint
            )
            if cflags_new == cflags:
                continue
            # setWindowFlags 会隐藏窗口：原本可见的必须重新 show 回来
            was_visible = child.isVisible()
            child.setWindowFlags(cflags_new)
            if was_visible:
                child.show()
                reassert_topmost(child)

    def is_always_on_top(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowStaysOnTopHint)

    def show(self) -> None:
        super().show()
        self._apply_no_activate()
        self._assert_topmost()
        # 显示后重置自动隐藏倒计时（呼出后若不操作，到时仍会隐藏）
        self._restart_auto_hide()
        # 加载中被隐藏过（hideEvent 停表），重新呼出若仍在加载则恢复旋转
        if self._loading:
            self._start_spinner()
        # 极简主题：气泡随 hideEvent 一并收起，呼出后按当前状态/文本重新驱动
        # （录音中或语音模式中被自动隐藏再呼出，否则会一直没有气泡）
        self._sync_bubble()

    def _assert_topmost(self) -> None:
        """Windows：重新断言 WS_EX_TOPMOST（SWP_NOACTIVATE 不抢焦点）。

        Qt 只在建窗时设一次 TOPMOST；这种窗口隐藏后重新 show，Windows 会把它插到
        置顶组底部，有别的置顶窗口时就被压住，表现为"置顶失效"。显式
        SetWindowPos(HWND_TOPMOST) 提回顶部。
        """
        if sys.platform != "win32":
            return
        if not (self.windowFlags() & Qt.WindowStaysOnTopHint):
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )
        except Exception:
            logger.debug("重新断言 TOPMOST 失败，忽略", exc_info=True)
        # 附属顶层小窗（气泡 / 确认按钮条）同样在置顶组里，压住了也得提回来：
        # 它们只在 show 和内容更新时 reassert，静止期间（识别已定稿、等修正返回、
        # 思考中）被别的置顶窗口压住就再也回不来。极简主题下内容**只**画在气泡里，
        # 气泡被压住等于什么都看不见。
        # 顺序固定「先本窗、后附属」：后提的在上，保证气泡始终盖在条之上。
        for child in (getattr(self, "_bubble", None),
                      getattr(self, "_confirm_btns", None)):
            if child is not None and child.isVisible():
                reassert_topmost(child)

    def _apply_no_activate(self) -> None:
        """Windows：设 WS_EX_NOACTIVATE，点击悬浮条不抢输入焦点，选中的文字不会被顶掉。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE
            )
        except Exception:
            logger.debug("WS_EX_NOACTIVATE 设置失败，忽略", exc_info=True)

    def _init_buttons(self) -> None:
        icon_size = QSize(18, 18)

        def make_icon_btn(icon, style, tooltip, on_click, w=ICON_BTN_W, h=BTN_H):
            btn = QPushButton("", self)
            btn.setIcon(icon)
            btn.setIconSize(icon_size)
            btn.setStyleSheet(style)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(w, h)
            btn.setToolTip(tooltip)
            btn.clicked.connect(on_click)
            return btn

        # 启停按钮（最左侧）
        self._play_icon = play_icon()
        self._stop_icon = stop_icon()
        self._btn_toggle = make_icon_btn(
            self._play_icon, _TOGGLE_BTN_PLACEHOLDER, t("bar.tip.toggle_record"),
            self._on_toggle_click
        )

        # 退出按钮（右上角，最小化到托盘；真正退出在右键菜单/托盘右键）
        self._close_icon = close_icon()
        self._btn_exit = make_icon_btn(
            self._close_icon, _TOGGLE_BTN_PLACEHOLDER, t("bar.tip.minimize"),
            self._on_exit_click,
            w=EXIT_BTN_W, h=EXIT_BTN_H,
        )

        self.setMinimumSize(MIN_W, MIN_H)
        self._layout_buttons()

    def _knot_min_height(self) -> int:
        """凯尔特主题的最小高度（含链带），委托 celtic 特性模块；
        特性模块缺失（如精简构建）时回退普通最小高度。"""
        feat = get_feature("celtic")
        if feat is not None:
            return feat.min_height()
        return MIN_H

    def _layout_buttons(self) -> None:
        h = self.height()
        if self._voice_mode:
            # 声纹主题：整窗点击切换录音，启停按钮隐藏，✕ 放右上角。
            # 边距按主题区分（两者视觉基准不同）：
            #   · 像素主题 6px —— 4px 阶梯边框内侧留 2px 呼吸（原 2px 压边框）
            #   · 极简主题 3px —— 中央声纹是占 72% 边长的**圆**，✕ 越靠角越远离
            #     圆弧；6px 时按钮矩形与圆弧相交、看着"叠在一起"（用户反馈
            #     「退出键跟图标还是有点重叠，把退出键放到更右上角一点」）。
            #     3px 是兼顾"避开圆弧"与"不被窗口圆角切掉"的落点（✕ 图标本身
            #     比 20px 按钮小且居中，不会撞上圆角）。
            pad = 6 if self._pixel_mode else 3
            self._btn_toggle.hide()
            self._btn_exit.move(self.width() - EXIT_BTN_W - pad, pad)
            return
        self._btn_toggle.show()
        # 凯尔特主题：按钮放大到 52×44，给凯尔特结图标更大画布
        btn_w, btn_h = (52, 44) if self._knot_icon_mode else (ICON_BTN_W, BTN_H)
        self._btn_toggle.setFixedSize(btn_w, btn_h)
        y_center = (h - btn_h) // 2
        # 按钮位置：凯尔特落链带内侧（左侧 ~23px）；赛博朋克切角按钮多留 3px；其余 PADDING
        if self._knot_icon_mode:
            btn_x = 18
        elif self._top_line_color is not None:
            btn_x = PADDING + 3
        else:
            btn_x = PADDING
        self._btn_toggle.move(btn_x, y_center)
        # 状态点偏移跟随按钮实际位置（统一 12px 节奏：按钮→点→文本）；
        # 凯尔特：结图标 40px 居中于 52px 按钮内、右缘余 6px 空白，间距收窄补偿
        dot_gap = BTN_DOT_GAP - 6 if self._knot_icon_mode else BTN_DOT_GAP
        self._dot_x = btn_x + btn_w + dot_gap
        # 退出按钮右上角；凯尔特主题角落是密集角结花纹，小 X 叠上去看不清，内移
        if self._knot_icon_mode:
            self._btn_exit.move(self.width() - EXIT_BTN_W - 25, 23)
        else:
            self._btn_exit.move(self.width() - EXIT_BTN_W - 6, 4)
        # 赛博朋克主题下按钮带切角 mask，需几何就绪后才能设置
        if self._top_line_color is not None:
            self._update_toggle_mask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_buttons()
        self._sync_voice_mask()  # 窗口尺寸变化时同步圆形遮罩
        if getattr(self, "_bubble", None) is not None and self._bubble.isVisible():
            self._bubble._sync_pos()
        # 确认条由绘制实现，尺寸变化无需重排（下次 paint 自适应）

    def _sync_voice_mask(self) -> None:
        """极简主题不设窗口遮罩（圆角由 paintEvent 画），清掉可能残留的旧 mask。"""
        self.clearMask()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        # 气泡跟随悬浮窗移动（声纹主题）
        if getattr(self, "_bubble", None) is not None and self._bubble.isVisible():
            self._bubble._sync_pos()
        # 条外确认按钮条跟随悬浮窗移动
        if getattr(self, "_confirm_btns", None) is not None:
            if self._confirm_btns.isVisible():
                self._confirm_btns._sync_pos()

    def _connect_signals(self) -> None:
        self.signals.state_changed.connect(self._set_state)
        self.signals.dialog_turn.connect(self._on_dialog_turn)
        self.signals.level_changed.connect(self._set_level)
        self.signals.text_updated.connect(self._set_text)
        self.signals.error_occurred.connect(self._set_error)
        self.signals.show_requested.connect(self.show)
        self.signals.hide_requested.connect(self.hide)
        self.signals.transient_hint.connect(self._flash_hint)
        self.signals.model_loading_changed.connect(self.set_model_loading)
        self.signals.model_ready_changed.connect(self.set_model_ready)
        # 命令待命态视觉反馈：ASR 线程 emit 经 queued 连接回主线程
        self.signals.command_standby.connect(self._on_command_standby)

    def _on_command_standby(self, active: bool) -> None:
        """命令待命态进入/退出：悬浮条提示。"""
        if active:
            self._flash_hint(t("hint.command_standby"), 8.0)
        # 退出时不另提示（命令结果/超时取消各有提示）

    # ---- 公共方法 ----

    def set_injection_method(self, method: str) -> None:
        """同步当前注入方式（右键菜单勾选状态用）。"""
        self._injection_method = method if method in ("clipboard_paste", "simulate_keys") else "clipboard_paste"

    def set_keep_clipboard(self, enabled: bool) -> None:
        """同步"保留剪贴"开关（右键菜单勾选状态用）。"""
        self._keep_clipboard = bool(enabled)

    def set_game_mode(self, enabled: bool) -> None:
        """同步"游戏模式"开关（右键菜单勾选状态用）。"""
        self._game_mode = bool(enabled)

    def set_live_intermediate(self, enabled: bool) -> None:
        """同步"逐字同步"开关（菜单勾选态用）。app 传的是扣过逻辑锁的生效值，非配置原值。"""
        self._live_intermediate = bool(enabled)

    # ---- 内嵌确认提示（文字条内渲染；按钮在条外迷你按钮条） ----

    def show_llm_wait_confirm(
        self, text: str, kind: str = "llm_wait",
        yes_text: str | None = None, no_text: str | None = None,
    ) -> None:
        """内嵌询问（提示文字条内渲染，极简主题走气泡；按钮在条外）：
        kind 标识询问类型（llm_wait / live_sync / conflict_llm / conflict_paste /
        conflict_commands / conflict_game_mode / conflict_method_game /
        conflict_live / game_mode_elevate），
        回答时随 bar_confirm 信号带回；yes_text/no_text 可自定义按钮文字
        （冲突更换类用"确认/取消"）。15 秒无操作自动收起。"""
        self._llm_wait_text = text
        self._llm_wait_active = True
        self._confirm_kind = kind
        self._marquee_phase = 0.0  # 重置滚动位置
        if self._confirm_btns is None:
            self._confirm_btns = ConfirmButtons(self)
        self._confirm_btns.set_labels(
            yes_text if yes_text is not None else t("common.confirm"),
            no_text if no_text is not None else t("common.not_now"),
        )
        self._confirm_btns.set_colors(
            QColor(self._bg_color), QColor(self._text_color)
        )
        self._confirm_btns._sync_pos()
        self._confirm_btns.show()
        if self._llm_wait_timer is None:
            self._llm_wait_timer = QTimer(self)
            self._llm_wait_timer.setSingleShot(True)
            self._llm_wait_timer.timeout.connect(self.hide_llm_wait_confirm)
        self._llm_wait_timer.start(15000)
        self._sync_bubble()  # 极简主题：确认文字同步到自带气泡
        self.update()

    def hide_llm_wait_confirm(self) -> None:
        """隐藏确认提示（应答或超时后收起）。"""
        if not self._llm_wait_active:
            return
        self._llm_wait_active = False
        self._stop_marquee()
        if self._confirm_btns is not None:
            self._confirm_btns.hide()
        self._sync_bubble()  # 极简主题：气泡回到常规内容
        self.update()

    def _confirm_answered(self, enabled: bool) -> None:
        """条外按钮条的应答：收起确认并把（类型, 结果）转发给 app。"""
        self.hide_llm_wait_confirm()
        self.signals.bar_confirm.emit(self._confirm_kind, enabled)

    def _draw_llm_confirm(self, painter: QPainter) -> None:
        """绘制确认提示文字：与识别结果同款字体/颜色；宽度足够一次显示，
        不足则循环滚动（跑马灯）；极简主题文字走自带气泡，不在条上重复画。"""
        if self._voice_mode or not self._llm_wait_text:
            return
        size = int(getattr(self.cfg, "font_size_bar", 13) or 13)
        font = get_font(size)
        painter.setFont(font)
        left = getattr(self, "_dot_x", 10) + DOT_D + DOT_TEXT_GAP
        right = self._btn_exit.x() - 8
        max_width = right - left
        if max_width <= 8:
            return
        painter.setPen(self._text_color)
        metrics = QFontMetrics(font)
        if metrics.horizontalAdvance(self._llm_wait_text) <= max_width:
            # 宽度足够：一次显示完整文字
            self._stop_marquee()
            painter.drawText(
                left, 0, max_width, self.height(),
                Qt.AlignVCenter | Qt.AlignLeft, self._llm_wait_text,
            )
        else:
            # 宽度不足：循环滚动（跑马灯），文字始终可读完
            self._start_marquee()
            self._draw_marquee_text(painter, font, left, max_width)

    # ---- 确认文字跑马灯（宽度不足时循环滚动） ----

    def _start_marquee(self) -> None:
        """启动滚动定时器（30ms/帧，仅文字溢出时运行）。"""
        if self._marquee_timer is None:
            self._marquee_timer = QTimer(self)
            self._marquee_timer.setInterval(30)
            self._marquee_timer.timeout.connect(self._on_marquee_tick)
        if not self._marquee_timer.isActive():
            self._marquee_timer.start()

    def _stop_marquee(self) -> None:
        if self._marquee_timer is not None and self._marquee_timer.isActive():
            self._marquee_timer.stop()

    def _on_marquee_tick(self) -> None:
        self._marquee_phase += 1.5  # 每 30ms 前进 1.5px ≈ 50px/s
        self.update()

    def _draw_marquee_text(
        self, painter: QPainter, font, left: int, max_width: int,
    ) -> None:
        """跑马灯绘制：文本向左滑出、右侧循环进入（双份无缝）。

        裁剪区固定在 [状态点右侧, ✕ 左侧]：滚动文字永远不会越过
        录音红点/启停按钮或 ✕——循环范围只在文本区内。
        """
        painter.save()
        painter.setClipRect(left, 0, max_width, self.height())
        metrics = QFontMetrics(font)
        tw = metrics.horizontalAdvance(self._llm_wait_text)
        gap = 28
        period = tw + gap
        offset = int(self._marquee_phase) % period
        x = left - offset
        for dx in (x - period, x, x + period):
            painter.drawText(
                dx, 0, tw, self.height(),
                Qt.AlignVCenter | Qt.AlignLeft, self._llm_wait_text,
            )
        painter.restore()

    def _engine_group_visible(self, group: str) -> bool:
        """提供商分组是否在识别引擎菜单中显示（设置里可逐个隐藏）。"""
        return bool(getattr(self.cfg, f"engine_show_{group}", True))

    def set_full_config(self, cfg) -> None:
        """同步完整配置（含密钥节）：engine_group_configured 要用顶层节判断密钥，
        cfg.ui 子节没有。热加载整体替换 cfg 对象后必须重新调用。"""
        self._full_cfg = cfg
        # 同步逐字同步配置原值（recording 节）；锁生效值由 app 随后用 set_live_intermediate 覆盖
        self._live_intermediate = bool(
            getattr(getattr(cfg, "recording", None), "live_intermediate", True)
        )

    @staticmethod
    def _engine_group_of(engine_id: str) -> str:
        """菜单引擎 ID -> 提供商分组（tencent/aliyun/xfyun/volcengine/funasr）。

        委托 presets.engine_group_of：与设置页/右键菜单共用同一套归属规则。
        """
        return engine_group_of(engine_id)

    def set_hint(self, text: str) -> None:
        self._hint = text
        self._default_hint = text
        if self._state == UI_IDLE:
            self.update()

    def set_model_loading(self, loading: bool) -> None:
        """本地模型加载状态，加载中 IDLE 提示换加载文案。worker 线程别直接调，走信号。"""
        if self._model_loading == loading:
            return
        self._model_loading = loading
        # 加载中：状态点位置显示旋转 spinner；结束：停表
        self._loading = loading
        if loading:
            self._start_spinner()
        else:
            self._stop_spinner()
        if self._state == UI_IDLE:
            self._hint = self._idle_hint()
        # 极简主题：加载开始弹出 loading 气泡；结束重算内容（不吞进行中的瞬态提示/错误）
        self._sync_bubble()
        self.update()

    def set_model_ready(self, ready: bool) -> None:
        """本地模型预热完成：停转 spinner 并回到默认待机提示（不再额外宣告"已就绪"）。"""
        if self._model_ready == ready:
            return
        self._model_ready = ready
        if ready:
            # 预热完成即加载必然结束：防御性确保 spinner 停转（覆盖 loading(False) 缺失路径）
            self._loading = False
            self._stop_spinner()
        if self._state == UI_IDLE:
            self._hint = self._idle_hint()
        # 加载结束/失败/超时路径（loading(False) 与 ready 任意顺序）：同步收起 loading 气泡
        self._sync_bubble()
        self.update()

    def _idle_hint(self) -> str:
        """待机提示：加载中 > 默认。加载文案只对 FunASR 显示，云端引擎不占提示位；
        模型就绪后直接回到默认提示，不再额外宣告"已就绪"。"""
        if self._current_engine.startswith("funasr") and self._model_loading:
            return t("hint.model_loading")
        return self._default_hint

    def set_engine(self, engine: str) -> None:
        self._current_engine = engine
        # 引擎切换影响 IDLE 提示（模型加载/就绪文案只对 FunASR 引擎显示）
        if self._state == UI_IDLE:
            self._hint = self._idle_hint()
            self.update()

    def is_auto_hide_enabled(self) -> bool:
        """当前是否启用了自动隐藏。"""
        return self._auto_hide_ms > 0


    def set_auto_hide(self, enabled: bool, seconds: int = 5) -> None:
        """切换自动隐藏开关，立即生效。"""
        if enabled:
            self._auto_hide_ms = seconds * 1000
            self._restart_auto_hide()
        else:
            self._auto_hide_ms = 0
            self._auto_hide_timer.stop()

    def set_bar_opacity(self, opacity: int) -> None:
        """更新自定义不透明度：0=主题默认；1~100=百分比，100 完全不透明。"""
        self._bar_opacity = int(opacity or 0)
        if self._bg_color is not None:
            if self._bar_opacity and 0 < self._bar_opacity <= 100:
                alpha = int(255 * self._bar_opacity / 100)
            else:
                alpha = 240 if self._is_light(self._bg_color.name()) else 235
            self._bg_color.setAlpha(alpha)
        self.update()

    def apply_theme_name(self, theme_name: str) -> None:
        """按主题预设名应用整套配色（含可选 border / top_line / knot_icon / voice_print）。"""
        # 局部名不要用 t：本模块 import 了 i18n 的 t()，函数内绑定 t 会
        # 把整个函数作用域里的 t 变成局部名（Python 作用域是函数级的），
        # 导致同函数内的 t("key") 抛 UnboundLocalError。
        theme = available_themes().get(theme_name)
        if not theme:
            return
        prev_theme = self._current_theme_name
        self._current_theme_name = theme_name
        # 先更新主题标记再应用配色：_apply_theme 依赖它们选择图标/尺寸分支
        self._knot_icon_mode = bool(theme.get("knot_icon"))
        self._pixel_mode = bool(theme.get("pixel"))
        voice_mode = bool(theme.get("voice_print"))
        if voice_mode != self._voice_mode:
            if voice_mode:
                # 进入声纹/方窗布局：记住条形尺寸（切回时还原），强制正方形。
                # 边长固定默认值、不从当前尺寸继承（旧的 max(VOICE_SIDE, min(w,h))
                # 会把拖大的条形带进方窗，越滚越大回不去）。
                # 像素主题的默认单独由 PIXEL_DEFAULT_SIDE 指定（见下方分支）。
                self._pre_voice_size = (self.width(), self.height())
                self.setMinimumSize(VOICE_MIN_SIDE, VOICE_MIN_SIDE)
                self.resize(VOICE_SIDE, VOICE_SIDE)
            else:
                # 离开声纹：还原条形尺寸与最小尺寸约束，收起气泡
                self.setMinimumSize(MIN_W, MIN_H)
                w, h = self._pre_voice_size or (
                    int(getattr(self.cfg, "bar_width", DEFAULT_BAR_W) or DEFAULT_BAR_W),
                    int(getattr(self.cfg, "bar_height", DEFAULT_BAR_H) or DEFAULT_BAR_H),
                )
                self.resize(max(MIN_W, w), max(MIN_H, h))
                self._pre_voice_size = None
                self._bubble.hide()
            self._voice_mode = voice_mode
            self._sync_voice_mask()  # 主题切换时同步圆形遮罩（进入/离开极简）
        # 凯尔特九宫格链框：窗高不足以容纳"上下链带 + 启停按钮"时
        # 自动抬升到主题最小高度，保证按钮完整落在链带内侧
        if self._knot_icon_mode and self.height() < self._knot_min_height():
            self.resize(self.width(), self._knot_min_height())
        # 像素主题：切换进入（含启动）时方窗回到**像素主题自己的默认边长**
        # PIXEL_DEFAULT_SIDE = 70（用户实际使用并确认的尺寸）——不从当前窗口
        # 尺寸继承，因为旧逻辑 max(VOICE_SIDE, min(w,h)) 会把拖大的尺寸带进来、
        # 越滚越大回不去。仅在主题名变化时触发：配置重载等对当前主题的重放
        # （app.py:2510）不动用户正在使用的尺寸；用户在主题内临时拖到别的
        # 尺寸也只在该次会话内有效，切主题/重启回到 70。
        if self._pixel_mode and prev_theme != theme_name:
            self.resize(PIXEL_DEFAULT_SIDE, PIXEL_DEFAULT_SIDE)
        # 按钮位置在 apply_colors 末尾重排：那里才刷新 _top_line_color 等主题标记
        self.apply_colors(
            theme["bg"], theme["text"], theme["accent"],
            border=theme.get("border"), top_line=theme.get("top_line"),
        )
        # 气泡跟随主题换色并同步内容（待机保持隐藏）
        self._bubble.set_colors(self._bg_color, self._text_color)
        self._sync_bubble()
        # 像素主题待机动画：切进像素主题且当前待机则起播，切走则停表
        if not self._pixel_mode:
            self._stop_pixel_idle_animation()
        self._sync_pixel_idle_animation()

    def apply_colors(self, bg: str, text: str, accent: str,
                    border: str | None = None, top_line: str | None = None) -> None:
        """运行时更新配色并同步明暗适配。border 覆盖默认描边、top_line 是顶部霓虹
        横线（None 不画），均仅部分主题用。"""
        self._bg_color = QColor(bg)
        # bar_opacity：0=主题默认；1~100=百分比，100 完全不透明。
        # 主题默认：浅色 240（94%，叠深色桌面仍近白）/深色 235（磨砂感）
        if self._bar_opacity and 0 < self._bar_opacity <= 100:
            alpha = int(255 * self._bar_opacity / 100)
        else:
            alpha = 240 if self._is_light(bg) else 235
        self._bg_color.setAlpha(alpha)
        self._text_color = QColor(text)
        self._accent_color = QColor(accent)
        self._accent_dim = QColor(accent)
        self._accent_dim.setAlpha(120)
        self._border_color = QColor(border) if border else None
        self._top_line_color = QColor(top_line) if top_line else None
        self._apply_theme()

    @staticmethod
    def _is_light(color_hex: str) -> bool:
        """判断颜色是否为浅色（亮度 > 128）。"""
        c = QColor(color_hex)
        return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 128

    def _apply_theme(self) -> None:
        """根据当前背景明暗，统一适配图标色、按钮样式与菜单样式。

        不用 QGraphicsDropShadowEffect：WA_TranslucentBackground 分层窗口下阴影画在
        窗口边界外会触发 UpdateLayeredWindowIndirect failed，画面冻结、提示无法刷新。
        层次感靠 paintEvent 内描边。
        """
        light = self._is_light(self._bg_color.name())
        # 图标色：浅底深图标、深底浅图标；主题专属图标委托特性模块
        feat = (get_feature("celtic") if self._knot_icon_mode else None) or (
            get_feature("cyberpunk") if self._top_line_color is not None else None
        )
        if feat is not None:
            feat.apply_icons(self)
        else:
            # 图标 18→20：按钮加 1px 描边后内容区 34×28，20px 仍安全，
            # 三角形有效面积增 ~11%，浅色下更醒目
            self._btn_toggle.setIconSize(QSize(20, 20))
            icon_color = "#1d1d1f" if light else "#e8e8e8"
            self._play_icon = play_icon(icon_color)
            # 录音中图标用红色，与状态点同色，状态靠颜色不靠形状区分
            self._stop_icon = stop_icon("#e85656")
        if self._state in (UI_LISTENING, UI_RECOGNIZING):
            self._btn_toggle.setIcon(self._stop_icon)
        else:
            self._btn_toggle.setIcon(self._play_icon)
        # 主题热切换时保持在「准备中」的结图（状态点等表达在各自绘制里，见 _apply_state）
        self._sync_armed_icon()
        self._apply_toggle_button_style()
        # 退出按钮平时淡图标、hover 提亮；凯尔特主题叠在白角饰上，恒用白 X+黑描边
        self._close_icon_dim = None
        self._close_icon_bright = None
        _feat_celtic = get_feature("celtic") if self._knot_icon_mode else None
        if _feat_celtic is not None:
            self._close_icon = _feat_celtic.close_icon()
            self._close_icon_dim = self._close_icon
            self._close_icon_bright = self._close_icon
        else:
            base = "#1d1d1f" if light else "#e8e8e8"
            self._close_icon_dim = close_icon(_rgba(base, 90))   # 平时淡色
            self._close_icon_bright = close_icon(base)           # 悬停提亮
            self._close_icon = self._close_icon_dim
        self._btn_exit.setIcon(self._close_icon)
        self._btn_exit.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 6px; }
            QPushButton:hover { background: rgba(194, 58, 58, 70); }
            QPushButton:pressed { background: rgba(158, 46, 46, 90); }
        """)
        # 监听退出按钮自身的 Enter/Leave：淡图标 ↔ 提亮图标
        if not getattr(self, "_exit_filter_installed", False):
            self._btn_exit.installEventFilter(self)
            self._exit_filter_installed = True
        # 主题标记已刷新，按主题重排按钮位置（凯尔特链带内侧/赛博朋克多留/其他 PADDING）
        self._layout_buttons()
        # 气泡配色同步（直接改色/热加载路径不经 apply_theme_name）
        if getattr(self, "_bubble", None) is not None:
            self._bubble.set_colors(self._bg_color, self._text_color)
            # 自定义配色热应用时 loading 气泡旧色残留：set_colors 对非空文本
            # 不换文字色，这里把 loading 内容按新配色重推一次（内部自判状态）
            if self._loading and self._voice_mode:
                self._sync_bubble()
        self.update()

    def _apply_toggle_button_style(self) -> None:
        """启停按钮样式，随主题与录音状态联动。

        凯尔特/赛博朋克的专属样式委托特性模块（theme_features）；其余：
        深浅各自中性灰阶，录音态加深底色+实色描边。
        """
        feat_celtic = get_feature("celtic") if self._knot_icon_mode else None
        if feat_celtic is not None:
            # 凯尔特：完全透明无 hover 反馈，图标即主体
            self._btn_toggle.setStyleSheet(feat_celtic.TOGGLE_QSS)
            self._btn_toggle.clearMask()
            return
        feat_cyber = get_feature("cyberpunk") if self._top_line_color is not None else None
        if feat_cyber is not None:
            # 赛博朋克：HUD 风细描边 + 左下切角
            feat_cyber.apply_toggle_style(self)
            return
        # 无专属样式：取消可能残留的切角 mask
        self._btn_toggle.clearMask()
        recording = self._state in (UI_LISTENING, UI_RECOGNIZING)
        light = self._is_light(self._bg_color.name())
        # 中性灰阶，不用 accent 蓝（深浅主题都违和）；录音态同色系加压，
        # 红只留给小停止图标和状态点
        if light:
            # 浅色主题：Fluent 软边灰阶（白底+浅灰描边，越深越"激活"），
            # 黑系硬边在近白底上太跳
            if recording:
                base = "#e4e4ea"
                border = "#9a9aa4"
                hover = "#dcdce4"
                hover_border = "#8a8a94"
                pressed = "#d0d0d9"
            else:
                base = "#ffffff"
                border = "#d4d4dc"
                hover = "#f2f2f6"
                hover_border = "#c4c4cd"
                pressed = "#e8e8ee"
        else:
            # 深色主题：白系灰阶（越白越"激活"）
            if recording:
                base = "rgba(255,255,255,0.26)"
                border = "rgba(255,255,255,0.95)"
                hover = "rgba(255,255,255,0.34)"
                hover_border = "rgba(255,255,255,1.0)"
                pressed = "rgba(255,255,255,0.18)"
            else:
                base = "rgba(255,255,255,0.08)"
                border = "rgba(255,255,255,0.18)"
                hover = "rgba(255,255,255,0.14)"
                hover_border = "rgba(255,255,255,0.28)"
                pressed = "rgba(255,255,255,0.05)"
        self._btn_toggle.setStyleSheet(f"""
            QPushButton {{ background: {base}; border: 1px solid {border}; border-radius: 10px; }}
            QPushButton:hover {{ background: {hover}; border: 1px solid {hover_border}; }}
            QPushButton:pressed {{ background: {pressed}; border: 1px solid {border}; }}
        """)

    def _sync_armed_icon(self) -> None:
        """「准备中」时按主题换启停按钮图标：只有凯尔特结图需要换色（暗红、不旋转）。

        其余主题的 ARMED 表达在各自的绘制里：内置三套是状态点暗红（极简另有
        中央声纹取色）、像素是均衡器/状态块、赛博朋克是切角边框。按钮图标一律
        保持待机样子——换成红色停止图标就"看着像录音"，正是 spec §3.2.1 要避免的。
        非凯尔特主题是 no-op（特性模块可能被 lite 构建裁掉）。
        """
        if self._state != UI_ARMED:
            return
        feat = get_feature("celtic") if self._knot_icon_mode else None
        if feat is not None:
            feat.apply_armed_icon(self)

    def _update_toggle_mask(self) -> None:
        """赛博朋克按钮左下切角 mask（委托 cyberpunk 特性模块）。"""
        feat = get_feature("cyberpunk")
        if feat is not None:
            feat.update_mask(self._btn_toggle)

    # ---- 本地模型加载 spinner（状态点位置的旋转加载指示） ----

    def _on_spinner_tick(self) -> None:
        """加载中每 50ms 旋转 18°，约 1 秒一圈（与凯尔特结旋转同节奏）。"""
        self._spinner_angle = (self._spinner_angle + 18) % 360
        if self._voice_mode:
            # 极简主题 spinner 画在气泡里（中央声纹保持纯图案）：把新角度推给
            # 气泡（存字段 + update），仅 update 不改角度会静止
            bubble = getattr(self, "_bubble", None)
            if bubble is not None and bubble.is_loading():
                bubble.set_spinner_angle(self._spinner_angle)
        else:
            self.update()

    def _start_spinner(self) -> None:
        if not self.isVisible():
            return
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        if self._spinner_timer.isActive():
            self._spinner_timer.stop()

    # ---- 状态 ----

    def _set_state(self, state: str) -> None:
        import time

        # 停止后 80ms 内的 LISTENING/RECOGNIZING 是 PortAudio 残留，忽略（人手速远大于 80ms）
        if state in (UI_LISTENING, UI_RECOGNIZING) and self._state == UI_IDLE:
            if time.time() - self._idle_since < 0.08:
                return

        # 丢弃残留的"识别中→聆听中"回切，避免停止后闪一帧"正在聆听"
        if state == UI_LISTENING and self._state == UI_RECOGNIZING:
            if self._pending_listening_timer is not None:
                self._pending_listening_timer.stop()
            self._pending_listening_timer = QTimer(self)
            self._pending_listening_timer.setSingleShot(True)
            self._pending_listening_timer.timeout.connect(
                lambda: self._apply_state(UI_LISTENING)
            )
            self._pending_listening_timer.start(30)
            return

        # 收到 IDLE/RECOGNIZING：取消 pending 的回切
        if state != UI_LISTENING and self._pending_listening_timer is not None:
            self._pending_listening_timer.stop()
            self._pending_listening_timer = None

        self._apply_state(state)

    def _apply_state(self, state: str) -> None:
        import time
        prev_state, self._state = self._state, state
        if state == UI_LISTENING and prev_state in (UI_IDLE, UI_ARMED):
            # 仅新会话（待机/长按准备→聆听）清累积，会话内回切聆听不清已确认文本。
            # ARMED 必须一并算作"新会话起点"：长按路径是 IDLE→ARMED→LISTENING，
            # 只认 IDLE 会让 prev_state=ARMED 绕过清理，上一轮的 _session_text
            # 被 _set_text 的累积拼接带进本轮气泡 —— 表现为"长按录音气泡文字残留"
            self._session_text = ""
        if state in _DIALOG_STATES and prev_state not in _DIALOG_STATES:
            # 进入对话：清掉上一段录音/对话的气泡累积（现状只 idle→listening 清，
            # 旧文本会漏进对话气泡）；文本与阅读保持/打断标记一并复位
            self._session_text = ""
            self._text = ""
            self._reset_dialog_display()
        if state == UI_IDLE:
            self._text = ""
            self._reset_dialog_display()
            self._hint = self._idle_hint()
            self._btn_toggle.setIcon(self._play_icon)
            self._btn_toggle.setToolTip(t("hint.start_recording"))
            self._pulse_timer.stop()
            if not self._loading:
                self._stop_spinner()   # 守卫：别误停 FunASR 预热的加载 spinner
            # 凯尔特主题：停止旋转并复位到 0° 待机图
            _feat = get_feature("celtic") if self._knot_icon_mode else None
            if _feat is not None:
                _feat.stop_rotation(self)
            self._restart_auto_hide()
            # 记录进入 IDLE 的时间，用于过滤停止后的残留信号
            self._idle_since = time.time()
        elif state == UI_LISTENING:
            self._hint = t("hint.listening")
            self._btn_toggle.setIcon(self._stop_icon)
            self._btn_toggle.setToolTip(t("hint.stop_recording"))
            self._pulse_phase = 0.0
            self._pulse_timer.start()
            # 凯尔特主题：开始凯尔特结旋转动画
            _feat = get_feature("celtic") if self._knot_icon_mode else None
            if _feat is not None:
                _feat.start_rotation(self)
            if self._auto_hide_ms > 0:
                self._auto_hide_timer.stop()
        elif state == UI_RECOGNIZING:
            self._hint = t("hint.recognizing")
            self._btn_toggle.setIcon(self._stop_icon)
            # 录音会话期间保持脉冲光晕，不因出字中断
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
            # 凯尔特主题：识别中保持旋转（与录音视为同一会话阶段）
            _feat = get_feature("celtic") if self._knot_icon_mode else None
            if _feat is not None and self._knot_rotate_timer is None:
                _feat.start_rotation(self)
            if self._auto_hide_ms > 0:
                self._auto_hide_timer.stop()
        elif state == UI_ARMED:
            # 长按准备中（spec §3.2.1）：纯提示态，刻意**不**设 _hint、不换停止
            # 图标、不起脉冲光晕——那些都是录音态的样子，而准备中可能被撤销
            # （轻点丢弃），像录音就会"红一下又消失像出错"。各主题的 ARMED 表达
            # 在自身绘制里（状态点/声纹取色/均衡器/边框），只有凯尔特结图要换图标
            self._sync_armed_icon()
        elif state in _DIALOG_STATES:
            # ---- 语音对话四态（spec「悬浮条」小节；文本由 controller 经
            # text_updated 推「你：/AI：」前缀文本，_hint 只兜底）----
            if not self._loading:
                self._stop_spinner()
            if state == DIALOG_UI_CONNECTING:
                self._hint = t("hint.dialog_connecting")
            elif state == DIALOG_UI_LISTENING:
                self._hint = t("hint.dialog_listening")
            elif state == DIALOG_UI_THINKING:
                self._hint = t("hint.dialog_thinking")
                # 思考态：状态点位置换 spinner（复用本地模型加载那套转圈）
                self._loading = False       # 不占 IDLE 加载提示位
                self._start_spinner()
            elif state == DIALOG_UI_SPEAKING:
                self._hint = t("hint.dialog_speaking")
                self._pulse_phase = 0.0
                self._pulse_timer.start()   # 播报呼吸光晕（同录音态动画）
            # 对话中按钮=退出对话（_on_toggle_click 分流到 dialog_requested）
            self._btn_toggle.setIcon(self._stop_icon)
            self._btn_toggle.setToolTip(t("hint.exit_dialog"))
            if self._auto_hide_ms > 0:
                self._auto_hide_timer.stop()
        # 状态切换后同步按钮样式（赛博朋克主题下黄/红描边随状态切换）
        self._apply_toggle_button_style()
        self._sync_bubble()
        # 像素主题：待机↔活跃切换时启停五柱跳动动画
        self._sync_pixel_idle_animation()
        self.update()

    def _on_pulse_tick(self) -> None:
        """驱动红点呼吸光晕动画。"""
        self._pulse_phase = (self._pulse_phase + 0.04) % 1.0  # ~1.2s 一个周期
        self.update()

    def _set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def _set_text(self, text: str, is_final: bool) -> None:
        self._text = text
        self._text_is_final = bool(is_final)
        if is_final and text and self._state not in _DIALOG_STATES:
            # 气泡显示会话累积全文：final 逐句拼接；引擎发全量累积（腾讯）时
            # 以累积开头则直接替换，避免重复。
            # 语音模式除外：对话内容走 dialog_turn 结构化通道（两行字幕式，
            # 整场累积没有意义），与这里的录音文本累积互不干扰
            acc = self._session_text
            self._session_text = text if (acc and text.startswith(acc)) else acc + text
        # 仅在录音会话内把状态升为"识别中"。停止后放行上屏的冲刷 final
        #（FunASR/自动停止/腾讯 preview "停止即上屏"）也会走到这里，此时
        # 悬浮条已是 IDLE：直接改 _state 会让状态点/声纹图标一直停在录音
        # 外观，且没有后续状态信号能把它改回来（图标"残留在播放状态"）
        if text and self._state in (UI_LISTENING, UI_RECOGNIZING):
            self._state = UI_RECOGNIZING
        self._sync_bubble()
        self.update()

    def _on_dialog_turn(self, turn_id: int, role: str, text: str,
                       is_final: bool, interrupted: bool) -> None:
        """一轮对话文本（结构化信号）：维护「上一轮 + 当前轮」两行。

        轮次号变化 = 新的一轮：把刚结束的那一轮归档到上行（**不限角色**，
        上一轮是谁说的就带谁的角色），当前行另起。
        同一轮内会反复推（豆包按句、Qwen 按 delta），此时只更新当前行。
        """
        if self._state not in _DIALOG_STATES:
            return                          # 已退出对话（信号在路上），丢弃
        if turn_id != self._dialog_turn_id:
            self._dialog_turn_id = turn_id
            if self._dialog_cur:
                # 归档上一轮：不限角色。原先只归档 AI，用户问完话、AI 一
                # 开口，用户那句就被覆盖 —— 正是 P2 要修的痛点
                self._dialog_prev = self._dialog_cur
                self._dialog_prev_role = self._dialog_cur_role or ROLE_USER
                self._dialog_prev_struck = self._dialog_cur_struck
            self._dialog_cur = ""
            self._dialog_cur_struck = False
            self._dialog_expanded = True    # 新一轮回到自动展开（H61）
            # 滚动位置一并归零：不归零的话，用户在上一轮回复里翻到中段后，
            # 新一轮的第一句会"显示在旧文本中间"
            if getattr(self, "_bubble", None) is not None:
                self._bubble._scroll_skip = 0
        self._dialog_cur = text
        self._dialog_cur_role = role or ROLE_USER
        self._dialog_cur_final = bool(is_final)
        if interrupted:
            self._dialog_cur_struck = True
        # 帧合并：一次回复会来几十上百个 delta，逐个直接重排 + 强制同步重绘会把
        # 主线程占满（长回复下系统直接报"未响应"）。这里只置脏标记，真正落屏交给
        # _flush_dialog_refresh 按 DIALOG_REFRESH_MS 合成。
        self._dialog_dirty = True
        self._schedule_dialog_refresh()

    def _schedule_dialog_refresh(self) -> None:
        """按帧合并对话文本刷新：已有在途定时器就不再重启（尾帧不会被推迟）。"""
        if self._dialog_refresh_timer is None:
            self._dialog_refresh_timer = QTimer(self)
            self._dialog_refresh_timer.setSingleShot(True)
            self._dialog_refresh_timer.timeout.connect(self._flush_dialog_refresh)
        if not self._dialog_refresh_timer.isActive():
            self._dialog_refresh_timer.start(DIALOG_REFRESH_MS)

    def _flush_dialog_refresh(self) -> None:
        """把本帧累积的对话文本一次性落到气泡。

        状态守卫与 _on_dialog_turn 入口同一条件：退出对话后信号栈里剩下的尾帧
        不该把内容再画回气泡（定时器比信号晚一拍，这条兜住）。
        """
        if not self._dialog_dirty:
            return
        self._dialog_dirty = False
        if self._state not in _DIALOG_STATES:
            return
        self._sync_bubble()
        self.update()

    def _active_flash(self, now: float) -> str:
        """当前有效的临时提示（过期返回空串）。"""
        if self._flash_msg and now < self._flash_until:
            return self._flash_msg
        return ""

    def _dialog_rows(self) -> list[dict]:
        """待显示的行（气泡卡片专用）。

        上行 = 上一轮（灰、角色不限）, 下行 = 当前轮（角色色带）。没有任何内容
        时返回空列表，调用方回落状态提示（"对话中 · 请说话" 等）。
        """
        rows: list[dict] = []
        if self._dialog_prev:
            rows.append({
                "role": self._dialog_prev_role or ROLE_AI,
                "text": self._dialog_prev,
                "struck": self._dialog_prev_struck,
                "current": False,
                "final": True,
            })
        if self._dialog_cur:
            rows.append({
                "role": self._dialog_cur_role or ROLE_USER,
                "text": self._dialog_cur,
                "struck": self._dialog_cur_struck,
                "current": True,
                "final": self._dialog_cur_final,
            })
        return rows

    def toggle_dialog_expand(self) -> None:
        """气泡单击：展开/收起全文（纯显示动作；新一轮会自动收起）。"""
        if self._state not in _DIALOG_STATES:
            return
        self._dialog_expanded = not self._dialog_expanded
        self._sync_bubble()
        self.update()

    def _reset_dialog_display(self) -> None:
        """复位语音模式对话显示（进入/退出对话、回到待机时调用）。"""
        self._dialog_turn_id = 0
        self._dialog_prev = ""
        self._dialog_prev_role = ""
        self._dialog_prev_struck = False
        self._dialog_cur = ""
        self._dialog_cur_role = ""
        self._dialog_cur_final = False
        self._dialog_cur_struck = False
        self._dialog_expanded = False

    def _set_error(self, message: str) -> None:
        import time
        self._error_msg = message
        self._error_until = time.time() + 5.0
        self._error_timer.start(5050)
        self._sync_bubble()
        self.update()

    def _flash_hint(self, message: str, seconds: float) -> None:
        """临时提示：停留 seconds 秒后用默认样式恢复。"""
        import time
        self._flash_msg = message
        self._flash_until = time.time() + max(0.0, seconds)
        # 到期主动重绘一次：否则 IDLE 下无重绘源，提示文字会一直留在屏上
        self._flash_timer.start(int(max(0.0, seconds) * 1000) + 50)
        self._sync_bubble()
        self.update()

    def _on_flash_expired(self) -> None:
        self._flash_msg = ""
        self._sync_bubble()
        self.update()

    def _on_error_expired(self) -> None:
        self._error_msg = ""
        self._sync_bubble()
        self.update()

    # ---- 按钮事件 ----

    def _on_toggle_click(self) -> None:
        # 对话态：启停按钮=退出对话（等价对话热键）；录音语义不变
        if self._state in _DIALOG_STATES:
            self.signals.dialog_requested.emit(True)
            return
        self.signals.toggle_requested.emit()

    def _on_exit_click(self) -> None:
        # 右上角 ✕ = 最小化到托盘（真正的退出在右键菜单/托盘右键）
        self.signals.hide_requested.emit()

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        """永远置顶开关切换：立即切换窗口标志 + 同步内存配置 + 通知 app 持久化。"""
        self.set_always_on_top(checked)
        try:
            self.cfg.always_on_top = checked
        except Exception:
            logger.debug("同步内存配置 always_on_top 失败，忽略", exc_info=True)
        self.signals.always_on_top_toggled.emit(checked)
        self.signals.transient_hint.emit(
            t("hint.on_top.on") if checked else t("hint.on_top.off"), 1.5
        )

    def _on_interrupt_toggled(self, checked: bool) -> None:
        """左键打断开关切换：写配置 + 即时生效 + 短暂提示。"""
        from ..config.loader import update_config_field
        update_config_field("interrupt_on_click", "true" if checked else "false",
                            section="ui", value_type="bool")
        # 直接同步内存 cfg，避免等重载
        try:
            self.cfg.interrupt_on_click = checked
        except Exception:
            logger.debug("同步内存配置 interrupt_on_click 失败，忽略", exc_info=True)
        self.signals.transient_hint.emit(
            t("hint.interrupt_click.on") if checked else t("hint.interrupt_click.off"), 1.5
        )

    def _on_interrupt_delete_toggled(self, checked: bool) -> None:
        """删除键打断开关切换：写配置 + 通知 app 启停监听 + 短暂提示。"""
        from ..config.loader import update_config_field
        update_config_field("interrupt_on_delete", "true" if checked else "false",
                            section="ui", value_type="bool")
        # 直接同步内存 cfg，避免等重载
        try:
            self.cfg.interrupt_on_delete = checked
        except Exception:
            logger.debug("同步内存配置 interrupt_on_delete 失败，忽略", exc_info=True)
        self.signals.interrupt_delete_toggled.emit(checked)
        self.signals.transient_hint.emit(
            t("hint.interrupt_delete.on") if checked else t("hint.interrupt_delete.off"), 1.5
        )

    def _on_history_toggled(self, checked: bool) -> None:
        """历史对话剪贴板开关：写配置 + 即时生效（已有条目保留，仅停止新增）。"""
        from ..config.loader import update_config_field
        from ..core import history as _history
        _history.set_enabled(checked)
        update_config_field("enable", "true" if checked else "false",
                            section="history", value_type="bool")
        self.signals.transient_hint.emit(
            t("hint.history.on") if checked else t("hint.history.off"), 1.5
        )

    def _copy_history_item(self, text: str) -> None:
        """历史条目点击：复制全文到剪贴板。"""
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        self.signals.transient_hint.emit(t("hint.copied"), 1.2)

    def _on_clear_history(self) -> None:
        """清空全部历史条目。"""
        from ..core import history as _history
        _history.clear()
        self.signals.transient_hint.emit(t("hint.history_cleared"), 1.5)

    def _on_choose_font_file(self) -> None:
        """弹出文件对话框让用户选择 .ttf / .otf 字体文件。"""
        from PySide6.QtWidgets import QFileDialog
        from .fonts import _loaded_file_path
        start_dir = ""
        if _loaded_file_path:
            from pathlib import Path
            start_dir = str(Path(_loaded_file_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            t("hint.choose_font_file"),
            start_dir,
            t("hint.font_file_filter"),
        )
        if path:
            self.signals.font_file_changed.emit(path)

    # ---- 右键菜单 ----

    def contextMenuEvent(self, event) -> None:
        """右键弹出统一菜单。设置对话框打开期间不弹（模态场景下菜单会被
        对话框遮住/抢焦点）。exec 后必须 deleteLater：QMenu 不删会泄漏，
        累积后右键越来越卡。"""
        if getattr(self, "_settings_dlg_open", False):
            return
        menu = self.build_menu(self)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def build_menu(self, parent=None) -> QMenu:
        """构建统一右键菜单（委托 context_menu 模块，悬浮条与托盘共用）。"""
        from .context_menu import build_context_menu
        return build_context_menu(self, parent)

    # ---- 鼠标拖动与调整尺寸 ----

    def _in_resize_handle(self, pos) -> bool:
        """判断点是否落在右下角调整尺寸手柄区。"""
        return (self.width() - pos.x() <= RESIZE_HANDLE
                and self.height() - pos.y() <= RESIZE_HANDLE)

    def _on_edge(self, pos) -> bool:
        """点是否在边缘带（各 6px）：边缘只用于拖动窗口，不参与点击打断判定。"""
        band = 6
        return (pos.x() < band or pos.y() < band
                or self.width() - pos.x() <= band
                or self.height() - pos.y() <= band)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            local = event.position().toPoint()
            if self._in_resize_handle(local):
                # 进入调整尺寸模式：记录起点与原始尺寸
                self._resize_pos = event.globalPosition().toPoint()
                self._resize_start_size = QSize(self.width(), self.height())
                event.accept()
                return
            # 记录按下位置供释放时区分"点击"与"拖动"；按钮区由 QPushButton 自己消费
            self._press_pos = local
            # 点击判定用全局坐标差：拖动时窗口跟着鼠标走，局部差永远很小，判不出拖过
            self._press_global = event.globalPosition().toPoint()
            self._press_on_edge = self._on_edge(local)
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            # 非左键必须走 super()，否则 Qt 不派发 contextMenuEvent，右键菜单不弹
            super().mousePressEvent(event)

    def _is_on_button(self, local: QPoint) -> bool:
        """判断点击位置是否落在按钮上（含 4px 容差，按钮被切角时也算）。"""
        for btn in (self._btn_toggle, self._btn_exit):
            if btn is None:
                continue
            if btn.geometry().adjusted(-2, -2, 2, 2).contains(local):
                return True
        return False

    def mouseMoveEvent(self, event) -> None:
        if self._resize_pos is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._resize_pos
            if self._voice_mode:
                # 声纹主题锁定正方形：宽高取同一值
                side = max(VOICE_MIN_SIDE,
                           max(self._resize_start_size.width() + delta.x(),
                               self._resize_start_size.height() + delta.y()))
                self.resize(side, side)
            else:
                # 凯尔特主题最小高度抬高：链带 + 启停按钮必须完整容纳
                min_h = self._knot_min_height() if self._knot_icon_mode else MIN_H
                new_w = max(MIN_W, self._resize_start_size.width() + delta.x())
                new_h = max(min_h, self._resize_start_size.height() + delta.y())
                self.resize(new_w, new_h)
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        # 悬停时光标如实反映操作：仅右下角手柄区显示缩放箭头
        if event.buttons() == Qt.NoButton:
            self.setCursor(
                Qt.SizeFDiagCursor
                if self._in_resize_handle(event.position().toPoint())
                else Qt.ArrowCursor
            )

    def mouseReleaseEvent(self, event) -> None:
        # 拖动或调整尺寸结束，持久化窗口状态
        if self._drag_pos is not None or self._resize_pos is not None:
            _save_window_state(self.x(), self.y(), self.width(), self.height())
        # 点击判定：全局按下->释放位移 < 5px 才算点击（拖动过窗口不算）
        press_global = getattr(self, "_press_global", None)
        moved = (press_global is None or
                 (event.globalPosition().toPoint() - press_global).manhattanLength() >= 5)
        if (self._voice_mode and event.button() == Qt.LeftButton
                and self._press_pos is not None and self._resize_pos is None
                and not moved
                and not self._in_resize_handle(event.position().toPoint())
                and not self._btn_exit.geometry().adjusted(-2, -2, 2, 2).contains(
                    event.position().toPoint())):
            # 极简主题：整窗即启停按钮，点击切换录音（对话态=退出对话）
            if self._state in _DIALOG_STATES:
                self.signals.dialog_requested.emit(True)
            else:
                self.signals.toggle_requested.emit()
        # 点击打断：按下位置几乎未动、不在按钮/手柄上才打断。文字预览区不再豁免
        #（它占了大半个悬浮条，豁免会让打断形同虚设）。声纹主题整窗已绑启停，不适用。
        elif (not self._voice_mode
                and self._press_pos is not None and event.button() == Qt.LeftButton
                and getattr(self.cfg, "interrupt_on_click", False)
                and self._state in (UI_LISTENING, UI_RECOGNIZING)
                and not moved
                and not self._is_on_button(event.position().toPoint())
                and not self._in_resize_handle(event.position().toPoint())
                and not getattr(self, "_press_on_edge", False)):
            self.signals.interrupt_requested.emit()
        self._drag_pos = None
        self._resize_pos = None
        self._press_pos = None
        self._press_global = None
        self._press_on_edge = False

    def enterEvent(self, event) -> None:
        """进入窗口时按位置切换光标，并暂停自动隐藏。"""
        self._mouse_inside = True
        if self._auto_hide_ms > 0:
            self._auto_hide_timer.stop()
        if self._voice_mode and not self._hover:
            self._hover = True
            self.update()
        if self._in_resize_handle(event.position().toPoint()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event) -> None:
        """离开窗口后重启自动隐藏倒计时（录音中除外）。"""
        self._mouse_inside = False
        if self._voice_mode and self._hover:
            self._hover = False
            self.update()
        self._restart_auto_hide()

    def eventFilter(self, obj, event) -> bool:
        """退出按钮 Enter/Leave：在淡色与提亮图标间切换（红色背景由 QSS 管）。"""
        if obj is self._btn_exit:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.Type.Enter and self._close_icon_bright:
                self._btn_exit.setIcon(self._close_icon_bright)
                return False
            if event.type() == QEvent.Type.Leave and self._close_icon_dim:
                self._btn_exit.setIcon(self._close_icon_dim)
                return False
        return super().eventFilter(obj, event)

    def _restart_auto_hide(self) -> None:
        """录音中或未启用时不隐藏；其余情况重置倒计时。"""
        if self._auto_hide_ms <= 0:
            return
        if self._state in (UI_LISTENING, UI_RECOGNIZING):
            self._auto_hide_timer.stop()
            return
        if self._mouse_inside:
            return
        self._auto_hide_timer.start(self._auto_hide_ms)

    def closeEvent(self, event) -> None:
        """关闭窗口（Alt+F4 等）改为隐藏到托盘，程序驻留。"""
        event.ignore()
        self.signals.hide_requested.emit()

    def hideEvent(self, event) -> None:
        """隐藏到托盘时持久化窗口位置和尺寸。"""
        _save_window_state(self.x(), self.y(), self.width(), self.height())
        # 气泡一并收起（声纹主题；下次呼出后由录音状态重新驱动弹出）
        if getattr(self, "_bubble", None) is not None:
            self._bubble.hide()
        self.hide_llm_wait_confirm()
        # 不可见时 spinner 无意义，停表省空转；重新 show() 时若仍在加载则恢复
        self._stop_spinner()
        # 像素待机动画同理：隐藏时停表，别让 100ms 定时器在托盘里空转
        self._sync_pixel_idle_animation()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """重新显示（托盘呼出）：恢复像素待机动画。"""
        super().showEvent(event)
        self._sync_pixel_idle_animation()

    # ---- 像素主题待机动画 ----

    def _sync_pixel_idle_animation(self) -> None:
        """按当前主题/状态/可见性启停像素五柱动画（待机呼吸/活跃抖动共用）。

        非像素主题下是 no-op（特性模块可能被 lite 构建裁掉，导入失败即跳过）；
        动画细节全在 theme_features.pixel 内，这里只负责在正确的时机叫它。
        """
        if not self._pixel_mode:
            return
        feat = get_feature("pixel")
        if feat is None or not hasattr(feat, "sync_pixel_animation"):
            return
        feat.sync_pixel_animation(self)

    def _stop_pixel_idle_animation(self) -> None:
        """无条件停表（切离像素主题时调用；特性模块缺失则无需处理）。"""
        feat = get_feature("pixel")
        if feat is None or not hasattr(feat, "stop_pixel_animation"):
            return
        feat.stop_pixel_animation(self)

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setBrush(self._bg_color)
        if self._voice_mode:
            # 像素主题：阶梯边框面板 + 像素均衡器 + 状态块（布局同极简，皮肤换像素）
            feat_pixel = get_feature("pixel") if self._pixel_mode else None
            if feat_pixel is not None:
                feat_pixel.draw_panel(painter, self)
                feat_pixel.draw_pattern(painter, self)
            else:
                # 极简主题：圆角方形面板，图标居中，右下角留缩放手柄
                bg = QColor(self._bg_color)
                if self._hover:
                    bg = bg.lighter(118)
                painter.setBrush(bg)
                painter.setPen(Qt.NoPen)
                radius = 12
                painter.drawRoundedRect(rect, radius, radius)
                self._draw_voice_print(painter)
            if self._llm_wait_active:
                self._draw_llm_confirm(painter)
            self._draw_resize_handle(painter)
            painter.end()
            return
        # 描边替代 QGraphicsDropShadowEffect（分层窗口下阴影会造成更新失败）；
        # 浅色淡黑边/深色淡亮边；主题专属描边委托特性模块
        feat_cyber = get_feature("cyberpunk") if self._top_line_color is not None else None
        feat_celtic = get_feature("celtic") if self._knot_icon_mode else None
        if feat_cyber is not None:
            # 赛博朋克：细霓虹边 + 左下切角
            feat_cyber.draw_frame(painter, rect, self)
        elif feat_celtic is not None:
            # 凯尔特只画透明边框 pixmap，得先自己填背景，否则整条透明
            painter.fillRect(rect, self._bg_color)
            feat_celtic.draw_border(self, painter)
        else:
            if self._border_color is not None:
                border = self._border_color
            else:
                light = self._is_light(self._bg_color.name())
                border = QColor(0, 0, 0, 22) if light else QColor(255, 255, 255, 28)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(rect, 14, 14)

        # 顶部霓虹横线（仅指定了 top_line 的主题显示）
        if feat_cyber is not None:
            feat_cyber.draw_top_line(painter, self)

        self._draw_status_dot(painter)
        # 确认提示激活时画确认文字，否则画常规文本
        if self._llm_wait_active:
            self._draw_llm_confirm(painter)
        else:
            self._draw_text(painter)
        self._draw_resize_handle(painter)

    def _draw_voice_print(self, painter: QPainter) -> None:
        """极简主题中央声纹图案：原图棋盘底已剥离，线条统一米白，面板内居中。

        加载态不在此画 spinner（改画在上方的 loading 气泡里），声纹保持纯图案。
        """
        side = min(self.width(), self.height())
        # 声纹图标实心与否：录音中或对话中（活跃会话）
        recording = (self._state in (UI_LISTENING, UI_RECOGNIZING)
                     or self._state in _DIALOG_STATES)
        if side < 16:
            return
        # 图标边长：面板内约占 72%，四周留出呼吸空间
        art = int(side * 0.72)
        if art < 16:
            art = side - 8
        # 长按准备中：声纹布局（极简）整窗即按钮、条上不画状态点，中央声纹是唯一
        # 能表达状态的部位。保持待机**空心**轮廓（实心=录音，借用了就成了"红一下
        # 又消失像出错"），只把取色换成与状态点同源的暗红（spec §3.2.1）
        armed = self._state == UI_ARMED
        pix = voice_icon(recording, art,
                         DOT_COLOR_MAP[UI_ARMED] if armed else "#eeeeee")
        # 录音中轻微呼吸缩放（电平驱动，幅度小不刺眼）
        scale = 1.0
        if recording:
            scale = 0.97 + 0.03 * self._level
        w = int(art * scale)
        x = (self.width() - w) / 2
        y = (self.height() - w) / 2
        painter.drawPixmap(int(x), int(y), w, w, pix)

    def _sync_bubble(self) -> None:
        """声纹主题气泡内容同步：错误 > 临时提示 > 识别文本 > 聆听提示；待机隐藏。

        非声纹主题为空操作；错误/临时提示待机时也可短暂弹出。"""
        import time
        bubble = getattr(self, "_bubble", None)
        if bubble is None:
            return
        # 非声纹主题：录音文本/错误/提示都画在条上，气泡只在语音模式出场
        #（语音模式下所有主题统一用气泡承载内容，见 _draw_text 的分流）
        if not self._voice_mode and self._state not in _DIALOG_STATES:
            if bubble.isVisible():
                bubble.hide()
            return
        now = time.time()
        # 确认提示优先级最高；极简主题小方块放不下文字，完整显示在气泡里
        if self._llm_wait_active and self._llm_wait_text:
            # marquee=True：长确认文本在气泡内循环滚动（极简主题小方块放不下）
            bubble.show_message(self._llm_wait_text, self._text_color, marquee=True)
            return
        if self._error_msg and now < self._error_until:
            bubble.show_message(self._error_msg, QColor("#e8a83c"))
            return
        # 语音模式：两行字幕式卡片（上=上一轮，角色不限；下=当前轮）。
        # 刻意排在临时提示**之前**：对话内容不该被一条瞬时提示清屏
        # （"已复制本轮"/"外放可能有回声"这类），提示改画成卡片底部一行小字。
        if self._state in _DIALOG_STATES:
            # H71-W2：只有**极简主题**把「思考中…」画进气泡。极简悬浮条是一整块
            # 声纹按钮（paintEvent 在 _voice_mode 下直接 return，条上不画状态文字），
            # 用户看不见思考态；其它主题条内 _hint 照旧显示「思考中...」，行为不变。
            thinking = bool(self._voice_mode) and self._state == DIALOG_UI_THINKING
            rows = self._dialog_rows()
            if rows:
                bubble.show_dialog(
                    rows,
                    expanded=self._dialog_expanded,
                    streaming=self._state == DIALOG_UI_SPEAKING,
                    toast=self._active_flash(now),
                    thinking=thinking,
                )
                return
            # 本轮还没有任何内容：临时提示 > 思考态 > 状态提示
            if self._flash_msg and now < self._flash_until:
                bubble.show_message(self._flash_msg, self._text_color)
            elif thinking:
                # 还没有任何轮次文本可挂靠时，思考态单独占一行气泡。
                # 文案键取气泡侧常量（THINKING_TEXT_KEY），与卡片里的思考行同源；
                # 值是调用点 t() 取的——常量只存键，导入期求值会焊死当时的语言。
                hint_color = QColor(self._text_color)
                hint_color.setAlpha(190)
                bubble.show_message(t(SpeechBubble.THINKING_TEXT_KEY), hint_color)
            elif self._hint:
                hint_color = QColor(self._text_color)
                hint_color.setAlpha(190)
                bubble.show_message(self._hint, hint_color)
            elif bubble.isVisible():
                bubble.hide()
            return
        if self._flash_msg and now < self._flash_until:
            bubble.show_message(self._flash_msg, self._text_color)
            return
        # 本地模型加载（极简主题）：气泡显示"转圈 + 提示文字"，随 tick 旋转。
        # 仅 IDLE 时显示（录音中让位给录音内容）；悬浮条隐藏时不弹（随 show 恢复）
        if self._loading and self._state == UI_IDLE:
            if self.isVisible():
                bubble.show_loading(t("hint.model_loading"), self._text_color,
                                    self._spinner_angle)
            return
        if self._state in (UI_LISTENING, UI_RECOGNIZING):
            if self._text:
                if self._text_is_final:
                    # 会话累积全文（多句 final 滚动拼接，不逐段跳变）。
                    # elide="left" 与中间结果同向（保留尾部）：定稿翻回头部会让
                    # 用户正读的尾部字当场消失、画面钉在开头"不会往后挪"
                    # （用户反馈「还是显示三个点儿，然后不会往后挪」）——与多行
                    # 卡片 H61-B「定稿不翻面」同一语义，省略号在头部表示旧文省略。
                    bubble.show_message(self._session_text, self._text_color,
                                        elide="left")
                else:
                    # 中间结果 = 累积 + 当前句（引擎发全量则以累积开头直接用），
                    # 半透明表示未定稿。
                    # elide="left"：文字超出气泡宽度时**保留尾部**——用户说话时
                    # 最关心"我刚说的字有没有被识别对"，保留头部（旧内容）会让
                    # 新说的字直接看不见（用户反馈「后面说的文字它就不显示了，
                    # 应该以最新显示的为主，把前面旧的省略掉」）。
                    # 尾部**不再加文字 "..."**：未定稿统一由绘制层的光标表达
                    # ——像素主题=金色 ▼（DQ 式），其他主题=竖线光标（| 闪烁），
                    # 见 bar_widgets 的单行绘制分支。两个标记叠在一起语义重复。
                    interim = QColor(self._text_color)
                    interim.setAlpha(160)
                    # 局部名不用 `t`：本模块顶部 import 了 i18n 的 t()，凡是在本函数里
                    # 直接调 t("...") 的分支都会被这个局部名遮蔽成 UnboundLocalError。
                    cur_txt, acc = self._text, self._session_text
                    combined = (cur_txt if (acc and cur_txt.startswith(acc))
                                else acc + cur_txt)
                    bubble.show_message(combined, interim, elide="left")
            else:
                hint_color = QColor(self._text_color)
                hint_color.setAlpha(190)
                bubble.show_message(self._hint or t("hint.listening"), hint_color)
            return
        if bubble.isVisible():
            bubble.hide()

    def _draw_resize_handle(self, painter: QPainter) -> None:
        """右下角绘制淡淡的斜纹手柄，提示可拖拽调节尺寸。"""
        # 凯尔特主题四角为凯尔特结角饰，右下角不再叠加斜纹手柄
        if self._knot_icon_mode:
            return
        from PySide6.QtGui import QPolygonF
        handle_color = QColor(self._text_color)
        # 浅色背景需要更高 alpha 才可见
        light = self._is_light(self._bg_color.name())
        handle_color.setAlpha(90 if light else 50)
        painter.setBrush(handle_color)
        painter.setPen(Qt.NoPen)
        w = self.width()
        h = self.height()
        tri = QPolygonF([
            QPointF(w - 2, h - RESIZE_HANDLE),
            QPointF(w - 2, h - 2),
            QPointF(w - RESIZE_HANDLE, h - 2),
        ])
        painter.drawPolygon(tri)

    def _status_color(self) -> QColor:
        """当前状态的颜色（状态点/像素状态块共用）。"""
        key = DOT_COLOR_MAP.get(self._state, "accent_dim")
        if key == "accent":
            return self._accent_color
        if key == "accent_dim":
            return self._accent_dim
        return QColor(key)

    def _draw_status_dot(self, painter: QPainter) -> None:
        cx = self._dot_x + DOT_D / 2
        cy = self.height() / 2

        # 模型加载中（且待机）：状态点位置改画旋转 spinner。守卫带 UI_IDLE：
        # 预热未完成用户就开始录音时，红点/光晕正常显示，加载指示不吞录音指示。
        # 画布放大至状态点 2 倍（20px）以状态点中心为圆心居中：常规主题下距启停
        # 按钮与文字各留 ~7px，不越界不重叠（offscreen 截图验证）。不透明强调色，
        # 比 IDLE 态半透明状态点更醒目；加载态不画光晕。
        if self._loading and self._state == UI_IDLE:
            d = DOT_D * 2
            pix = spinner_pixmap(d, self._accent_color.name(), self._spinner_angle)
            painter.drawPixmap(int(cx - d / 2), int(cy - d / 2), pix)
            return
        # 对话思考态：同一位置的 spinner，颜色用该态点色（蓝），转圈=等模型
        if self._state == DIALOG_UI_THINKING:
            d = DOT_D * 2
            color_hex = DOT_COLOR_MAP[DIALOG_UI_THINKING]
            pix = spinner_pixmap(d, color_hex, self._spinner_angle)
            painter.drawPixmap(int(cx - d / 2), int(cy - d / 2), pix)
            return

        key = DOT_COLOR_MAP.get(self._state, "accent_dim")
        color = self._status_color()

        light = self._is_light(self._bg_color.name())

        # 录音会话中：呼吸光晕，颜色跟随当前状态点（聆听/识别=红；对话播报=紫）
        if self._state in (UI_LISTENING, UI_RECOGNIZING, DIALOG_UI_SPEAKING):
            phase = self._pulse_phase
            glow_r = 5 + phase * 11
            # 浅色背景上光晕更柔，避免红色过曝
            base_alpha = 110 if light else 150
            glow_alpha = int((1 - phase) * base_alpha)
            glow_color = QColor(color)
            glow_color.setAlpha(glow_alpha)
            painter.setBrush(glow_color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r)
        elif light:
            # 浅色背景下静态点也加一层柔光晕，避免点显得突兀
            halo = QColor(color)
            halo.setAlpha(45)
            painter.setBrush(halo)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), 8, 8)

        # 对话聆听态：点大小随麦克风电平跳（声纹感，1.5~2 倍直径）
        if self._state == DIALOG_UI_LISTENING:
            r = DOT_D / 2 * (1.0 + self._level)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), r, r)
            return
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(self._dot_x, self.height() // 2 - DOT_D // 2, DOT_D, DOT_D)

    def _draw_text(self, painter: QPainter) -> None:
        import time

        # 字体走 get_font()（自定义或 Qt 默认），字号取 ui.font_size_bar（默认 13）
        size = int(getattr(self.cfg, "font_size_bar", 13) or 13)
        font = get_font(size)
        painter.setFont(font)

        left = self._dot_x + DOT_D + DOT_TEXT_GAP
        right = self._btn_exit.x() - 8
        max_width = right - left
        if max_width <= 0:
            return

        metrics = QFontMetrics(font)

        # 语音模式：内容（含错误与临时提示）全部交给上方气泡，条内只留状态提示。
        # 条内文本区实测只有约 210px（左侧状态点 82px + 右侧退出按钮 34px），
        # 一行仅容 ~16 个汉字 —— 放不下"两条消息 + 角色区分"。条的角色是
        # 状态与控制，内容交给能容纳它的容器（气泡对任何主题都可用）。
        if self._state in _DIALOG_STATES:
            hint = self._hint or ""
            if hint:
                painter.setPen(self._text_color)
                painter.drawText(
                    left, 0, max_width, self.height(),
                    Qt.AlignVCenter | Qt.AlignLeft,
                    elide_right(metrics, hint, max_width),
                )
            return

        if self._flash_msg and time.time() < self._flash_until:
            painter.setPen(self._text_color)
            elided = elide_right(metrics, self._flash_msg, max_width)
            painter.drawText(
                left, 0, max_width, self.height(),
                Qt.AlignVCenter | Qt.AlignLeft, elided,
            )
            return

        if self._flash_msg and time.time() >= self._flash_until:
            self._flash_msg = ""

        if self._error_msg and time.time() < self._error_until:
            painter.setPen(QColor("#e8a83c"))
            elided = elide_right(metrics, self._error_msg, max_width)
            painter.drawText(
                left, 0, max_width, self.height(),
                Qt.AlignVCenter | Qt.AlignLeft, elided,
            )
            return

        if self._error_msg and time.time() >= self._error_until:
            self._error_msg = ""

        painter.setPen(self._text_color)
        text = self._text if self._text else self._hint
        if not text:
            return

        is_interim = False
        if not self._text:
            # 提示文本（非识别结果）：全不透明，右截断
            painter.setPen(self._text_color)
            elided = elide_right(metrics, text, max_width)
        elif self._text_is_final:
            # 最终识别结果：全不透明；说话场景尾部才是新内容，ElideLeft
            painter.setPen(self._text_color)
            elided = elide_left(metrics, text, max_width)
        else:
            # 中间结果：半透明，ElideLeft 显示最新字词。
            # 尾部**不再加 "..."**——改用竖线光标表达未定稿（与气泡里的
            # 竖线光标一致，像素主题的气泡则用金色 ▼）。用户反馈
            # 「其他主题尾部可以改成竖线光标闪烁的那种」。
            interim_color = QColor(self._text_color)
            interim_color.setAlpha(160)
            painter.setPen(interim_color)
            elided = elide_left(metrics, text, max_width)
            is_interim = True
        painter.drawText(
            left, 0, max_width, self.height(),
            Qt.AlignVCenter | Qt.AlignLeft, elided,
        )
        if is_interim:
            # 竖线光标：闪烁相位复用红点呼吸的 _pulse_timer（录音/识别期间
            # 一直在跑，1.2s 周期 → phase<0.5 即 0.6s 亮 / 0.6s 灭，标准光标节奏）
            tw = metrics.horizontalAdvance(elided)
            cx = left + tw + 6
            if cx + 4 <= left + max_width:
                draw_caret_marker(
                    painter, cx, self.height() / 2,
                    min(metrics.height(), self.height() - 12),
                    self._pulse_phase < 0.5, interim_color)


