"""主程序：状态机与模块集成。

实时流式识别，WebSocket 逐字同步上屏。

状态流转：
  IDLE --热键/按钮--> LISTENING (采集 + 识别)
  LISTENING --热键/按钮--> IDLE (停止)
  LISTENING --无语音超时--> IDLE (自动停止)
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

# sounddevice/numpy/websockets 及 ASR 引擎模块不在启动时导入：合计约 330ms
#（冷启动还被杀软扫描放大），且仅录音时需要。统一在使用处延迟导入，窗口尽快可交互。
from ..config.loader import (
    find_config_path,
    get_config_unchecked,
    is_configured,
)
from ..steam_integration import StatsTracker, get_integration, is_steam_build
from ..hotkey import probe as _hold_probe
from ..hotkey.hold import HoldAction, HoldTracker, hold_flag_enabled
from ..hotkey.listener import HotkeyListener
from ..i18n import t
from ..input.injector import TextInjector
from ..msgbox import show_error
from ..ui.floating_bar import FloatingBar, UI_ARMED, UI_IDLE, UI_LISTENING
from ..ui.icons import app_icon
# 历史对话剪贴板（同包 stdlib-only 模块，无重依赖，可放心顶层导入）
from . import history as _history
# LLM 修正替换链路（同包轻模块，无 Qt/重依赖）
from . import llm_replace
# ASR 会话与音频管线（同包轻模块，引擎类仍各自延迟导入）
from . import asr_pipeline

logger = logging.getLogger(__name__)

APP_STATE_IDLE = "idle"
APP_STATE_LISTENING = "listening"
APP_STATE_STOPPING = "stopping"

# 语音命令 App 侧处理器（同包轻模块，命令路由/待命/AI/开关）
# 延迟到常量定义之后导入：voice_command_handlers 顶层 from .app import APP_STATE_*
from . import voice_command_handlers
from . import settings_handlers

# 语音命令的默认触发词 / AI 前缀兜底值。
# 与 config/defaults.py 的 commands.prefix / ai_prefix 同源 —— 这是「用户要
# 说出的词」而不是界面文案：翻成英文会让兜底值与配置文件里的默认值不一致
# （用户在设置页看到的默认值反而不生效）。理由同 core/voice_commands.py。
DEFAULT_CMD_PREFIX = "听我说"      # noqa: i18n
DEFAULT_CMD_AI_PREFIX = "帮我"     # noqa: i18n

LEVEL_NORMALIZE = 3000.0

# 单次录音硬超时（秒）：超过即强制停止，兜底防底噪贴阈值导致"永远停不下来"
MAX_SESSION_SECONDS = 60

# ASR 后台建连期间的音频预缓冲上限（块，100ms/块 -> 20 秒），
# 防御性封顶：建连异常挂起时不会无限吃内存
PENDING_AUDIO_MAX_BLOCKS = 200

# ASR 建连瞬时超时自动重试：网络抖动会自愈，重试几乎必成；配置类错误不重试。
# 重试期间会话保持聆听，音频进预缓冲，连上后补发不丢字。
ASR_CONNECT_RETRY_MAX = 3
ASR_CONNECT_RETRY_DELAY = 2.0


# 建连瞬时错误判定与火山 resource_id 合成已随管线搬入 asr_pipeline

# 预滚缓冲（100ms/块）：覆盖热键呼出 0.15s 和麦克风启动耗时，防吞首字
PREROLL_BLOCKS = 4

# 「延后 toggle 的收尾」窗口（毫秒）。悬浮条隐藏时，越过阈值的正式开始走
# _on_toggle → _delayed_toggle，延后 150ms 才真正 _toggle；用户若在这 150ms 里
# 松手，END 分支看到的状态还是待机（于是不 toggle），而那次延后的开始随后照样
# 落地——开出一段谁也没要的录音，只有 10 秒静音超时能收场，且这条路径会冲刷
# final，附近的说话内容可能被上屏。取值必须**大于**那个 150ms 延后（否则定时器
# 会在开始落地之前空跑一趟），又远小于任何有意义的录音时长。
PENDING_START_CLEANUP_MS = 200

# 长按 + AI 修正的「收尾窗口」（毫秒）：松手后 ASR 引擎的尾句 final 是异步冲刷
# 回来的（stop() 之后仍可能吐一帧），而整段送修只发生一次——不等这一下，用户刚
# 说的最后半句就进不了这次修正。窗口取「听得见的最小值」：够网络引擎冲刷一帧，
# 又短到松手到上屏的额外延迟不易察觉。
HOLD_LLM_COLLECT_MS = 300

# 底噪校准：头 1 秒采样，阈值=中位数×1.8，封顶配置阈值×3（防校准期说话抬过头）
NOISE_CALIB_BLOCKS = 10
NOISE_CALIB_FACTOR = 1.8
NOISE_CALIB_MAX_RATIO = 3.0


class VoiceApp(QObject):
    """语音输入应用主控。"""

    # LLM 修正完成 → 主线程执行替换注入（后台线程 emit，自动 queued 到主线程）
    llm_replace_requested = Signal()

    # 长按说话：热键线程 → 主线程的「按下了」通知（同上，Qt 跨线程自动排队）。
    # 为什么要绕这一道：QObject 有线程亲和性，QTimer 只有在主线程创建并启动才会被
    # Qt 调度；热键回调跑在 hotkey-native（或 keyboard 钩子）线程上，就地建表得到的
    # 是「启动失败、永不触发」的定时器（实测报 QObject::startTimer: current thread's
    # event dispatcher has already been destroyed，isActive() 为 False），状态机随之
    # 永远停在 ARMED——热键从此按下去毫无反应，且只在日志里留一行异常。
    hold_press_requested = Signal()

    # 发行层默认引擎：实例属性在 __init__ 里按发行层判定决定；
    # 类级默认 None 保证 __new__ 构造的轻量对象（测试假 app）热加载路径可读
    _default_engine: str | None = None

    def __init__(self, config, start_hidden: bool = False):
        super().__init__()
        self.cfg = config
        # 静默启动：仅托盘常驻；热键/托盘唤出悬浮条后再录音
        self._start_hidden = start_hidden
        # 发行层默认引擎（商店版 funasr）；首启与热加载共用同一默认值
        self._default_engine: str | None = "funasr" if is_steam_build() else None
        self.state = APP_STATE_IDLE
        self.auto_stop_seconds = getattr(config.recording, "auto_stop_seconds", 5)
        self._lock = threading.Lock()
        self._last_voice_time = 0.0

        # LLM 修正替换信号 → 槽（跨线程 queued；不能用 QMetaObject.invokeMethod，
        # 它对 Python 类不可靠）
        self.llm_replace_requested.connect(self._do_llm_replace)
        # 长按说话：热键线程 emit → 主线程 _on_hotkey_press（与上面 LLM 一条路数）
        self.hold_press_requested.connect(self._on_hotkey_press)

        # 会话计时与底噪校准状态
        self._session_start_time = 0.0   # 本次录音开始时间（硬超时判据）
        self._calib_energies = None      # 底噪校准采样缓冲，None=非校准期
        # ASR 后台建连期间的音频预缓冲（先录音后建连，避免开头丢字）
        self._pending_audio = []
        # 预滚缓冲：待机期持续保留最近几块音频，录音时回灌，防吞首字
        self._preroll: deque = deque(maxlen=PREROLL_BLOCKS)
        self._last_block_time = 0.0      # 最近一次收到音频块的时刻（判流健康）
        self._capture_started_at = 0.0   # 采集流本次启动时刻
        self._capture_lock = threading.Lock()
        # 丢弃标记（语义：**停止后不要再打新字**）：本会话未上屏结果全丢（含
        # 排队中的注入、引擎停连时冲刷的残帧），停止即硬切断；新会话自动复位。
        # 注意它**不是**「用户不要这段结果了」——手动停止（热键再按）一样会置位
        # （见 asr_pipeline.stop_session），所以凡是「真打断才该放弃结果」的判定
        # （LLM 修正上屏/替换）必须判 _session_interrupted，不能用本标记
        self._discard_results = False
        # 真打断标记（语义：**用户明确不要这段结果**）：只在删除键打断、左键
        # 点击打断两条路径置位，新会话复位。与 _discard_results 分开的原因：
        # 后者在手动停止时必然置位，而「修正后上屏」的 AI 回包必然晚于停止 ——
        # 用前者判会把用户刚说完的话整段吃掉（无提示、历史也查不到）
        self._session_interrupted = False
        # 自动停止挂起标记（静音超时/时长上限置位，stop_session 读后清除）：
        # 自动停止放行引擎冲刷 final——停止时在途的尾句 final（各引擎停止
        # 冲刷的残留句）放行上屏，硬切断会把用户刚说的话整句丢掉
        self._auto_stop_pending = False
        # 会话序号，每次录音递增；LLM 修正替换比对序号，防旧会话回调误删新会话文字
        self._session_seq = 0
        # 最近 FINAL 注入时刻：修正期间又有新句上屏则光标后移，退格会删错，替换时检测到即取消
        self._last_final_at = 0.0
        # 最近一次上屏时的前台窗口句柄：LLM 修正替换前校验焦点未移走
        self._inject_hwnd = 0
        # 热加载防抖状态：_pending_config_path 待重载的配置路径
        self._pending_config_path: str | None = None
        self._config_reload_timer: QTimer | None = None

        self.capture: AudioCapture | None = None
        self.vad: VAD | None = None
        self.asr: TencentRealtimeASR | None = None
        self.injector = TextInjector(
            method=config.injection.method,
            paste_delay_ms=config.injection.paste_delay_ms,
            keep_clipboard=getattr(config.injection, "keep_clipboard", True),
            game_mode=bool(getattr(config.injection, "game_mode", False)),
        )
        # 暂停播放/全局静音执行器（录音开始/停止时中断与恢复后台音频）
        from ..audio.media_pause import MediaPauser
        self.media_pauser = MediaPauser()
        self.bar: FloatingBar | None = None
        self.hotkey: HotkeyListener | None = None
        # 长按说话（可选）：状态机 + 采样定时器；关闭时两者都不创建
        self._hold_tracker: HoldTracker | None = None
        self._hold_timer: QTimer | None = None
        self._hold_vk = 0            # 主键虚拟键码（探针用）
        self._hold_active = False    # 长按生效中（asr_pipeline 据此豁免自动停止）
        # 当前会话是本次长按起的：只有它归长按收尾。别的路径（录音按钮/托盘菜单/
        # 语音命令）起的会话与长按无关，长按绝不能替它启停
        self._hold_owns_session = False
        # 「延后 toggle 还没落地」标记：悬浮条隐藏时越过阈值的正式开始要等 150ms
        # 才真正发生（_on_toggle → _delayed_toggle）。置位 = 本次长按请求的开始仍
        # 在半路上；用户在这段窗口里松手时，END 分支据此起一次性定时器把它收掉
        # （见 _arm_pending_start_cleanup 与 PENDING_START_CLEANUP_MS）
        self._hold_pending_start = False
        self._pending_start_timer: QTimer | None = None
        self.cmd_hotkey: HotkeyListener | None = None  # 命令待命热键（语音命令开启时注册）
        self.delete_listeners: list[HotkeyListener] = []  # 删除键打断监听（可选）
        self._app: QApplication | None = None
        self._config_watcher: QFileSystemWatcher | None = None
        self._config_path = None
        self._tray: QSystemTrayIcon | None = None
        # 平台 SDK 能力层单例 + 500ms 回调泵（仅 商店版且初始化成功时起）
        self._steam = None
        self._steam_timer: QTimer | None = None
        # 统计累计（只记数字，不记文本）：字数在 _on_asr_result、时长在 _stop_session
        self._steam_stats = StatsTracker()
        # 已计过时长的会话起点，用于去重（同一次会话重复 stop 只算一次）
        self._steam_session_counted_at = 0.0
        self._funasr_instance = None  # 预热的 FunASR 实例（供复用，含加载锁）
        self._funasr_gen = 0  # 预热代号：参数变更弃旧起新时递增，旧线程完成后静默退出
        self._llm_corrector = None   # LLM 修正器（cfg.llm.enable=True 时创建）
        self._wait_for_correction = False  # True=「修正后上屏」：final 不立即上屏，等修正完成后 inject
        # 逐字同步逻辑锁：AI 修正/剪贴输入/语音命令/游戏模式强制关闭；
        # 下面字段记锁定前的偏好，解除时恢复（None=无记忆）
        self._live_pref_locked: bool | None = None        # AI 修正锁
        self._live_pref_locked_paste: bool | None = None  # 剪贴输入锁
        self._live_pref_locked_commands: bool | None = None  # 语音命令锁
        self._live_pref_locked_game: bool | None = None      # 游戏模式锁
        # 游戏模式注入方式锁：记锁定前的注入方式（None=无记忆，如开启时本就是剪贴输入）
        self._method_pref_locked_game: str | None = None
        # 管理员权限差警告只提示一次（UIPI：目标游戏管理员运行时本进程合成输入被丢弃）
        self._elev_gap_warned = False
        # conflict_live 确认回答时待处理的冲突源（["llm","paste","commands","game"]
        # 子集；确认前写入，执行/取消后清空）
        self._pending_live_conflicts: list | None = None
        # 语音命令待命态：单说触发词后等下一句 final 当命令执行，超时自动取消
        self._command_standby = False
        # 命令热键启动录音：会话建立后自动进入待命态（免说触发词）
        self._standby_start = False
        # 最近一次上屏的 final 文本（"删除那句"的删除基准）
        self._last_spoken_text = ""
        # wait 模式「发送」的延迟回车：等修正全部上屏再回车，避免发空消息
        self._deferred_enter = False
        # feed 时的 wait 模式按 requested_at 关联，防热切换 _wait_for_correction 后 job 走错分支
        self._feed_context: dict[float, bool] = {}
        # 长按 + AI 修正：按住期间只累积文本（不上屏、不送修正），松手后等一个
        # 极短收尾窗口（引擎冲刷尾句）再**整段一次性**送 AI，修正返回后上屏
        # ——用户诉求「松开长按直接送 AI 处理然后返回，而不是长按状态下等待返回」
        self._hold_llm_collect = False      # 收尾窗口开启中（松手后短窗口内为 True）
        self._hold_llm_texts: list[str] = []  # 本轮长按待送修的 final（按时序）
        self._hold_llm_timer: QTimer | None = None
        # 整段提交批次的 requested_at：回调时据此标记 job，会话切换时降级注入
        # 原文（原文从未上屏，直接丢弃等于把用户说的话吃掉）
        self._hold_llm_batch_ts: set[float] = set()
        # 修正替换队列：回调 append、主线程槽 pop(0) 逐条处理。单槽变量不行，
        # 两个回调同时完成会互相覆盖，丢句子
        self._pending_llm_replace: list = []
        self._instance_server: QLocalServer | None = None  # 单实例锁服务端
        # 语音对话控制器（豆包 S2S，见 core/dialog.py）：延迟导入——dialog 模块
        # 顶层虽无重依赖，但保持与 asr_pipeline 一致的按需加载风格
        from .dialog import DialogController
        self.dialog = DialogController(self)
        self.dialog_hotkey: HotkeyListener | None = None  # 对话热键（可配）
        # 对话内容 → 历史记录的配对器（见 core/dialog_history；信号在 _setup 里接）
        from .dialog_history import ExchangeRecorder
        self._dialog_history = ExchangeRecorder()

    _INSTANCE_KEY = "openrealtimeasr-ui-single-instance"  # 命名管道/锁名

    def _acquire_single_instance(self) -> bool:
        """获取单实例锁。True=本进程为主实例；False=已有实例（已通知其显示）。

        --replace（提权重启接管模式）：通知旧实例退出并等它释放锁后接管，
        而不是唤出旧实例后自己退出。
        """
        replace = "--replace" in sys.argv[1:]
        probe = QLocalSocket()
        probe.connectToServer(self._INSTANCE_KEY)
        if probe.waitForConnected(300):
            if replace:
                # 提权重启接管：通知旧实例退出，等它释放锁后再接管
                probe.write(b"quit\n")
                probe.waitForBytesWritten(300)
                probe.disconnectFromServer()
                logger.info("接管模式：已通知旧实例退出，等待释放单实例锁")
                # 轮询等锁释放（旧实例退出清理最多几百 ms），上限 5 秒
                for _ in range(50):
                    time.sleep(0.1)
                    p2 = QLocalSocket()
                    p2.connectToServer(self._INSTANCE_KEY)
                    if not p2.waitForConnected(100):
                        break  # 服务端已释放
                    p2.disconnectFromServer()
            else:
                # 已有实例在运行：通知唤出悬浮条，随后本进程退出。
                # --hidden 实例（开机自启 Run 键与管理员计划任务双触发时
                # 后到的那份）静默退出，不在开机时唤出悬浮条
                if "--hidden" not in sys.argv[1:]:
                    probe.write(b"show\n")
                    probe.waitForBytesWritten(300)
                probe.disconnectFromServer()
                print(t("app.msg.already_running"))
                logger.info("检测到已有实例，本次启动退出")
                return False

        # 无实例：清理上次崩溃可能残留的管道（正常退出会 removeServer，
        # 异常残留会导致 listen 失败）后创建服务端
        QLocalServer.removeServer(self._INSTANCE_KEY)
        self._instance_server = QLocalServer()
        if not self._instance_server.listen(self._INSTANCE_KEY):
            # 极端情况仍失败：不阻塞启动，仅记日志（放弃单实例约束）
            logger.error("单实例锁创建失败（忽略）：%s",
                         self._instance_server.errorString())
            self._instance_server = None
            return True
        self._instance_server.newConnection.connect(self._on_instance_client)
        return True

    def _on_instance_client(self) -> None:
        """处理第二实例的连接请求：唤出悬浮条 / 接管模式退出当前实例。"""
        conn = self._instance_server.nextPendingConnection() if self._instance_server else None
        if conn is None:
            return

        def handle() -> None:
            data = bytes(conn.readAll())
            if b"quit" in data:
                # 提权重启接管：新实例已就绪，当前实例让位退出
                logger.info("收到接管退出指令，当前实例退出")
                self._on_exit()
                return
            if b"show" in data and self.bar is not None:
                # 唤出悬浮条（本槽在主线程事件循环中执行，线程安全）
                self.bar.show()
                self.bar.raise_()
                self.bar._restart_auto_hide()

        conn.readyRead.connect(handle)
        # 连接断开后清理，避免句柄泄漏
        conn.disconnected.connect(conn.deleteLater)

    def run(self) -> int:
        self._app = QApplication.instance() or QApplication(sys.argv)
        # 程序图标（任务栏/窗口标题栏/托盘统一用 Open-RealtimeASR-UI 声浪图标）
        self._app.setWindowIcon(app_icon())
        # 悬浮条隐藏后程序仍驻留托盘，不随最后一个窗口关闭退出
        self._app.setQuitOnLastWindowClosed(False)

        # 单实例锁：第二个实例通知已有实例唤出悬浮条后自行退出。
        # QLocalServer 基于 Windows 命名管道，可靠且崩溃残留可清理。
        if not self._acquire_single_instance():
            return 0
        # 设置应用默认字体（完全由用户驱动：font_file > font_family > Qt 默认）
        from ..ui.fonts import apply_default_font
        apply_default_font(
            self._app,
            family=getattr(self.cfg.ui, "font_family", "") or "",
            font_file=getattr(self.cfg.ui, "font_file", "") or "",
            app_point_size=int(getattr(self.cfg.ui, "font_size_app", 9) or 9),
        )

        self.bar = FloatingBar(self.cfg.ui)
        # 命令待命超时定时器：必须在 QApplication 创建后（QTimer 前置约束）
        self._standby_timer = QTimer(self)
        self._standby_timer.setSingleShot(True)
        self._standby_timer.timeout.connect(self._on_standby_timeout)
        # 延迟回车保险：修正迟迟不回时超时兜底执行回车，发送不悬死
        self._deferred_enter_timer = QTimer(self)
        self._deferred_enter_timer.setSingleShot(True)
        self._deferred_enter_timer.timeout.connect(self._on_deferred_enter_timeout)
        # 完整配置（含密钥节）：右键菜单密钥过滤用（bar.cfg 只有 cfg.ui 子节）
        self.bar.set_full_config(self.cfg)
        if is_configured(self.cfg):
            self.bar.set_hint(
                t("hint.press_to_start", hotkey=self.cfg.hotkey.toggle.upper()))
        elif getattr(self.cfg, "engine", "") == "funasr":
            # funasr 无密钥概念：未就绪只能是依赖不可用（打包版/未安装）
            self.bar.set_hint(t("hint.funasr_missing"))
        else:
            self.bar.set_hint(t("hint.no_key"))
        self.bar.set_engine(self._engine_menu_id(self.cfg))
        self.bar.set_injection_method(
            getattr(getattr(self.cfg, "injection", None), "method", "clipboard_paste")
        )
        self.bar.set_keep_clipboard(
            bool(getattr(getattr(self.cfg, "injection", None), "keep_clipboard", True))
        )
        self.bar.set_game_mode(
            bool(getattr(getattr(self.cfg, "injection", None), "game_mode", False))
        )
        # 历史对话剪贴板：启动时读入开关（右键"历史记录"菜单可切换）
        _history.set_enabled(
            bool(getattr(getattr(self.cfg, "history", None), "enable", True))
        )
        self.bar.signals.toggle_requested.connect(self._on_toggle)
        self.bar.signals.interrupt_requested.connect(self._on_click_interrupt)
        self.bar.signals.settings_requested.connect(self._open_settings)
        self.bar.signals.history_window_requested.connect(self._open_history_window)
        self.bar.signals.exit_requested.connect(self._on_exit)
        self.bar.signals.inject_requested.connect(self._on_inject_requested)
        self.bar.signals.hide_requested.connect(self._on_hide_to_tray)
        self.bar.signals.engine_changed.connect(self._on_engine_changed)
        self.bar.signals.theme_changed.connect(self._on_theme_changed)
        self.bar.signals.auto_hide_toggled.connect(self._on_auto_hide_toggled)
        self.bar.signals.always_on_top_toggled.connect(self._on_always_on_top_toggled)
        self.bar.signals.font_file_changed.connect(self._on_font_file_changed)
        self.bar.signals.injection_method_changed.connect(self._on_injection_method_changed)
        self.bar.signals.keep_clipboard_toggled.connect(self._on_keep_clipboard_toggled)
        self.bar.signals.game_mode_toggled.connect(self._on_game_mode_toggled)
        self.bar.signals.pause_bg_audio_toggled.connect(
            self._on_pause_bg_audio_toggled)
        self.bar.signals.hold_to_talk_toggled.connect(self._on_hold_to_talk_toggled)
        self.bar.signals.live_intermediate_toggled.connect(self._on_live_intermediate_toggled)
        self.bar.signals.llm_enable_toggled.connect(self._on_llm_enable_toggled)
        self.bar.signals.bar_confirm.connect(self._on_bar_confirm)
        self.bar.signals.autostart_toggled.connect(self._on_autostart_toggled)
        self.bar.signals.elevate_requested.connect(self._on_elevate)
        # 语音命令模式（见 core/voice_commands.py）
        self._sync_voice_commands_config()
        self._register_voice_command_handlers()
        self.bar.signals.command_action.connect(self._on_command_action)
        self.bar.signals.command_ai.connect(self._on_command_ai)
        self.bar.signals.command_phrase.connect(self._on_command_phrase)
        self.bar.signals.command_standby.connect(self._on_command_standby)
        self.bar.signals.commands_toggled.connect(self._on_commands_toggled)
        if self._start_hidden:
            logger.info("静默启动：悬浮条隐藏至托盘，热键按下后呼出")
        else:
            self.bar.show()

        # 商店版首启欢迎（--hidden 开机自启不弹；只弹一次）
        # 延迟导入：welcome_dialog 会带起 config.loader，顶层导入与 core.app 成环
        from ..ui.welcome_dialog import (
            WelcomeDialog,
            is_first_run,
            mark_shown,
            should_show_welcome,
        )

        if should_show_welcome(is_steam_build(), self._start_hidden):
            if is_first_run():
                # 先落标记再弹：弹窗期间崩溃/强杀也不会让下次启动再弹一次
                mark_shown()
                WelcomeDialog(
                    on_open_settings=self._open_settings,
                    hotkey=self.cfg.hotkey.toggle.upper(),
                    cfg=self.cfg,
                ).exec()

        # 麦克风常驻预热：后台打开采集流（设备打开耗时 0.2~0.6s），
        # 热键按下时音频即刻就绪，防止开头吞字；失败不阻塞启动
        if getattr(getattr(self.cfg, "audio", None), "warmup_capture", True):
            threading.Thread(
                target=self._warmup_capture, daemon=True, name="capture-warmup"
            ).start()

        # 系统托盘
        self._setup_tray()

        # 游戏模式沿上次会话保留开启 + 本次未提权：启动时补一次提权询问。
        # 提权只活一个进程生命周期，重启后必然回到普通权限，而游戏模式
        # 落盘保留——这个组合每次重启都会出现（2026-09-10 用户报「重启后
        # 默认不是提权状态」）。复用开关时同款确认条（game_mode_elevate）；
        # 静默启动不弹，免破坏开机自启的安静
        from .elevation import should_prompt_elevation
        if (not self._start_hidden and should_prompt_elevation(bool(getattr(
                getattr(self.cfg, "injection", None), "game_mode", False)))):
            self.bar.show_llm_wait_confirm(
                t("app.confirm.game_mode_elevate"),
                kind="game_mode_elevate", yes_text=t("common.elevate_now"),
                no_text=t("common.not_now"))

        # 配置文件热加载监听
        self._config_path = find_config_path()
        if self._config_path:
            self._config_watcher = QFileSystemWatcher()
            self._config_watcher.addPath(str(self._config_path))
            # 热加载防抖：设置界面保存会逐字段写 30+ 次，每次都完整热加载能把
            # 主线程卡死数秒（曾见 1 秒 33 次加载）。延迟 300ms 合并成一次
            self._config_reload_timer = QTimer(self._app)
            self._config_reload_timer.setSingleShot(True)
            self._config_reload_timer.setInterval(300)
            self._config_reload_timer.timeout.connect(self._reload_config)
            self._config_watcher.fileChanged.connect(self._on_config_file_changed)
            logger.info("配置文件监听已启动：%s", self._config_path)

        # 平台 SDK 回调泵：平台回调必须在主线程按节奏跑（云存档读写
        # 完成、扩展包状态变化都靠它推进），不能塞进 Qt 事件循环的空隙里等。
        # 500ms 是平台文档给的经验值；初始化失败则整段静默跳过。
        self._steam = get_integration()
        if is_steam_build() and self._steam.init():
            self._steam_timer = QTimer(self._app)
            self._steam_timer.setInterval(500)
            self._steam_timer.timeout.connect(self._steam.run_callbacks)
            self._steam_timer.start()
            logger.info("Steamworks 回调泵已启动（500ms）")

            # 有 N 卡但没装显卡加速扩展包：主动提示一次（设置页另有安装入口）。
            # 必须排在 init() 之后：扩展包状态查询读的是 init 里建的 Apps 句柄，
            # 没 init 时它恒返回 False，会给已装扩展包的用户白提示一次。
            # 用 transient_hint 而不是弹框：它不是错误，不值得打断用户。
            from ..steam_integration import (
                STEAM_DLC_GPU_APP_ID,
                nvidia_gpu_present,
            )

            if STEAM_DLC_GPU_APP_ID and nvidia_gpu_present() and \
                    not self._steam.dlc_installed(STEAM_DLC_GPU_APP_ID):
                self.bar.signals.transient_hint.emit(
                    t("set.steam.gpu_install_hint"), 5.0)

        self.hotkey = HotkeyListener(
            self.cfg.hotkey.toggle, self._on_toggle,
            on_press=self._request_hold_press if self._hold_to_talk_enabled() else None)
        try:
            self.hotkey.start()
        except Exception as exc:
            logger.error("热键注册失败：%s", exc)
            show_error(
                t("app.err.hotkey_register", error=exc)
            )
            return 3

        # 命令待命热键（可选入口）：语音命令开启且配置了热键时注册
        self._update_cmd_hotkey_listener()

        # 语音模式：控制器信号转接到悬浮条。对话内容走结构化 dialog_turn
        # （轮次/角色/文本/定稿/打断），不再复用录音那条带「你：/AI：」前缀的
        # 文本通道——角色由 UI 画成色带，前缀字符串已彻底退场
        self.dialog.state_changed.connect(self.bar.signals.state_changed)
        self.dialog.dialog_turn.connect(self.bar.signals.dialog_turn)
        self.dialog.level_changed.connect(self.bar.signals.level_changed)
        self.dialog.error_occurred.connect(self.bar.signals.error_occurred)
        self.dialog.transient_hint.connect(self.bar.signals.transient_hint)
        # 对话内容进历史记录（一轮问答合成一条，见 core/dialog_history）：
        # 配对与落库时机都在 recorder 里，这里只把两条信号接过去
        self.dialog.dialog_turn.connect(self._dialog_history.on_turn)
        self.dialog.state_changed.connect(self._dialog_history.on_state)
        # 对话入口（右键菜单「语音对话」/对话态启停按钮/声纹整窗点击）：
        # 等价于按对话热键（toggle 自带互斥与线程化）
        self.bar.signals.dialog_requested.connect(
            lambda checked: self.dialog.toggle())
        # 对话热键（可配；空或 dialog.enable=False 时不注册）
        self._update_dialog_hotkey_listener()

        engine_desc = getattr(self.cfg, "engine", "tencent")
        logger.info("应用已启动 引擎=%s 热键=%s", engine_desc, self.cfg.hotkey.toggle)
        print(t("app.msg.started", hotkey=self.cfg.hotkey.toggle.upper()))

        # 删除键打断录音（可选，默认关闭）：仅监听不拦截按键
        self._update_delete_listener(
            bool(getattr(getattr(self.cfg, "ui", None), "interrupt_on_delete", False))
        )

        # FunASR 引擎：启动后台预热模型
        self._preheat_funasr()

        # 右键菜单预热：Qt **首次**弹出菜单要一次性付掉创建分层弹出窗口
        # （WA_TranslucentBackground + DWM 合成）+ 首次解析菜单 QSS 的成本，
        # 本机实测 0.65~0.76s、第二次仅 0.02s；用户机实测首次右键等了 3s+。
        # 在屏幕外 popup 一次并立刻关闭，把这份成本挪到事件循环首轮 ——
        # 用户第一次右键就是热的。内部吞掉一切异常，不影响启动。
        from ..ui.context_menu import prewarm_context_menu
        QTimer.singleShot(0, lambda: prewarm_context_menu(self.bar))

        # LLM 文本修正（可选）：按配置创建修正器
        self._setup_llm_corrector()
        # 按生效值同步逐字同步勾选态（AI 修正/剪贴输入时强制未勾选）
        self.bar.set_live_intermediate(self._live_intermediate())

        try:
            return int(self._app.exec())
        finally:
            self._cleanup()

    # ---- 删除键打断（可选功能）----

    def _update_delete_listener(self, enabled: bool) -> None:
        """按配置启停删除键打断监听。"""
        if enabled:
            if self.delete_listeners:
                return
            # keyboard 的 "backspace,delete" 是序列热键（先 B 后 D 才触发）不是任一
            # 触发，要分开注册；suppress=False 不拦截按键
            for key in ("backspace", "delete"):
                listener = HotkeyListener(key, self._on_delete_interrupt, suppress=False)
                try:
                    listener.start()
                    self.delete_listeners.append(listener)
                except Exception as exc:
                    logger.error("删除键打断监听注册失败（%s）：%s", key, exc)
            if self.delete_listeners:
                logger.info("删除键打断已开启（录音中按 Backspace/Delete 停止）")
        else:
            for listener in self.delete_listeners:
                listener.stop()
            self.delete_listeners.clear()
            logger.info("删除键打断已关闭")

    def _on_delete_interrupt(self) -> None:
        """删除键回调（keyboard 线程）：仅录音中响应。停止放独立线程，不阻塞分发。"""
        if self.state != APP_STATE_LISTENING:
            return
        threading.Thread(
            target=self._delete_interrupt, daemon=True, name="delete-interrupt"
        ).start()

    def _delete_interrupt(self) -> None:
        with self._lock:
            # 二次确认状态：可能与热键停止竞争，非录音中则忽略
            if self.state == APP_STATE_LISTENING:
                logger.info("删除键打断录音，丢弃本会话未上屏文字")
                self._discard_results = True
                self._session_interrupted = True   # 真打断，在途修正也不许再上屏
                self.injector.cancel_pending()
                self._stop_session()

    def _on_click_interrupt(self) -> None:
        """左键打断（主线程信号槽）：仅录音中响应，停止放独立线程避免卡 UI。"""
        if self.state != APP_STATE_LISTENING:
            return
        threading.Thread(
            target=self._click_interrupt, daemon=True, name="click-interrupt"
        ).start()

    def _click_interrupt(self) -> None:
        with self._lock:
            if self.state == APP_STATE_LISTENING:
                logger.info("左键打断录音，丢弃本会话未上屏文字")
                self._discard_results = True
                self._session_interrupted = True   # 真打断，在途修正也不许再上屏
                self.injector.cancel_pending()
                self._stop_session()

    # ---- 长按说话（可选交互，spec: docs/superpowers/specs/2026-09-19-hold-to-talk-design.md）----

    def _hold_to_talk_enabled(self) -> bool:
        """长按模式是否生效：配置开启 + 探针可用 + 热键主键可解析。

        最后一条是 fail-safe：探针只能按虚拟键码采样，而 parse_hotkey 对「无修饰键
        的裸键」「多步组合」返回 None——这类热键本来就走 keyboard 钩子，探针拿不到
        主键，长按判定无从谈起。此时自动下线、回落旧的「按一下启停」；否则 on_press
        取代了 on_trigger，而探针永远报「未按住」，热键会变成按下去毫无反应的死键。
        """
        try:
            if not hold_flag_enabled(getattr(self.cfg.hotkey, "hold_to_talk", False)):
                return False
        except Exception:
            return False
        if not bool(_hold_probe.available()):
            return False
        try:
            return self._hold_vk_code() > 0
        except Exception:
            return False

    def _hold_threshold_ms(self) -> int:
        try:
            return int(getattr(self.cfg.hotkey, "hold_threshold_ms", 300) or 300)
        except Exception:
            return 300

    def _hold_poll_ms(self) -> int:
        try:
            return max(5, int(getattr(self.cfg.hotkey, "hold_poll_ms", 20) or 20))
        except Exception:
            return 20

    def _hold_vk_code(self) -> int:
        """热键串里主键的虚拟键码（无修饰键组合时探针仍可用）。"""
        from ..hotkey.native import parse_hotkey
        spec = parse_hotkey(getattr(self.cfg.hotkey, "toggle", "") or "")
        return int(spec[1]) if spec else 0

    def _request_hold_press(self) -> None:
        """长按模式的 on_press（在热键线程执行）：只发信号，处理留给主线程。

        见 hold_press_requested 定义处的线程亲和性说明。关闭长按时本方法根本不会注册
        进 HotkeyListener（on_press=None），按下行为与改动前逐字一致。
        """
        self.hold_press_requested.emit()

    def _on_hotkey_press(self) -> None:
        """热键按下（长按模式下由 hold_press_requested 排队到主线程后调用）。

        对话进行中交给对话侧长按（spec §3.3：录音热键在对话里同样是「按住说话」
        ——按住对话热键与按住录音热键都该能抢话）；对话长按没生效时，仍走既有
        _on_toggle（它带互斥提示）；长按未启用时同理，保证关闭开关后行为与改动前
        逐字一致。

        交给对话侧时**必须把刚按下的键码带过去**（_hold_vk_code 读的是录音热键）：
        控制器自己解析的是 cfg.hotkey.dialog，两个键不是一个键，带错了（或不带）
        它就会去采另一个键——SPEAKING 下先不可逆地打断 AI，然后闸门永远打不开、
        一帧都不上行（详见 DialogController.hold_press）。上面 _hold_to_talk_enabled()
        已经证明这个键码可解析（> 0），此处不会抛。
        """
        if not self._hold_to_talk_enabled():
            self._on_toggle()
            return
        if self.dialog is not None and self.dialog.is_active():
            if self.dialog.hold_enabled():
                self.dialog.hold_press(self._hold_vk_code())
                return
            self._on_toggle()
            return
        if self._hold_tracker is not None and self._hold_tracker.phase != "idle":
            return  # 系统自动重复的 keydown
        self._hold_vk = self._hold_vk_code()
        self._hold_owns_session = False   # 本次长按还没起会话，是否归属到 ACTIVATE 才定
        # 上一次长按的待修正缓冲可能还没送出去（松手后 300ms 收尾窗口内又按下了）：
        # 必须**先送修再清**，否则连按两次时前一段话会被这里静默吃掉
        if getattr(self, "_hold_llm_texts", None):
            self._hold_llm_flush()
        # 兜底清空：异常路径留下的残句不得混进这一轮（正常路径已由 flush 清空）
        self._hold_llm_texts = []
        self._hold_tracker = HoldTracker(self._hold_threshold_ms())
        self._hold_tracker.press()
        self._start_hold_watch()
        self._apply_hold_ui(HoldAction.NONE)
        self.bar.signals.state_changed.emit(UI_ARMED)

    def _start_hold_watch(self) -> None:
        """建表并起表。只能在主线程调用（QTimer 的线程亲和性，见类信号说明）。"""
        if self._hold_timer is None:
            self._hold_timer = QTimer(self)
            self._hold_timer.timeout.connect(self._on_hold_tick)
        self._hold_timer.start(self._hold_poll_ms())

    def _stop_hold_watch(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.stop()

    def _probe_held(self) -> bool:
        """探针采样：None（调用失败）按未按下处理，宁可提前结束不可永久按住。"""
        state = _hold_probe.is_key_down(self._hold_vk)
        return bool(state) if state is not None else False

    def _on_hold_tick(self) -> None:
        """采样一拍。异常必须吞掉：Qt 定时器回调里抛出会中断后续调度。"""
        try:
            tracker = self._hold_tracker
            if tracker is None:
                self._stop_hold_watch()
                return
            action = tracker.poll(self._probe_held(), time.monotonic())
            if action == HoldAction.ACTIVATE:
                self._hold_active = True
                self._hold_start_recording()
            elif action in (HoldAction.END, HoldAction.DISCARD):
                self._hold_active = False
                self._stop_hold_watch()
                self._hold_tracker = None
                if action == HoldAction.END:
                    self._hold_end_recording()   # 要读归属标记：清理必须排在它之后
                else:
                    self._apply_hold_ui(action)
                self._hold_owns_session = False
        except Exception:
            logger.exception("长按采样异常")
            self._hold_active = False
            self._hold_owns_session = False
            self._stop_hold_watch()
            self._hold_tracker = None
            self._apply_hold_ui(HoldAction.DISCARD)
            # 异常路径也算「松手」：本次长按请求的开始若还在半路上，仍然要收掉它，
            # 否则那次延后的开始会留下一段没人管的录音。收尾表自己出错不得再抛
            # ——本方法就是 Qt 定时器回调，抛出会中断后续调度
            if self._hold_pending_start:
                try:
                    self._arm_pending_start_cleanup()
                except Exception:
                    logger.exception("延后开始的收尾表建立失败")
                    self._hold_pending_start = False

    def _hold_start_recording(self) -> None:
        """越过阈值：对话中则开对话上行闸门，否则走既有启停路径开始录音。

        会话可能是别的路径起的（录音按钮/托盘菜单/语音命令），也可能本会话刚被
        60 秒硬上限或左键打断停掉。_on_toggle 是无条件取反——此时调它会把别人的
        录音停掉、或让松手凭空开一段新录音，所以只在 APP_STATE_IDLE 时真正启动。
        对话分支是为「按下录音热键之后、越过阈值之前对话被别的入口打开」这条
        交叉路径准备的：那一下必须落到对话闸门上，不能去 toggle 录音。
        """
        if self.dialog is not None and self.dialog.is_active():
            self.dialog.set_hold_active(True)
            return
        if self.state != APP_STATE_IDLE:
            self._emit_live_state()   # 界面按真实状态纠正（按下时的 ARMED 提示要撤）
            return
        self._hold_owns_session = True
        self.bar.signals.state_changed.emit(UI_LISTENING)
        self._on_toggle()
        # 这一拍结束时还没进入聆听 = 开始还在半路上（悬浮条隐藏时 _on_toggle 走
        # 延后 150ms 的 _delayed_toggle，或启动线程还没跑到）。记下它，松手时若
        # 仍未落地，就由 _arm_pending_start_cleanup 起表收掉——否则那次延后的
        # 开始会在松手之后落地，留下一段没人要的录音（见 PENDING_START_CLEANUP_MS）。
        self._hold_pending_start = self.state != APP_STATE_LISTENING

    def _hold_end_recording(self) -> None:
        """松手：对话中则关对话闸门并结束本轮；否则只停「本次长按自己起的」录音。

        长按起的会话若已经不在聆听态（硬上限/打断已把它停掉，或启动当场失败），
        也不能再 toggle——那会从一次松手里凭空开出一段新录音。归属标记由
        _on_hold_tick 在 END/DISCARD/异常三个分支统一清除。

        三种收场：
        1. 会话已落地且属于本次长按：当场 toggle 停掉，延后窗口的收尾不再需要；
        2. 会话还在半路上（延后 toggle 未落地）：这一拍**绝不能** toggle（延后
           路径下那是「开始」），改用一次性定时器盯着，落地了再收；
        3. 别人的会话/已经结束的会话：什么都不做（既有契约）。
        """
        if self.dialog is not None and self.dialog.is_active():
            self.dialog.hold_end()
            return
        if self._hold_owns_session and self.state == APP_STATE_LISTENING:
            self._hold_pending_start = False
            # 长按 + AI 修正：先开收尾窗口再停录（顺序不能反）——停录会置丢弃
            # 标记并很快把状态打回待机，窗口必须在那之前打开才能收到冲刷尾句
            self._begin_hold_llm_collect()
            self._on_toggle()
            return
        if self._hold_pending_start:
            self._arm_pending_start_cleanup()
            return
        # 会话在按住期间已自行结束（60 秒硬上限 / 被打断），但缓冲里可能已攒下
        # 文本：补开一次收尾窗口把它送修，否则松手后这段内容凭空消失
        # （缓冲为空时 _hold_llm_flush 直接返回，不会空发请求）
        self._begin_hold_llm_collect()

    def _arm_pending_start_cleanup(self) -> None:
        """起一次性定时器：把「延后 toggle 窗口里松手」那段半路上的开始收掉。

        单次 200ms（PENDING_START_CLEANUP_MS），到点后只在标记仍置位、且真的进入了
        聆听态时才 stop（见 _on_pending_start_timeout）。定时器归主线程（本方法只从
        主线程的采样回调进来），跨线程建表会被 Qt 拒绝，同 _start_hold_watch。
        """
        if self._pending_start_timer is None:
            self._pending_start_timer = QTimer(self)
            self._pending_start_timer.setSingleShot(True)
            self._pending_start_timer.setInterval(PENDING_START_CLEANUP_MS)
            self._pending_start_timer.timeout.connect(self._on_pending_start_timeout)
        self._pending_start_timer.start()

    def _on_pending_start_timeout(self) -> None:
        """收尾定时器到点：那次延后的开始若真的落地了，就停掉它。

        只认「标记仍置位」+「当前正在聆听」两条同时成立：
        - 标记已被清（会话在按住期间正常落地并收场，或已经收尾过）= 无事可做；
        - 状态不是聆听 = 那次开始根本没落地（或已经被别的路径收掉）。**绝不能在
          待机态调 _on_toggle**：那里它是「开始录音」——正是本机制要防的缺陷，
          松手反倒开出一段没人要的录音。
        异常必须吞掉：Qt 定时器回调里抛出会中断后续调度（同 _on_hold_tick）。
        """
        try:
            if not self._hold_pending_start:
                return
            self._hold_pending_start = False
            if self.state != APP_STATE_LISTENING:
                return
            logger.info("长按已松手但延后的开始仍落地了：收掉这段没人要的录音")
            self._on_toggle()
        except Exception:
            logger.exception("延后开始的收尾失败")
            self._hold_pending_start = False

    def _emit_live_state(self) -> None:
        """把界面拉回当前真实会话状态。

        按住/丢弃期间界面可能停在 ARMED，只能按 self.state 纠正，不得替管线宣称
        状态（STOPPING、识别中、错误这些界面由管线自己发）。
        """
        if self.state == APP_STATE_LISTENING:
            self.bar.signals.state_changed.emit(UI_LISTENING)
        elif self.state == APP_STATE_IDLE:
            self.bar.signals.state_changed.emit(UI_IDLE)

    def _apply_hold_ui(self, action: str) -> None:
        """轻点丢弃：界面回真实状态（ARMED 只是提示态，没有业务副作用要撤）。

        正在录音时不得发 UI_IDLE：那会清掉条上的文字与会话显示、重开自动隐藏，
        而录音其实还在继续（会话是别的路径起的，长按无权结束它）。
        """
        if action == HoldAction.DISCARD:
            self._emit_live_state()

    # ---- 长按 + AI 修正：松手整段送 AI（spec 之外的组合行为，见 _begin_hold_llm_collect）----

    def _hold_llm_active(self) -> bool:
        """按住期间的累积判定：本次长按真正持有一个会话 + AI 修正器存在。

        `_hold_owns_session` 不能省：按下越过阈值时若会话已由别的路径起（托盘/
        按钮/命令热键），_hold_start_recording 会放弃归属，此时若还把 final 收进
        缓冲就再也没人来送修——那些 final 会全部消失。
        两个开关缺一不可——只开长按、或只开 AI 修正，行为与改动前逐字一致。
        """
        # getattr 兜底：本方法会被「长按采样」路径调用，而该路径的测试替身
        # （以及构造中途的实例）未必带上修正器字段
        return (bool(getattr(self, "_hold_active", False))
                and bool(getattr(self, "_hold_owns_session", False))
                and getattr(self, "_llm_corrector", None) is not None)

    def _begin_hold_llm_collect(self) -> None:
        """松手（主线程）：开收尾窗口，把引擎冲刷的尾句一起收齐再送 AI。

        只做「置位 + 起表」：真正的送修在窗口到点后的 _hold_llm_flush。
        主线程调用（_on_hold_tick），QTimer 的线程亲和性满足。
        本方法只看修正器在不在——调用点都是「本次长按要收场了」，是否真有文本
        由 _hold_llm_flush 自己判断（空缓冲直接返回，不会空发请求）。
        """
        if getattr(self, "_llm_corrector", None) is None:
            return
        self._hold_llm_collect = True
        if self._hold_llm_timer is None:
            self._hold_llm_timer = QTimer(self)
            self._hold_llm_timer.setSingleShot(True)
            self._hold_llm_timer.timeout.connect(self._hold_llm_flush)
        self._hold_llm_timer.start(HOLD_LLM_COLLECT_MS)

    def _hold_llm_flush(self) -> None:
        """收尾窗口到点（主线程）：整段一次性送 AI，修正返回后上屏。

        走 wait 语义（原文不上屏、回包直接注入修正版）：这正是用户要的「松手
        才送 AI，修完才出字」——按住期间输入框一个字都不出，只在悬浮条气泡里
        看识别文本。超时/失败由修正器降级为原文（见 do_llm_replace_job）。
        """
        self._hold_llm_collect = False
        # 局部名不用 `t`：本模块顶层 import 了 i18n 的 t()，遮蔽它会让下面的
        # t("hint.hold_llm_correcting") 变成 UnboundLocalError（tests/test_i18n.py
        # 有专门的守卫用例，见 NoShadowingOfTranslationHelperTest）
        chunks = [s for s in getattr(self, "_hold_llm_texts", []) if s]
        self._hold_llm_texts = []
        # 真打断（删除键 / 左键点击）后不得再送修：用户明确表示不要这段内容了。
        # 打断会把界面打回待机，松手走的是本方法的兜底调用——不在这里拦，
        # "打断"之后整段照样被送 AI 并上屏，打断动作等于失效
        if getattr(self, "_session_interrupted", False):
            logger.info("长按整段已被真打断，丢弃缓冲：%d句", len(chunks))
            return
        whole = "".join(chunks).strip()
        if not whole:
            return
        corrector = getattr(self, "_llm_corrector", None)
        if corrector is None:
            # 修正器在会话中途被关掉（设置热改 / key 被清）：直接上屏原文，
            # 不能因为"没人修正了"就把用户说的话吞掉
            try:
                self.injector.inject(whole, is_final=True,
                                     done_callback=self._record_inject_hwnd)
                self._last_spoken_text = whole
                _history.add(whole)
                logger.info("长按整段上屏（修正器已关闭）：%d字", len(whole))
            except Exception:
                logger.exception("长按整段上屏失败")
            return
        self._last_final_at = time.time()
        self._feed_context[self._last_final_at] = True
        self._hold_llm_batch_ts.add(self._last_final_at)
        # wait 模式没有原文上屏可记窗口：识别时刻快照前台窗口，回包时校验焦点
        self._inject_hwnd = self.injector.current_window_hwnd()
        self.bar.signals.transient_hint.emit(t("hint.hold_llm_correcting"), 2.0)
        logger.info("长按整段送 AI 修正：%d句 %d字", len(chunks), len(whole))
        corrector.feed(whole, self._last_final_at)
        # batch_sentences>1 时 feed 只入累积窗口（要凑满 N 句或等 batch_idle_ms 静默
        # 超时）；而长按整段本身就是"一次性打包"，再等一个 idle 窗口纯属白等 ——
        # 立即冲刷（batch_sentences<=1 时缓冲为空，本调用是空操作）
        flush_pending = getattr(corrector, "flush_pending", None)
        if callable(flush_pending):
            flush_pending()

    # ---- 会话控制 ----

    def _on_toggle(self) -> None:
        # 两模式互斥：对话中忽略录音热键（忽略+提示，不抢占——抢占会丢正在
        # 进行的对话，代价不对称，spec「两模式互斥」小节）
        if self.dialog is not None and self.dialog.is_active():
            self.bar.signals.error_occurred.emit(t("app.err.dialog_active"))
            return
        # 悬浮条已隐藏时先呼出，稍后再切换录音
        if self.bar and not self.bar.isVisible():
            self.bar.signals.show_requested.emit()
            # 延后切换，给窗口显现一点时间
            threading.Thread(target=self._delayed_toggle, daemon=True, name="toggle").start()
            return
        threading.Thread(target=self._toggle, daemon=True, name="toggle").start()

    def _delayed_toggle(self, delay: float = 0.15) -> None:
        """呼出悬浮条后短暂延时再切换录音状态。"""
        time.sleep(delay)
        self._toggle()

    def _toggle(self) -> None:
        with self._lock:
            if self.state == APP_STATE_IDLE:
                self._start_session()
            elif self.state == APP_STATE_LISTENING:
                self._stop_session()

    # ---- ASR 会话与音频管线（实现见 core/asr_pipeline.py，此处保留同名薄包装） ----

    def _start_session(self) -> None:
        """开始语音会话（委托 asr_pipeline）。"""
        asr_pipeline.start_session(self)

    def _warmup_capture(self) -> None:
        """后台预热采集流（委托 asr_pipeline）。"""
        asr_pipeline.warmup_capture(self)

    def _stop_capture(self) -> None:
        """立即停采集流（委托 asr_pipeline）。"""
        asr_pipeline.stop_capture(self)

    def _ensure_capture(self) -> None:
        """确保采集流可用：健康复用、死亡重建（委托 asr_pipeline）。"""
        asr_pipeline.ensure_capture(self)

    def _connect_asr_worker(self) -> None:
        """后台建连线程（委托 asr_pipeline）。"""
        asr_pipeline.connect_asr_worker(self)

    def _session_stop_from_audio(self) -> None:
        """音频回调线程的停止入口（委托 asr_pipeline）。"""
        asr_pipeline.session_stop_from_audio(self)

    def _stop_session(self) -> None:
        """停止语音会话（委托 asr_pipeline）。"""
        # 平台统计：会话时长在停止这一侧结算（起始时间由 start_session 落）
        self._accrue_steam_session()
        asr_pipeline.stop_session(self)

    def _create_asr(self, cfg):
        """按 engine 创建 ASR 客户端（委托 asr_pipeline）。"""
        return asr_pipeline.create_asr(self, cfg)

    def _preheat_funasr(self) -> None:
        """后台预热 FunASR 模型（委托 asr_pipeline）。"""
        asr_pipeline.preheat_funasr(self)

    def _kick_funasr_preheat(self, model: str, display_name: str) -> None:
        """切到本地引擎后立即预热（菜单切换路径专用）。

        菜单切换已把 engine/model 预同步进内存 cfg，配置热加载对比不出
        差异、不会自动触发预热，必须在此显式启动，否则模型要等到用户
        首次按录音热键才开始加载。
        """
        if not getattr(self.cfg.funasr, "preheat", True):
            self.bar.signals.transient_hint.emit(
                t("hint.engine_switched_next", engine=display_name), 2.0)
            return
        # 同模型实例已就绪/加载中：不重复起加载，只给对应状态提示
        inst = self._funasr_instance
        if inst is not None and getattr(inst, "model_name", "") == model:
            if inst.is_ready():
                self.bar.signals.transient_hint.emit(
                    t("hint.engine_switched_ready", engine=display_name), 2.0)
                return
            if inst.is_loading():
                self.bar.signals.transient_hint.emit(
                    t("hint.engine_switched_loading", engine=display_name), 2.5)
                return
        # 无实例/换模型/上次加载失败：弃旧起新，预热内部按代号去重
        self._funasr_instance = None
        self.bar.signals.model_ready_changed.emit(False)
        self.bar.signals.transient_hint.emit(
            t("hint.engine_switched_begin", engine=display_name), 2.5)
        self._preheat_funasr()

    def _on_audio_block(self, block) -> None:
        """音频块回调：预滚/校准/VAD/分发（委托 asr_pipeline）。"""
        asr_pipeline.on_audio_block(self, block)

    def _send_audio_with_buffer(self, block) -> None:
        """发音频块（未连上先入缓冲，委托 asr_pipeline）。"""
        asr_pipeline.send_audio_with_buffer(self, block)

    def _finish_noise_calibration(self) -> None:
        """底噪校准收尾（委托 asr_pipeline）。"""
        asr_pipeline.finish_noise_calibration(self)


    # ---- 实时 ASR 回调 ----

    def _live_intermediate(self) -> bool:
        """逐字同步生效条件：配置开启、AI 修正未启用、非剪贴输入、语音命令未启用、
        游戏模式未启用。菜单槽里已锁，这里再兜一次热加载直接改 yaml 的路径。"""
        cfg_on = bool(getattr(getattr(self.cfg, "recording", None),
                              "live_intermediate", True))
        return (
            cfg_on
            and not self._llm_config_enabled()
            and self.injector.method != "clipboard_paste"
            and not self._commands_config_enabled()
            and not self.injector.game_mode
        )

    def _on_asr_result(self, text: str, slice_type: int, index: int) -> None:
        from ..asr.tencent_realtime import (  # 延迟导入（录音中必已缓存，仅字典查找）
            SLICE_FINAL,
            SLICE_INTERMEDIATE,
            SLICE_START,
        )
        # 长按 + AI 修正的收尾窗口（松手后 HOLD_LLM_COLLECT_MS 内）：引擎冲刷的
        # 尾句收进待修正缓冲，不上屏也不送修正。必须排在下面两道守卫**之前**——
        # 松手时 stop_session 会置 _discard_results、状态也很快回待机，那两道守卫
        # 会把尾句挡在门外，整段送修就少了最后半句（用户选了「等一个极短窗口」）
        if slice_type == SLICE_FINAL and getattr(self, "_hold_llm_collect", False):
            if text and text.strip():
                self._hold_llm_texts.append(text.strip())
                # 尾句是真实说出来的、随后会随整段上屏：字数统计照常累计。
                # 下面 FINAL 分支里的那次统计被这里 return 掉了，不会重复计
                _stats = getattr(self, "_steam_stats", None)
                if _stats is not None:
                    _stats.add_final_chars(len(text.strip()))
            logger.debug("长按 + AI 修正：收尾窗口收到尾句 %d字", len(text or ""))
            return
        # 打断后的会话结果一律丢弃：停连时引擎冲刷的 final 帧也在此拦截
        # （自动停止例外——_auto_stop_pending 放行冲刷 final，见 asr_pipeline.stop_session）
        if self._discard_results:
            logger.info("会话已被打断，丢弃结果帧：%r", text[:20] if text else text)
            return
        # ASR 吐字即刷新超时计时：VAD 阈值过高没触发 SPEECH_START 时不至于误超时
        if text:
            self._last_voice_time = time.time()
        if slice_type in (SLICE_START, SLICE_INTERMEDIATE):
            # 停止后引擎收尾还会流式回吐中间帧（可连吐数百毫秒），不在 LISTENING
            # 一律丢弃，否则"停了还在写字"
            if self.state != APP_STATE_LISTENING:
                logger.debug("丢弃停止后的中间帧：%r", text[:20] if text else text)
                return
            # 中间结果：更新悬浮条（边说边看）。
            self.bar.signals.text_updated.emit(text, False)
            # 逐字同步：中间结果实时进输入框，增量追加、ASR 回头修正才退格重打。
            # wait 模式（等修正后上屏）不启用，避免与修正叠加。
            # 命令候选句不注入：中间帧静默 + final 整句补写不丢字，命令文字也不残留
            from . import voice_commands as _vcmd

            if (
                text
                and self._live_intermediate()
                and not self._wait_for_correction
                and not self._command_standby
                and not _vcmd.may_be_command_start(text)
            ):
                self.injector.inject(text, is_final=False)
        elif slice_type == SLICE_FINAL:
            # IDLE 后才到的 FINAL 是停连冲刷残留（STOPPING 阶段在途尾句已放行），丢弃
            if self.state not in (APP_STATE_LISTENING, APP_STATE_STOPPING):
                logger.info(
                    "丢弃停止完成后的残留 FINAL：%r", text[:20] if text else text
                )
                return
            # 平台统计：只累计最终字数（数字，不含文本）。
            # 放在状态守卫**之后**：那些残留帧是同一段话的重发（在途尾句已在
            # STOPPING 期放过一次），计入会重复统计同一句话。
            stats = getattr(self, "_steam_stats", None)
            if text and stats is not None:
                stats.add_final_chars(len(text))
            # STOPPING 期部分引擎（讯飞）补发残留标点帧（如单独的"。"）：纯标点丢弃
            if (
                self.state == APP_STATE_STOPPING
                and text
                and not text.strip("。，、！？；：.,!?;:~～… \t")
            ):
                logger.info("丢弃停止后的残留标点帧：%r", text)
                return
            logger.info(
                "ASR final 到达 %d字 线程=%s", len(text),
                threading.current_thread().name,
            )
            # 命中触发词/待命态的句子只作命令路由：不上屏、不进历史、不送修正
            if self._maybe_route_command(text):
                return
            self.bar.signals.text_updated.emit(text, True)
            if text:
                # 经 queued signal 切主线程提交注入（实际在 injector 专用线程执行）
                self.bar.signals.inject_requested.emit(text)

    def _on_inject_requested(self, text: str) -> None:
        """主线程槽：final 文本提交注入线程上屏。injector 专用线程执行，节流
        sleep 放主线程同步跑会冻结事件循环。排队期间被打断的注入在这拦截；
        开 LLM 修正时先注入原文，修正回调再替换。"""
        if self._discard_results:
            logger.info("会话已被打断，取消排队中的注入：%r", text[:20])
            return
        # 统一 strip：与 LLM 修正退格的长度基准一致（corrector.feed 内部同样 strip）
        text = text.strip()
        if not text:
            return
        self._last_final_at = time.time()
        # 长按 + AI 修正：按住期间**只累积**——既不上屏也不送修正。松手后由
        # _hold_llm_flush 整段一次性送 AI（用户诉求：松开长按才送 AI 处理，
        # 而不是按住期间就逐句送、逐句等返回）
        if self._hold_llm_active():
            self._hold_llm_texts.append(text)
            logger.debug("长按 + AI 修正：本句进入待修正缓冲 %d字", len(text))
            return
        logger.info(
            "注入请求 %d字 线程=%s", len(text),
            threading.current_thread().name,
        )
        # UIPI 权限差检测：目标程序管理员运行时，本进程（普通权限）的合成
        # 输入会被 Windows 静默丢弃——检测到就提示一次，避免用户无从排查
        if self.injector.game_mode and not self._elev_gap_warned:
            if self.injector.foreground_elevation_gap():
                self._elev_gap_warned = True
                logger.warning(
                    "前台目标权限高于本进程，合成输入将被 Windows 丢弃（UIPI）")
                self.bar.signals.transient_hint.emit(
                    t("hint.elevation_needed"), 5.0)
        try:
            # 「修正后上屏」：final 只入修正队列，回调里直接 inject 修正版
            #（失败/超时就 inject 原文）。中间结果显示不受影响，照常说边看
            if self._wait_for_correction and self._llm_corrector is not None:
                self._feed_context[self._last_final_at] = True
                # wait 模式没有原文上屏可记窗口：识别时刻快照前台窗口，回包时校验焦点
                self._inject_hwnd = self.injector.current_window_hwnd()
                self._llm_corrector.feed(text, self._last_final_at)
                return
            # 注入完成后由回调记录前台窗口：焦点即上屏窗口，比提交前取准
            self.injector.inject(
                text, is_final=True, done_callback=self._record_inject_hwnd,
            )
            # 「删除那句」的基准：记录本次上屏文本（修正回调会再更新）
            self._last_spoken_text = text
            # 开 LLM 修正：后台线程修正、回调替换；传本条上屏时刻供"有新句则取消"判断
            if self._llm_corrector is not None:
                # AI 修正开启时本句不进历史：统一在修正回调里记最终产物，防两版入历史
                self._feed_context[self._last_final_at] = False
                self._llm_corrector.feed(text, self._last_final_at)
                return
            # AI 修正关闭时记入历史（历史开关关着时 add 内部忽略）
            _history.add(text)
        except Exception:
            logger.exception("结果输出失败")

    def _record_inject_hwnd(self) -> None:
        """注入完成回调：记录上屏时的前台窗口，供 LLM 修正替换前校验焦点。int 赋值原子无需加锁。"""
        try:
            self._inject_hwnd = self.injector.current_window_hwnd()
        except Exception:
            self._inject_hwnd = 0

    # ---- 语音命令模式（触发词 + 本地命令表 + 「帮我」AI 路径）----

    def _sync_voice_commands_config(self) -> None:
        """把 commands 配置节同步进 voice_commands 模块（启动/热加载；委托 voice_command_handlers）。"""
        voice_command_handlers.sync_config(self)

    def _register_voice_command_handlers(self) -> None:
        """本地命令表的动作接线（委托 voice_command_handlers）。"""
        voice_command_handlers.register_handlers(
            self,
            keep_clipboard=self._on_keep_clipboard_toggled,
            live_intermediate=self._on_live_intermediate_toggled,
            llm_enable=self._on_llm_enable_toggled,
            interrupt_delete=self._on_interrupt_delete_toggled,
        )

    def _command_delete_last(self) -> None:
        """「删除那句」：退格删掉刚上屏的最后一句 final（委托 voice_command_handlers）。"""
        voice_command_handlers.command_delete_last(self)

    def _maybe_route_command(self, text: str) -> bool:
        """命令检测，命中返回 True（委托 voice_command_handlers）。"""
        return voice_command_handlers.maybe_route_command(self, text)

    def _dispatch_command(self, command_text: str) -> None:
        """ASR 线程：路由命令文本，queued signal 切主线程执行（委托 voice_command_handlers）。"""
        voice_command_handlers.dispatch_command(self, command_text)

    def _on_command_standby(self, active: bool) -> None:
        """主线程槽：待命态定时器启停（委托 voice_command_handlers）。"""
        voice_command_handlers.on_command_standby(self, active)

    def _on_standby_timeout(self) -> None:
        """待命超时（主线程）：复位标志并提示（委托 voice_command_handlers）。"""
        voice_command_handlers.on_standby_timeout(self)

    # ---- 命令待命热键（键盘直达命令态，免说触发词）----

    def _update_cmd_hotkey_listener(self) -> None:
        """按配置注册/更新/注销命令待命热键（委托 voice_command_handlers）。"""
        voice_command_handlers.update_cmd_hotkey_listener(self)

    def _on_command_hotkey(self) -> None:
        """命令待命热键触发（委托 voice_command_handlers）。"""
        voice_command_handlers.on_command_hotkey(self)

    # ---- 语音对话热键（豆包 S2S，见 core/dialog.py）----

    def _update_dialog_hotkey_listener(self) -> None:
        """按配置注册/更新/注销对话热键（启动、热加载时调用）。

        两种情况不注册：hotkey.dialog 为空（只关热键这一个入口，菜单与
        悬浮条仍可用），或 dialog.enable=False（功能总开关，菜单项与悬浮条
        对话入口一并下线）。注册失败只提示不阻断（对话是可选入口，与命令
        热键同策略，主热键失败才致命）。总开关在设置页改完会随配置热加载
        走到这里，故立即生效。

        长按说话（可选）：会话进行中把「按下」交给对话侧长按闸门，其余情况
        保持既有 toggle（spec §3.3 守卫条件——IDLE 时必须仍能按一下进入语音
        模式）。on_press 与 on_trigger 是二选一（HotkeyListener._on_hotkey），
        所以分流放在 _on_dialog_hotkey_press 里。开关热改要重建监听器：on_press
        在构造时定下，与录音热键那条路数一致（见 _reload_config 的 old_hold）。
        """
        from ..config.loader import dialog_enabled

        # 早算：它同时刷新控制器里的闸门缓存位（_on_mic_block 只读那个），
        # 也决定下面要不要挂 on_press。放在任何提前 return 之前，配置里把对话
        # 热键删掉时这次刷新同样要发生。
        hold = self.dialog is not None and self.dialog.hold_enabled()

        want = str(getattr(self.cfg.hotkey, "dialog", "") or "").strip()
        if not want or not dialog_enabled(self.cfg):
            if self.dialog_hotkey is not None:
                try:
                    self.dialog_hotkey.stop()
                except Exception:
                    logger.debug("对话热键注销失败", exc_info=True)
                self.dialog_hotkey = None
            return
        if self.dialog_hotkey is not None:
            if (self.dialog_hotkey.hotkey == want
                    and (self.dialog_hotkey.on_press is not None) == hold):
                return  # 键位与长按挂接都没变，保持注册
            try:
                self.dialog_hotkey.stop()
            except Exception:
                logger.debug("对话热键注销失败", exc_info=True)
            self.dialog_hotkey = None
        self.dialog_hotkey = HotkeyListener(
            want, self._on_dialog_hotkey,
            on_press=self._on_dialog_hotkey_press if hold else None)
        try:
            self.dialog_hotkey.start()
        except Exception as exc:
            logger.error("对话热键注册失败：%s", exc)
            self.dialog_hotkey = None
            self.bar.signals.transient_hint.emit(
                t("hint.dialog_hotkey_failed", hotkey=want), 2.5)

    def _on_dialog_hotkey_press(self) -> None:
        """对话热键按下瞬间（仅长按说话生效时注册；在热键线程执行）。

        分流：会话进行中交给对话侧长按（按下即打断、越过阈值才开闸门），
        其余情况（IDLE / STOPPING / 长按被关掉）保持既有 toggle 入口——
        后者还负责把隐藏的悬浮条先呼出来。
        线程：hold_press 里的即时打断就在本线程发，建采样表由控制器自身的
        信号排队回主线程（QTimer 的线程亲和性，见 DialogController._hold_press_queued）。
        """
        if self.dialog is not None and self.dialog.is_active():
            self.dialog.hold_press()
            return
        self._on_dialog_hotkey()

    def _on_dialog_hotkey(self) -> None:
        """对话热键触发（keyboard 线程）：悬浮条隐藏时先呼出，再切对话。

        toggle 自带线程化与录音互斥（忽略+提示），这里不做状态判断。"""
        if self.bar and not self.bar.isVisible():
            self.bar.signals.show_requested.emit()
        self.dialog.toggle()

    def _on_command_action(self, action: str, arg: str) -> None:
        """主线程槽：执行本地命令表命中的动作（委托 voice_command_handlers）。"""
        voice_command_handlers.on_command_action(self, action, arg)

    def _on_command_phrase(self, content: str) -> None:
        """主线程槽：自定义短语命中，整段注入（委托 voice_command_handlers）。"""
        voice_command_handlers.on_command_phrase(self, content)

    def _on_command_ai(self, instruction: str) -> None:
        """主线程槽：「帮我」AI 命令链（委托 voice_command_handlers）。"""
        voice_command_handlers.on_command_ai(self, instruction)

    def _on_commands_toggled(self, enabled: bool) -> None:
        """右键菜单「语音命令」开关（委托 voice_command_handlers）。"""
        voice_command_handlers.on_commands_toggled(self, enabled)

    def _apply_commands_toggle(self, enabled: bool) -> None:
        """执行语音命令启停（委托 voice_command_handlers）。"""
        voice_command_handlers.apply_commands_toggle(self, enabled)

    # ---- LLM 修正替换（实现见 core/llm_replace.py，此处保留同名薄包装） ----

    def _setup_llm_corrector(self) -> None:
        """按 cfg.llm 配置创建 LLM 修正器（委托 llm_replace）。"""
        llm_replace.setup_llm_corrector(self)

    def _on_llm_corrected(
        self, original: str, corrected: str, requested_at: float = 0.0,
    ) -> None:
        """LLM 修正完成回调：单句路径（委托 llm_replace）。"""
        llm_replace.on_llm_corrected(self, original, corrected, requested_at)
        # 修正版才是屏幕上的最终文本：更新「删除那句」基准
        self._last_spoken_text = corrected

    def _on_llm_corrected_multi(
        self, pairs: list, requested_at: float = 0.0,
    ) -> None:
        """LLM 修正完成回调：多句路径（委托 llm_replace）。"""
        llm_replace.on_llm_corrected_multi(self, pairs, requested_at)
        # 批量替换按句逐条进行，最后上屏的是最后一句的修正版
        if pairs:
            self._last_spoken_text = str(pairs[-1][1] or "")

    @Slot()
    def _do_llm_replace(self) -> None:
        """主线程槽：逐条执行排队中的修正替换（委托 llm_replace）。"""
        llm_replace.do_llm_replace(self)

    def _maybe_fire_deferred_enter(self) -> None:
        """延迟回车落地（委托 llm_replace）。"""
        llm_replace.maybe_fire_deferred_enter(self)

    def _on_deferred_enter_timeout(self) -> None:
        """延迟回车超时兜底（委托 llm_replace）。"""
        llm_replace.on_deferred_enter_timeout(self)

    def _history_record_on_screen(self, original: str, pairs) -> None:
        """补记已上屏的原文（委托 llm_replace）。"""
        llm_replace.history_record_on_screen(self, original, pairs)

    def _on_asr_error(self, code: int, message: str) -> None:
        # 阿里云 NO_VALID_AUDIO 是"没出声就结束"的预期行为（快速连点），只记日志不弹窗
        if str(code) == "NO_VALID_AUDIO_ERROR":
            logger.info("忽略空任务错误（NO_VALID_AUDIO_ERROR）：未发送音频即结束")
            return
        # 会话已不在聆听态时，服务端报错只是"提前结束任务"的副产物，仅记 WARNING
        # 不弹窗；聆听态中的错误照常弹
        if self.state != APP_STATE_LISTENING:
            logger.warning(
                "会话已结束后收到服务端错误（不弹窗）：code=%s message=%s",
                code, message,
            )
            return
        self.bar.signals.error_occurred.emit(
            t("app.err.asr", code=code, message=message))

    def _on_asr_state(self, state: str) -> None:
        # error 状态只在建连/加载失败路径出现，_connect_asr_worker 已统一弹
        # "连接失败"，这里再弹会双弹窗
        from ..asr.tencent_realtime import STATE_ERROR  # 延迟导入
        if state == STATE_ERROR:
            logger.debug("ASR 状态 error（由建连线程统一处理）")

    def _on_funasr_progress(self, percent: int, message: str) -> None:
        """FunASR 模型下载/加载进度，显示在悬浮条上。"""
        # 引擎感知：加载中切走引擎后，被抛弃的预热线程仍会继续回调，
        # 不该再往悬浮条刷进度（尤其下载场景每秒一条）
        if getattr(self.cfg, "engine", "") != "funasr":
            return
        # 每次更新刷新 3 秒，保证下载期间持续可见
        self.bar.signals.transient_hint.emit(message, 3.0)

    # ---- 配置热加载 ----

    def _on_config_file_changed(self, path: str) -> None:
        """配置文件变更（防抖入口）：合并短时间内的连续写入，只热加载一次。"""
        # 编辑器"写临时文件+替换"保存会断开监视，立即重新挂上
        if self._config_watcher and path not in self._config_watcher.files():
            try:
                self._config_watcher.addPath(path)
            except Exception:
                logger.debug("重新监视配置文件失败：%s", path, exc_info=True)
        self._pending_config_path = path
        self._config_reload_timer.start()

    def _reload_config(self) -> None:
        """防抖到期：执行一次完整热加载。"""
        path = self._pending_config_path
        self._pending_config_path = None
        if not path:
            if self._config_path:
                path = str(self._config_path)
            else:
                return
        self._on_config_changed(path)

    def _on_config_changed(self, path: str) -> None:
        """配置文件被修改，重新加载并应用。"""
        from pathlib import Path

        logger.info("检测到配置变更：%s", path)
        try:
            new_cfg = get_config_unchecked(path, default_engine=self._default_engine)
        except Exception as exc:
            logger.error("重新加载配置失败：%s", exc)
            self.bar.signals.error_occurred.emit(
                t("app.err.config_load", error=exc))
            return

        old_hotkey = self.cfg.hotkey.toggle
        # 长按开关的生效值也要在替换 cfg 之前取：on_press 是构造期定下的，热加载
        # 只换 cfg 不重建热键的话，设置页/手改 yaml 打开长按要等重启才生效
        old_hold = self._hold_to_talk_enabled()
        old_engine_type = getattr(self.cfg, "engine", "tencent")
        old_tencent_model = self.cfg.tencent.engine_model_type
        old_funasr = getattr(self.cfg, "funasr", None)  # 参数变更检测用（引擎不变时重建预热）
        old_ui = self.cfg.ui  # 主题/配色变更检测用（apply 前先对比，避免无谓重应用）
        # 分类绑定被改（设置页/手改 yaml）且当前生效引擎正是旧绑定时，
        # 生效模型字段跟随新绑定，避免保存后仍用旧模型、需重点右键菜单
        self._follow_slot_rebinding(self._engine_menu_id(self.cfg), new_cfg)
        self.cfg = new_cfg
        # 同步完整配置到悬浮条：热加载整体替换 cfg 对象，
        # bar 的密钥过滤（_full_cfg）与 cfg.ui 引用都要跟上
        self.bar.set_full_config(new_cfg)
        self.bar.cfg = new_cfg.ui

        # 更新热键（热键串或长按开关的生效值变了都要重新注册）
        if new_cfg.hotkey.toggle != old_hotkey or self._hold_to_talk_enabled() != old_hold:
            logger.info("热键变更：%s → %s（长按 %s → %s）",
                        old_hotkey, new_cfg.hotkey.toggle,
                        "开" if old_hold else "关",
                        "开" if self._hold_to_talk_enabled() else "关")
            if self.hotkey:
                self.hotkey.stop()
            self.hotkey = HotkeyListener(
                new_cfg.hotkey.toggle, self._on_toggle,
                on_press=self._request_hold_press if self._hold_to_talk_enabled() else None)
            try:
                self.hotkey.start()
            except Exception as exc:
                logger.error("热键注册失败：%s", exc)
                self.bar.signals.error_occurred.emit(
                    t("app.err.hotkey_register_short", hotkey=new_cfg.hotkey.toggle))

        # 更新注入器参数
        self.injector.method = new_cfg.injection.method
        self.injector.paste_delay = new_cfg.injection.paste_delay_ms / 1000.0
        self.injector.keep_clipboard = bool(getattr(new_cfg.injection, "keep_clipboard", True))
        self.injector.game_mode = bool(getattr(new_cfg.injection, "game_mode", False))
        self.bar.set_injection_method(new_cfg.injection.method)
        self.bar.set_keep_clipboard(bool(getattr(new_cfg.injection, "keep_clipboard", True)))
        self.bar.set_game_mode(bool(getattr(new_cfg.injection, "game_mode", False)))

        # 同步 ui 配置对象到悬浮条：引擎提供商显隐等 ui 字段随热加载刷新
        self.bar.cfg = new_cfg.ui

        # 删除键打断开关热加载
        self._update_delete_listener(
            bool(getattr(getattr(new_cfg, "ui", None), "interrupt_on_delete", False))
        )

        # LLM 修正器热加载：启用且填 key 则创建，否则置空
        llm_cfg = getattr(new_cfg, "llm", None)
        llm_on = (llm_cfg is not None and getattr(llm_cfg, "enable", False)
                  and bool(getattr(llm_cfg, "api_key", "")))
        if llm_on and self._llm_corrector is None:
            self._setup_llm_corrector()
        elif not llm_on and self._llm_corrector is not None:
            self._llm_corrector = None
            self._wait_for_correction = False
            logger.info("LLM 修正已关闭")
        elif llm_on and self._llm_corrector is not None:
            # 修正器存续期间改 wait_for_correction（设置页/手改）即时生效，
            # 否则只有重建修正器（重启/重开 AI 修正）才读得到新值
            self._wait_for_correction = bool(
                getattr(llm_cfg, "wait_for_correction", False))
        # 热加载后按生效值刷逐字同步勾选态（set_full_config 同步的是配置原值）
        self.bar.set_live_intermediate(self._live_intermediate())

        self.auto_stop_seconds = getattr(new_cfg.recording, "auto_stop_seconds", 5)

        # 语音命令配置热加载（触发词/前缀/开关即时生效）
        self._sync_voice_commands_config()
        # 命令热键随 commands.enable / commands.hotkey 热加载刷新
        self._update_cmd_hotkey_listener()
        # 对话热键随 hotkey.dialog 热加载刷新（对话进行中不重启会话，
        # 其余 dialog 配置下次进入对话时由 _start_worker 重新读取生效）
        self._update_dialog_hotkey_listener()
        # 命令被禁用时清待命态：残留待命会阻断中间帧上屏，行为不一致
        from . import voice_commands as _vc_hot
        if not _vc_hot.enabled() and self._command_standby:
            self._command_standby = False
            self.bar.signals.command_standby.emit(False)

        # 更新提示
        if self.state == APP_STATE_IDLE:
            if is_configured(new_cfg):
                self.bar.set_hint(
                    t("hint.press_to_start", hotkey=new_cfg.hotkey.toggle.upper()))
            elif getattr(new_cfg, "engine", "") == "funasr":
                self.bar.set_hint(t("hint.funasr_missing"))
            else:
                self.bar.set_hint(t("hint.no_key"))

        # 引擎类型变更：切换预热
        new_engine_type = getattr(new_cfg, "engine", "tencent")
        if new_engine_type != old_engine_type:
            logger.info("引擎类型变更：%s -> %s", old_engine_type, new_engine_type)
            if new_engine_type == "funasr":
                self._funasr_instance = None  # 丢弃旧实例，重新预热
                self.bar.signals.model_ready_changed.emit(False)
                self._preheat_funasr()
            else:
                # 云端引擎按会话建连无需预热，释放本地模型
                self._funasr_instance = None
                self.bar.signals.model_loading_changed.emit(False)
                self.bar.signals.model_ready_changed.emit(False)
                if old_engine_type == "funasr":
                    # 清掉在途的"模型加载中…"瞬态提示，避免切走后残留最多 3 秒
                    self.bar.signals.transient_hint.emit("", 0.0)
        elif new_engine_type == "funasr":
            # 引擎未变但本地模型参数变了：重建实例并重新预热，改动即时生效免重启
            of, nf = old_funasr, new_cfg.funasr
            # "默认" 是 chunk_preset 缺失时的比较哨兵（不是界面文案），故豁免 i18n：
            # 它只参与"两侧取值是否相同"的判断，永远不渲染给用户。
            _cp_default = "默认"  # noqa: i18n
            if (getattr(of, "model", "") != getattr(nf, "model", "")
                    or getattr(of, "device", "") != getattr(nf, "device", "")
                    or getattr(of, "chunk_preset", _cp_default)
                       != getattr(nf, "chunk_preset", _cp_default)
                    or getattr(of, "hotword", "") != getattr(nf, "hotword", "")):
                logger.info("FunASR 参数变更，重新预热本地模型")
                self._funasr_instance = None
                self.bar.signals.model_ready_changed.emit(False)
                self._preheat_funasr()

        # 云端引擎模型变更提示（下次录音生效）
        if new_engine_type == "tencent" and new_cfg.tencent.engine_model_type != old_tencent_model:
            logger.info(
                "引擎变更：%s → %s（下次录音生效）",
                old_tencent_model,
                new_cfg.tencent.engine_model_type,
            )
        self.bar.set_engine(self._engine_menu_id(new_cfg))

        # 颜色同步：有主题预设名就整表恢复（含 border/top_line），别只恢复基础三色。
        # 主题未变跳过重应用：重建 pixmap + 全窗 re-polish 在设置对话框事件循环里会卡
        from ..ui.theme_registry import available_themes
        ui = new_cfg.ui
        _theme_name = getattr(ui, "theme_name", "") or ""
        colors_changed = (
            getattr(ui, "bg_color", "") != getattr(old_ui, "bg_color", "") or
            getattr(ui, "text_color", "") != getattr(old_ui, "text_color", "") or
            getattr(ui, "accent_color", "") != getattr(old_ui, "accent_color", "")
        )
        theme_changed = (
            getattr(old_ui, "theme_name", "") or ""
            != _theme_name
        )
        if _theme_name and _theme_name in available_themes() and (theme_changed or colors_changed):
            self.bar.apply_theme_name(_theme_name)
        elif colors_changed:
            self.bar.apply_colors(
                getattr(ui, "bg_color", "#1c1c20"),
                getattr(ui, "text_color", "#eeeeee"),
                getattr(ui, "accent_color", "#5696e8"),
            )

        # 悬浮条不透明度热加载（无需重应用主题，仅改背景 alpha 重绘）
        try:
            new_op = int(getattr(ui, "bar_opacity", 100) or 100)
            if new_op != int(getattr(old_ui, "bar_opacity", 100) or 100):
                self.bar.set_bar_opacity(new_op)
        except Exception:
            logger.debug("bar_opacity 热加载失败", exc_info=True)

        # 字体热加载：三个字段任一变化就重应用。绘制走 get_font() 缓存，
        # app.setFont 广播 FontChange 触发重绘，再显式 update() 兜底
        font_sig = (
            str(getattr(ui, "font_file", "") or ""),
            str(getattr(ui, "font_family", "") or ""),
            int(getattr(ui, "font_size_app", 9) or 9),
        )
        old_font_sig = (
            str(getattr(old_ui, "font_file", "") or ""),
            str(getattr(old_ui, "font_family", "") or ""),
            int(getattr(old_ui, "font_size_app", 9) or 9),
        )
        if font_sig != old_font_sig:
            from ..ui.fonts import apply_default_font
            apply_default_font(
                self._app,
                family=font_sig[1],
                font_file=font_sig[0],
                app_point_size=font_sig[2],
            )
            self.bar.update()
            logger.info("字体已热加载：%s", font_sig[0] or font_sig[1] or "<默认>")

        # 信息类提示走白色临时提示，真错误才走橙色 error 通道
        self.bar.signals.transient_hint.emit(t("hint.config_updated"), 1.5)
        logger.info("配置已热加载")

        # 重新监视（部分编辑器保存后路径会断开）
        p = Path(path)
        if p.exists() and self._config_watcher:
            if str(p) not in self._config_watcher.files():
                self._config_watcher.addPath(str(p))

    @staticmethod
    def _engine_menu_id(cfg) -> str:
        """构建引擎菜单 ID（格式与 presets.get_engines 一致）：funasr:<model> /
        aliyun:<model> / xfyun:std:cn|std:en|lang / volcengine:bigasr|seedasr /
        tencent 用 engine_model_type。"""
        return settings_handlers.engine_menu_id(cfg)

    def _follow_slot_rebinding(self, old_active: str, new_cfg) -> None:
        """分类「调用」绑定被改且当前生效引擎正是旧绑定时，生效模型字段跟随新绑定。

        绑定（menu_model_<slot>）与生效模型字段（tencent.engine_model_type /
        aliyun.model）分开存储：右键菜单点击路径两者同步写，设置页/手改
        yaml 只改绑定，此处补齐另一路径，否则保存后录音仍用旧模型。
        """
        settings_handlers.follow_slot_rebinding(self, old_active, new_cfg)

    def _on_engine_changed(self, engine: str) -> None:
        """引擎切换：写配置让热加载生效。engine 带 funasr:/aliyun:/xfyun:/volcengine: 前缀，
        其余按腾讯云模型名处理。"""
        settings_handlers.on_engine_changed(self, engine)

    def _emit_switch_hint(self, ok_hint: str, fail_hint: str) -> None:
        """引擎切换结果提示统一出口：配置就绪闪现成功语，否则橙色错误通道。

        is_configured 按当前引擎组判定：云端引擎查密钥是否填写，
        funasr 查依赖可用性（对应 fail_hint 各不相同，由调用方传入）。
        """
        settings_handlers.emit_switch_hint(self, ok_hint, fail_hint)

    def _on_theme_changed(self, theme_name: str) -> None:
        """主题切换：更新颜色 + 写入配置文件。"""
        settings_handlers.on_theme_changed(self, theme_name)

    def _on_injection_method_changed(self, method: str) -> None:
        """注入方式切换（右键菜单）：立即生效并写入配置持久化。

        冲突确认：切到「剪贴输入」会强制关闭「逐字同步」——若逐字同步
        开着，先弹内嵌确认（确认才执行切换，取消维持原状）。
        游戏模式开启期间切「逐字输入」同样先确认（确认后联动关闭游戏模式）。
        """
        settings_handlers.on_injection_method_changed(self, method)

    def _apply_injection_method(self, method: str) -> None:
        """执行注入方式切换：写配置 + 逐字同步逻辑锁 + 联动确认/提示。"""
        settings_handlers.apply_injection_method(self, method)

    def _llm_config_enabled(self) -> bool:
        """AI 修正是否启用（按 cfg.llm.enable，api_key 由创建修正器处把关）。"""
        return settings_handlers.llm_config_enabled(self)

    def _commands_config_enabled(self) -> bool:
        """语音命令是否启用（读模块运行态；与配置同步，_live_intermediate 等
        多线程路径也走这里——模块 bool 读取 GIL 下安全）。"""
        return settings_handlers.commands_config_enabled(self)

    def _set_live_config(self, enabled: bool) -> None:
        """写入逐字同步配置（持久化 + 内存 cfg + 悬浮条勾选态同步）。"""
        settings_handlers.set_live_config(self, enabled)

    def _live_conflict_sources(self) -> list:
        """「逐字同步」开启路上的冲突源（llm/paste/commands/game 的子集）。"""
        return settings_handlers.live_conflict_sources(self)

    @staticmethod
    def _live_conflict_desc(sources: list) -> str:
        """冲突源 -> 确认文案的"将…"部分。

        连接符也走词条：中文用「、」+「并」，英文用 ", " + " and "，
        硬编任一种都会让另一种语言的句子读不通。
        """
        return settings_handlers.live_conflict_desc(sources)

    def _on_live_intermediate_toggled(self, enabled: bool) -> None:
        """右键菜单「逐字同步」：即时生效并持久化。

        冲突确认：开启与 AI 修正（回包整句重写叠加实时上屏会闪烁）、
        剪贴输入（整段粘贴叠加实时注入太折腾剪贴板）或语音命令
        （命令句中间帧可能因同音误识别残留在输入框、顶掉选中文本）
        冲突时，先弹确认（确认=连带关闭冲突项并开启；取消=维持原状）。
        冲突源组合任意，统一走 conflict_live（待关闭项存
        _pending_live_conflicts，确认回答时逐个执行）。
        """
        settings_handlers.on_live_intermediate_toggled(self, enabled)

    def _on_llm_enable_toggled(self, enabled: bool) -> None:
        """右键菜单「AI 修正」：即时启停并持久化。未配 api_key 拒绝开启。

        冲突确认：开启会强制关闭「逐字同步」——若逐字同步开着，先弹
        内嵌确认（确认才执行开启，取消维持原状）；开启成功后再询问
        是否连带开启「修正后上屏」。
        """
        settings_handlers.on_llm_enable_toggled(self, enabled)

    def _apply_llm_toggle(self, enabled: bool) -> None:
        """执行 AI 修正启停：写配置 + 修正器启停 + 逐字同步逻辑锁 + 联动确认。"""
        settings_handlers.apply_llm_toggle(self, enabled)

    def _on_llm_wait_confirm(self, enable_wait: bool) -> None:
        """悬浮条内嵌确认的回答：「修正后上屏」开 / 不开。"""
        settings_handlers.on_llm_wait_confirm(self, enable_wait)

    def _on_bar_confirm(self, kind: str, enabled: bool) -> None:
        """悬浮条内嵌确认统一入口：按询问类型分发。

        conflict_* = 冲突更换确认：确认→执行切换；取消→保持原状。
        game_mode_elevate = 游戏模式开启后的提权询问（非冲突类）。"""
        settings_handlers.on_bar_confirm(self, kind, enabled)

    def _on_live_sync_confirm(self, enabled: bool) -> None:
        """「逐字同步」确认回答：开启即生效（询问时已确认无锁阻碍）；
        暂不则保持关闭。"""
        settings_handlers.on_live_sync_confirm(self, enabled)

    def _on_keep_clipboard_toggled(self, enabled: bool) -> None:
        """保留剪贴开关（右键菜单切换）：立即生效并写入配置持久化。"""
        settings_handlers.on_keep_clipboard_toggled(self, enabled)

    def _on_pause_bg_audio_toggled(self, enabled: bool) -> None:
        """右键菜单「暂停播放」开关：接口可用才落盘；下次录音生效。"""
        settings_handlers.on_pause_bg_audio_toggled(self, enabled)

    def _on_hold_to_talk_toggled(self, enabled: bool) -> None:
        """右键菜单切换长按录音：写配置 + 重建热键（on_press 有无随之切换）+ 提示。

        热键必须重建：HotkeyListener 的 on_press 在构造时定下，改配置不会改变已注册
        那个实例的分派（非 None 时它取代 on_trigger），不重建就仍是旧行为。
        """
        settings_handlers.on_hold_to_talk_toggled(self, enabled)

    def _on_game_mode_toggled(self, enabled: bool) -> None:
        """右键菜单「游戏模式」开关：冲突确认后执行。

        冲突确认：开启会强制关闭「逐字同步」——若逐字同步开着，先弹
        内嵌确认（确认才执行开启，取消维持原状）。
        """
        settings_handlers.on_game_mode_toggled(self, enabled)

    def _apply_game_mode_toggle(self, enabled: bool) -> None:
        """执行游戏模式启停：写配置 + 注入器/悬浮条同步 + 注入方式/逐字同步逻辑锁。"""
        settings_handlers.apply_game_mode_toggle(self, enabled)

    def _apply_method_lock(self, method: str) -> None:
        """游戏模式注入方式锁的强制切换/恢复：四处同步注入方式，
        不走 _apply_injection_method（避免连带触发逐字同步锁/询问）。"""
        settings_handlers.apply_method_lock(self, method)

    def _on_interrupt_delete_toggled(self, enabled: bool) -> None:
        """删除键打断开关（右键菜单切换）：即时启停监听，配置已由 bar 写入。"""
        settings_handlers.on_interrupt_delete_toggled(self, enabled)

    def _on_auto_hide_toggled(self, enabled: bool) -> None:
        """自动隐藏开关：立即生效并写入配置持久化。"""
        settings_handlers.on_auto_hide_toggled(self, enabled)

    def _on_always_on_top_toggled(self, enabled: bool) -> None:
        """永远置顶开关：窗口标志已由 bar 切换，这里写配置持久化。"""
        settings_handlers.on_always_on_top_toggled(self, enabled)

    def _on_autostart_toggled(self, enabled: bool) -> None:
        """开机自启开关：写/删 HKCU Run 注册表键（用户级，无需管理员）。

        `set_enabled` 返回的是 i18n 键（该模块不接触界面语言），在这里翻译。
        """
        settings_handlers.on_autostart_toggled(self, enabled)

    def _on_font_file_changed(self, font_path: str) -> None:
        """用户通过右键菜单选了字体文件：复制到程序目录、加载、写入配置。"""
        settings_handlers.on_font_file_changed(self, font_path)

    # ---- UI 事件 ----

    def _on_elevate(self) -> None:
        """程序提权（右键菜单「程序提权」）：拉起管理员实例接管后退出当前实例。

        已是管理员则仅提示；用户拒绝 UAC 则留在当前实例并报错。
        """
        from .elevation import is_elevated, relaunch_elevated
        if is_elevated():
            self.bar.signals.transient_hint.emit(t("hint.already_admin"), 2.0)
            return
        ok, msg = relaunch_elevated()
        if ok:
            self._restart_for_elevation()
        else:
            logger.warning("提权重启未完成：%s", msg)
            self.bar.signals.error_occurred.emit(t("app.err.elevate_failed", error=msg))

    def _restart_for_elevation(self) -> None:
        """提权实例已拉起：清理并退出当前实例（新实例经 --replace 接管单实例锁）。"""
        logger.info("提权实例已接管，当前实例退出")
        self._on_exit()

    def _on_exit(self) -> None:
        logger.info("用户请求退出")
        self._cleanup()
        if self._app:
            self._app.quit()

    def _on_hide_to_tray(self) -> None:
        """隐藏悬浮条到托盘，程序继续后台运行。"""
        if self.bar:
            self.bar.hide()

    def _setup_tray(self) -> None:
        """创建系统托盘图标与交互。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统托盘不可用，跳过托盘创建")
            return

        self._tray = QSystemTrayIcon(app_icon(), self._app)
        self._tray.setToolTip(t("app.tray_tooltip"))
        self._tray.activated.connect(self._on_tray_activated)
        # 托盘右键菜单复用悬浮条的统一菜单
        self._tray.setContextMenu(self.bar.build_menu(self._tray))
        self._tray.show()
        logger.info("系统托盘已创建")
        # Windows 没有「让托盘图标默认可见」的接口：任何没被用户手动允许过的
        # exe 都会被收进任务栏溢出区（新装的打包版 / 商店版因此默认看不见，
        # 而源码版跑的 python.exe 早就允许过，所以看起来"只有打包版没图标"）。
        # 系统只在**弹出通知**时把该图标临时提升到托盘区，故首次启动借一次
        # 气泡让它自己露面，之后不再打扰。
        self._promote_tray_once()

    def _promote_tray_once(self) -> None:
        """首次启动弹一次气泡，借系统「弹通知 = 提升图标」的行为让托盘露面。

        只做一次（标志落在状态目录，与欢迎窗共用 welcome_dialog 的 _mark）；
        `--hidden` 开机自启不弹，免得每次开机打扰。
        """
        if self._tray is None or self._start_hidden:
            return
        # 延迟导入：welcome_dialog 会带起 config.loader，顶层导入与 core.app 成环
        from ..ui.welcome_dialog import is_guided, mark_guided

        if is_guided("tray_promote"):
            return
        mark_guided("tray_promote")
        # 延后一点：托盘刚 show 时系统未必登记完，立刻弹容易被吞
        QTimer.singleShot(1500, self._show_tray_promote)

    def _show_tray_promote(self) -> None:
        if self._tray is None:
            return
        self._tray.showMessage(
            t("app.tray_promote_title"),
            t("app.tray_promote_hint"),
            QSystemTrayIcon.Information,
            5000,
        )

    def _on_tray_activated(self, reason) -> None:
        """托盘交互：双击=显隐悬浮条；单击=可见时切换录音，隐藏时只显示。"""
        if reason == QSystemTrayIcon.DoubleClick:
            if self.bar:
                if self.bar.isVisible():
                    self.bar.hide()
                else:
                    self.bar.show()
                    self.bar.raise_()
                    self.bar._restart_auto_hide()
        elif reason == QSystemTrayIcon.Trigger:
            # 悬浮条隐藏时，单击托盘只显示，不进入录音模式
            if self.bar and not self.bar.isVisible():
                self.bar.show()
                self.bar.raise_()
                self.bar._restart_auto_hide()
                return
            self._on_toggle()

    def _accrue_steam_session(self) -> None:
        """把本次会话时长计入 平台统计（只记数字）。

        以 `_session_start_time` 去重：停止路径有好几条（热键、自动静音、
        引擎切换、退出），同一次会话被重复结算时只算一次——新的会话会有
        新的起始时间，所以按起始时间比对就够，不必额外置位。
        """
        stats = getattr(self, "_steam_stats", None)
        if stats is None:
            return
        started = float(getattr(self, "_session_start_time", 0.0) or 0.0)
        if not started or started == getattr(
                self, "_steam_session_counted_at", 0.0):
            return
        self._steam_session_counted_at = started
        try:
            stats.add_session(max(0.0, time.time() - started))
        except Exception:
            logger.debug("Steam 会话统计失败", exc_info=True)

    def _flush_steam_cloud(self) -> None:
        """上传云存档（脱敏配置 + 历史）+ 统计落盘。失败只记日志。

        调用点：设置保存后、退出时。刻意不做定时节流：真正会变的只有配置和
        历史，而这两者都发生在用户动作之后，平台自己也会合并写入请求。
        """
        # 一律 getattr：_cleanup 会被「只造了半个 VoiceApp」的测试调用
        steam = getattr(self, "_steam", None)
        if steam is None or not steam.available:
            return
        from pathlib import Path

        from . import history as _history

        from ..steam_integration import build_cloud_files

        try:
            cfg_dict = {}
            if self._config_path:
                import yaml

                cfg_dict = yaml.safe_load(
                    Path(self._config_path).read_text(encoding="utf-8")) or {}
            include_keys = bool(getattr(
                getattr(self.cfg, "steam", None), "cloud_sync_keys", False))
            files = build_cloud_files(
                cfg_dict, _history.entries(), include_keys=include_keys)
            for name, text in files.items():
                steam.cloud_write(name, text.encode("utf-8"))
            stats = getattr(self, "_steam_stats", None)
            if stats is not None:
                stats.flush(steam)
        except Exception:
            logger.debug("云存档上传失败", exc_info=True)

    def _open_settings(self) -> None:
        """非模态打开设置对话框（打开期间悬浮条仍可拖动但右键菜单禁用），
        保存后由热加载生效。

        不透明度滑块实时预览，取消/未保存关闭时恢复原值；单例守卫：
        已打开则置前返回，不叠开第二窗。
        """
        from ..ui.settings_dialog import SettingsDialog

        if getattr(self, "_settings_dlg", None) is not None:
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
            return
        dlg = SettingsDialog(self.cfg, parent=self.bar)
        self._settings_dlg = dlg
        # 设置打开期间禁用悬浮条右键菜单（contextMenuEvent 守卫读取）
        self.bar._settings_dlg_open = True
        # 记录原始透明度，取消时恢复
        orig_opacity = int(getattr(self.cfg.ui, "bar_opacity", 0) or 0)
        dlg.bar_opacity_preview.connect(self.bar.set_bar_opacity)
        # 「永久为管理员启动」开启并确认 UAC 后：提权实例已接管，当前实例退出
        dlg.restart_requested.connect(self._restart_for_elevation)

        def _on_finished(_result: int) -> None:
            self._settings_dlg = None
            self.bar._settings_dlg_open = False
            n = getattr(dlg, "saved_count", 0)
            if n > 0:
                self.bar.signals.transient_hint.emit(
                    t("hint.settings_saved", count=n), 1.5)
                # 存过盘就把云存档重传一次：设置里可能刚改了 engine、
                # 云存档键白名单开关，这些都在云文件里
                self._flush_steam_cloud()
            else:
                # 取消/未保存：恢复原透明度（预览改动不持久化）
                self.bar.set_bar_opacity(orig_opacity)
                self.bar.signals.transient_hint.emit(t("hint.settings_unchanged"), 1.5)
            dlg.deleteLater()

        dlg.finished.connect(_on_finished)
        dlg.show()
        dlg.activateWindow()

    def _open_history_window(self) -> None:
        """打开历史记录独立窗口（单例复用；实时监听新增/清空）。"""
        if getattr(self, "_history_win", None) is None:
            from ..ui.history_window import HistoryWindow

            self._history_win = HistoryWindow(self.cfg, parent=self.bar)
        self._history_win.show()
        self._history_win.raise_()
        self._history_win.activateWindow()

    def _cleanup(self) -> None:
        # 单实例锁释放：关闭服务端并移除命名管道（避免残留导致下次无法 listen）
        if self._instance_server is not None:
            try:
                self._instance_server.close()
                QLocalServer.removeServer(self._INSTANCE_KEY)
            except Exception:
                logger.debug("单实例锁清理异常", exc_info=True)
            self._instance_server = None
        if self.hotkey:
            self.hotkey.stop()
        if self.cmd_hotkey:
            try:
                self.cmd_hotkey.stop()
            except Exception:
                logger.debug("命令热键注销异常", exc_info=True)
            self.cmd_hotkey = None
        # 语音对话：同步收尾（正常 <1s；FinishSession 超时容忍路径最多 ~10s）
        if self.dialog_hotkey:
            try:
                self.dialog_hotkey.stop()
            except Exception:
                logger.debug("对话热键注销异常", exc_info=True)
            self.dialog_hotkey = None
        if getattr(self, "dialog", None) is not None:
            # 最后一轮对话先落库：退出路径上 queued 的 idle 槽可能已经跑不到，
            # 不补这一下，最后一问一答就白说了（recorder.flush 幂等）
            try:
                self._dialog_history.flush()
            except Exception:
                logger.debug("对话历史落库异常", exc_info=True)
            try:
                self.dialog.shutdown()
            except Exception:
                logger.debug("对话收尾异常", exc_info=True)
        for listener in self.delete_listeners:
            listener.stop()
        # 暂停播放/全局静音：退出兑底恢复（退出路径不经过 stop_session）
        try:
            self.media_pauser.resume()
        except Exception:
            logger.debug("后台音频恢复异常", exc_info=True)
        if self.capture:
            self.capture.stop()
        if self.asr:
            try:
                self.asr.stop()
            except Exception:
                pass
        # 平台：退出前补记最后一段会话时长 + 上传云存档/统计。
        # 退出路径不经过 _stop_session，不补这一下最后一段会话就白录了。
        # 能力层没起来时三步全是 no-op。
        self._accrue_steam_session()
        self._flush_steam_cloud()
        timer = getattr(self, "_steam_timer", None)
        if timer is not None:
            timer.stop()
            self._steam_timer = None
        if self._tray:
            self._tray.hide()
            self._tray = None
        # 长按采样定时器：退出路径一并停表并清状态——长按中途退出时定时器仍在跑，
        # 而 _on_exit 之后到 exec() 返回之间 Qt 事件循环还会转，残留的采样会再触发
        # 一次录音启停；清空也免得状态跨实例残留
        self._stop_hold_watch()
        self._hold_tracker = None
        self._hold_active = False
        self._hold_owns_session = False
        # 延后开始的收尾定时器同理：退出路径上它若再响一次，会去 _on_toggle 一个
        # 正在拆的会话（而 _toggle 在待机态是「开始录音」）
        if self._pending_start_timer is not None:
            self._pending_start_timer.stop()
        self._hold_pending_start = False
