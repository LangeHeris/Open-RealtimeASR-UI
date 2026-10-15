"""实时语音对话控制器（豆包端到端 S2S）—— 状态机与编排层。

采集 / DoubaoDialogClient / AudioPlayer 由控制器自持（不复用 app.capture：对话要 20ms/包低延迟上行，
不吃输入模式的预滚/校准/本地 VAD）。回调线程直接执行逻辑，UI 全经 Qt 信号 queued 到主线程；三处刻意
不绕主线程（绕主线程排队会积压或丢毫秒级时序）：_handle_audio→player.write、450→player.clear()+闸门
翻转、_on_mic_block→send_audio。

音频闸门（barge-in 第二道防线）：352 是纯音频帧拿不到 reply_id，无法按轮过滤，不关就会被旧轮尾部音频
继续塞进播放器（「明明打断了，AI 又接着说半句」）；重开条件是新 350 且 reply_id 与被作废值不同。
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..asr.doubao_dialog_codec import DIALOG_BLOCK_FRAMES
from ..asr.dialog_events import (
    AI_FULL_TEXT,
    AI_TRANSCRIPT,
    AI_TURN_END,
    AI_TURN_START,
    BARGE_IN,
    SESSION_READY,
    USER_TRANSCRIPT,
    USER_TURN_END,
    DialogError,
    DialogErrorEvent,
)
from ..config.loader import (
    build_qwen_ws_url,
    dialog_configured,
    dialog_enabled,
    dialog_provider,
    resolve_dialog_credentials,
    resolve_qwen_credentials,
)
from ..hotkey import probe as _hold_probe
from ..hotkey.hold import HoldAction, HoldTracker, hold_flag_enabled
from ..i18n import t
from ..paths import state_dir

logger = logging.getLogger(__name__)

# ---- 内部状态机（UI 态字符串经 state_changed 信号下发，回落 "idle"）----
DIALOG_STATE_IDLE = "idle"
DIALOG_STATE_CONNECTING = "connecting"
DIALOG_STATE_LISTENING = "listening"
DIALOG_STATE_THINKING = "thinking"
DIALOG_STATE_SPEAKING = "speaking"
DIALOG_STATE_STOPPING = "stopping"

# 对话 UI 态字符串（Plan 4 的 floating_bar 渲染分支从本模块 import 这几个
# 常量，单一事实源；Plan 3 期间 bar 不认识它们：DOT_COLOR_MAP.get 兜底
# accent_dim、_apply_state 无命中分支不崩，文本显示不受影响）
DIALOG_UI_CONNECTING = "dialog_connecting"
DIALOG_UI_LISTENING = "dialog_listening"
DIALOG_UI_THINKING = "dialog_thinking"
DIALOG_UI_SPEAKING = "dialog_speaking"

# 对话四态全集（floating_bar 的状态分流/会话清零、bar_widgets 的「语音模式下
# 不画未定稿光标」共用这一份，新增对话态时只改这里）
DIALOG_UI_STATES = frozenset((DIALOG_UI_CONNECTING, DIALOG_UI_LISTENING,
                              DIALOG_UI_THINKING, DIALOG_UI_SPEAKING))

_UI_STATE_MAP = {
    DIALOG_STATE_IDLE: "idle",
    DIALOG_STATE_CONNECTING: DIALOG_UI_CONNECTING,
    DIALOG_STATE_LISTENING: DIALOG_UI_LISTENING,
    DIALOG_STATE_THINKING: DIALOG_UI_THINKING,
    DIALOG_STATE_SPEAKING: DIALOG_UI_SPEAKING,
    # STOPPING 不映射：收尾期间 UI 停留原态，完成即回 idle（收尾 <1s）
}

# 声纹电平归一化（与 app.LEVEL_NORMALIZE 同值：RMS 3000 视为满电平；
# 本地定义避免 import app 拉起整个主程序模块链）
LEVEL_NORMALIZE = 3000.0

# SPEAKING → LISTENING 排空轮询间隔（spec：50ms 对 UI 足够）
DRAIN_INTERVAL_MS = 50

# dialog_id 跨进程续接文件（state_dir 下的 JSON）
_CONTEXT_FILE = state_dir() / "dialog_context.json"

# 一次性耳机提示标记：进程内全局（spec：不占配置项、不持久化、不重复打扰）
_HEADPHONE_HINTED = False


def _safe_float(value, default: float) -> float:
    """配置数值兜底：非法值（空串/非数字/nan/inf）回默认，不让 float() 异常
    把整次对话启动炸成不可读的英文报错，也不让 NaN/Infinity 这类非法 JSON
    直达服务端（UI 已拦截，此为手改 yaml 的兜底）。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _make_asr_factory(cfg, engine: str, overrides: dict):
    """返回一个 asr_factory 闭包：按引擎名造该家的实时识别客户端。

    只做「引擎名 → 构造函数」的映射与参数搬运，不含任何编排逻辑；
    编排全在 HermesDialogClient 内部。overrides 为空时该引擎走常规配置。
    """

    def _factory(*, on_result=None, on_error=None):
        if engine == "tencent":
            from ..asr.tencent_realtime import TencentRealtimeASR
            # 局部名不能叫 `t`：本模块顶部 import 了 i18n 的 t()，同名局部
            # 会把下方的 t("...") 遮蔽成 UnboundLocalError（同 floating_bar
            # 那次踩过的坑，守卫见 test_i18n.NoShadowingOfTranslationHelper）。
            tencent_cfg = getattr(cfg, "tencent", None)
            return TencentRealtimeASR(
                app_id=str(getattr(tencent_cfg, "app_id", "")),
                secret_id=str(getattr(tencent_cfg, "secret_id", "")),
                secret_key=str(getattr(tencent_cfg, "secret_key", "")),
                engine_model_type=overrides.get(
                    "model", str(getattr(tencent_cfg, "engine_model_type",
                                          "16k_zh_en_2.0"))),
                hotword_id=overrides.get(
                    "hotword", str(getattr(tencent_cfg, "hotword_id", ""))),
                on_result=on_result, on_error=on_error)
        if engine == "aliyun":
            from ..asr.aliyun_realtime import AliyunRealtimeASR
            a = getattr(cfg, "aliyun", None)
            return AliyunRealtimeASR(
                api_key=str(getattr(a, "api_key", "")),
                model=overrides.get("model", str(getattr(a, "model", ""))),
                language_hints=overrides.get(
                    "lang", str(getattr(a, "language_hints", ""))),
                vocabulary_id=overrides.get(
                    "hotword", str(getattr(a, "vocabulary_id", ""))),
                on_result=on_result, on_error=on_error)
        # 其余引擎（funasr / volcengine / xfyun）本次不铺开：直接报错让用户
        # 去改成已支持的引擎，比静默用错引擎好排查
        raise DialogError(
            t("dialog.err.unsupported_engine", engine=engine))

    return _factory


# 轮次说话人（dialog_turn 的 role 字段）：UI 画角色色带/标签用，不猜前缀
ROLE_USER = "user"
ROLE_AI = "ai"


class DialogController(QObject):
    """语音对话控制器：状态机 + 采集/上行/下行编排 + barge-in + 半双工。

    app 为 VoiceApp 实例（读 cfg / 录音状态 / 共享 media_pauser）；
    单测传入等形 SimpleNamespace 即可（见 tests/test_dialog_controller.py）。
    """

    # UI 通知信号（跨线程 emit，Qt 自动 queued 到主线程）
    state_changed = Signal(str)          # dialog_* / idle
    # 一轮对话文本：轮次号 / role(user|ai) / 文本 / 是否定稿 / 是否被打断。
    # turn：同一轮会推很多次（豆包按句、Qwen 按 delta），UI 靠它判断覆盖当前行还是另起一行
    # interrupted：barge-in 时原样重发一次并置位，UI 据此划线
    dialog_turn = Signal(int, str, str, bool, bool)
    level_changed = Signal(float)        # 声纹电平 0~1
    error_occurred = Signal(str)         # 中文错误提示（单行）
    transient_hint = Signal(str, float)  # 一次性提示
    # 内部信号：请求在主线程启停排空轮询。worker 线程直调 QTimer.start()
    # 会被 Qt 拒绝（见 _apply_drain_poll）；主线程 emit = 直连同步
    # worker 线程 emit = 排队到主线程执行
    _drain_poll = Signal(bool)
    # 内部信号：请求在主线程建长按采样表。热键回调在工作线程，而 QTimer 的线程亲和性
    # 是「创建它的那个线程」——就地建表拿到的是 isActive() 恒假、永不触发的定时器，
    # 压着不放也永远越不过阈值、闸门打不开。与 app 侧 hold_press_requested 同路数。
    _hold_press_queued = Signal()

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._state = DIALOG_STATE_IDLE
        # 轮次跟踪：UI 靠轮次号区分"新的一轮"与"同一轮的增量"
        self._turn_seq = 0                   # 轮次号（只在说话人变化时自增）
        self._turn_role = ""                 # 当前轮次的说话人
        self._last_text = ""                 # 最近一次下发的文本（barge-in 重发用）
        self._last_role = ""
        self._lock = threading.Lock()        # toggle 竞态守卫
        self._client = None
        self._player = None
        self._capture = None
        self._dialog_id = ""                 # 150 返回，续接上下文
        self._session_provider = ""          # 本会话 provider（重连 id 门控）
        self._current_reply_id = ""          # 最近一次 350 的 reply_id
        self._invalidated_reply_id = ""      # barge-in 作废的 reply_id
        self._audio_gate_closed = False      # barge-in 音频闸门
        # 长按说话（可选）：上行闸门。与 _audio_gate_closed 分开——那个是
        # barge-in 后丢弃在途旧轮音频，这个是「用户没按住就不上行」
        # 两者语义不同，合并会让 barge-in 的恢复逻辑误开长按闸门。
        self._hold_uplink_open = False
        self._hold_tracker = None            # HoldTracker（主线程持有）
        self._hold_timer = None              # 采样 QTimer（主线程创建）
        self._hold_vk = 0                    # 对话热键主键虚拟键码（hold_enabled 刷新）
        # 本次按住实际要采样的键码：0 = 自解析对话热键（见 hold_press(vk)）。按住录音
        # 热键时按下的与控制器自解析的不是同一个键，采错就是一道永远打不开的死闸门；
        # 穿透进来的键码只活一次按住，不覆盖 _hold_vk 缓存。
        self._hold_press_vk = 0
        # _on_mic_block 只读这两个缓存位（采集回调线程不得解析配置/热键串）：
        # _hold_gate = 长按模式是否生效（hold_enabled 写入，见其 docstring）；
        # _hold_client_gate = 客户端自带上行闸门（Hermes，_attach_client 写入）。
        self._hold_gate = False
        self._hold_client_gate = False
        self._tts_ended = False              # 359 已到（排空判定的一半）
        # 本轮 AI 回复是否已开始（AI_TURN_START 置位 / USER_TURN_END 清空）：
        # THINKING 自动收尾的轮次门槛，挡住上一轮迟到的事件
        self._ai_turn_started = False
        # D1：本轮 AI 文本门闩。豆包这一版 AI 正文只在 550（ChatResponse）的 content 里、
        # 350 不带 text，_ai_text_shown = 本轮有 AI_TRANSCRIPT 上过屏，_ai_full_text_seen =
        # 本轮收到过非空 AI_FULL_TEXT；两者在 USER_TURN_END / 换 reply_id 的 AI_TURN_START
        # 复位，供 AI_TURN_END 判断本轮有没有字上屏，没有就打 WARNING。
        self._ai_text_shown = False
        self._ai_full_text_seen = False
        self._reconnect_used = False         # 本轮对话已用过一次自动重连
        self._start_thread = None
        self._stop_thread = None
        self._reconnect_thread = None
        self._drain_timer = QTimer(self)     # 常驻轮询，_check_drain 内自守卫
        self._drain_timer.setInterval(DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._check_drain)
        self._drain_poll.connect(self._apply_drain_poll)
        self._hold_press_queued.connect(self._start_hold_watch)

    # ---- 生命周期查询 ----

    def is_active(self) -> bool:
        """对话是否进行中（含连接中/收尾中，用于两模式互斥）。"""
        return self._state != DIALOG_STATE_IDLE

    # ---- 状态机 ----

    def _set_state(self, state: str) -> None:
        """内部态迁移 + 下发 UI 态信号（STOPPING 不下发，见 _UI_STATE_MAP）。"""
        if state == self._state:
            return
        self._state = state
        ui = _UI_STATE_MAP.get(state)
        if ui is None:
            return
        logger.info("对话状态：%s", state)
        self.state_changed.emit(ui)

    def _enter_listening(self) -> None:
        """进入聆听：只发状态，不发文本。

        状态提示由 UI 侧的 _apply_state 按状态渲染，不占用 dialog_turn——那条通道
        专供对话内容，否则状态会把刚显示的内容顶掉（气泡内容闪烁）。
        """
        self._set_state(DIALOG_STATE_LISTENING)

    def _begin_turn(self, role: str) -> int:
        """切换说话人即新的一轮，返回轮次号。

        同一轮内会被反复调用，只有 role 真正变化时才自增；UI 靠轮次号是否变化判断
        「覆盖当前行」还是「另起一行」。
        """
        if role != self._turn_role:
            self._turn_seq += 1
            self._turn_role = role
        return self._turn_seq

    def _emit_turn(self, text: str, is_final: bool,
                   interrupted: bool = False) -> None:
        """下发一轮文本（角色前缀由 UI 画，不再拼进字符串）。"""
        self._last_text = text
        self._last_role = self._turn_role
        self.dialog_turn.emit(self._turn_seq, self._turn_role, text,
                              bool(is_final), bool(interrupted))

    # ---- client 回调（asyncio 线程直调；UI 经信号转主线程）----

    def _attach_client(self, client) -> None:
        """绑定 client 的 4 回调（_create_client 后调用；单测也走这里注入）。"""
        self._client = client
        # 客户端自带上行闸门（Hermes 的 set_uplink_open）：长按闸门关闭时不能
        # 把帧拦在控制器里，得让它自己去补静音保活，见 _on_mic_block。
        # 这里算一次就够：客户端每次重建都必经本方法，采集回调线程只读缓存位。
        self._hold_client_gate = callable(getattr(client, "set_uplink_open", None))
        client.on_state = self._handle_client_state
        client.on_event = self._handle_event
        client.on_audio = self._handle_audio
        client.on_error = self._handle_error

    def _handle_client_state(self, state: str) -> None:
        """client 连接状态回调。session_ready 已由 150 事件处理；这里只管断连。"""
        if state != "disconnected":
            return
        if self._state in (DIALOG_STATE_IDLE, DIALOG_STATE_STOPPING):
            return  # 主动收尾路径，_stop_worker 统一收场
        # 意外断连：不自动重连（重连只在明确错误码路径），直接收场
        logger.warning("对话连接意外断开")
        self.error_occurred.emit(t("dialog.err.connection_lost"))
        self._begin_stop()

    def _handle_event(self, neutral: str, payload: dict) -> None:
        """中立语义事件 → 状态机（spec 表 A/B 归一后，控制器不认识提供商码）。"""
        if neutral == SESSION_READY:
            self._dialog_id = str(payload.get("dialog_id", ""))
            self._persist_dialog_id()
            self._enter_listening()
        elif neutral == USER_TRANSCRIPT:
            text = str(payload.get("text", ""))
            if text:
                # is_final：用户中间结果 False、最终 True（客户端翻译层算好）
                self._begin_turn(ROLE_USER)
                self._emit_turn(text, bool(payload.get("is_final", False)))
        elif neutral == USER_TURN_END:
            # 同 _enter_listening：状态只走 state_changed（UI 画 spinner +「思考中...」），
            # 用户刚才那句话留在屏上。进入新一轮即复位两个排空标志：上一轮 359 会残留成
            # True，而 THINKING 也是可完结态（无音频 provider 的唯一出口，见 _check_drain），
            # 不复位会被上一轮提前收尾；_ai_turn_started 清空后本轮要等自己的 AI_TURN_START。
            self._tts_ended = False
            self._ai_turn_started = False
            # D1：新一轮 = 重新判定"本轮有没有 AI 文本"
            self._ai_text_shown = False
            self._ai_full_text_seen = False
            self._set_state(DIALOG_STATE_THINKING)
        elif neutral == AI_TURN_START:
            # 不切态：SPEAKING 只由首个音频帧触发（无音频的异常路径不能永久
            # 卡 SPEAKING）。这里记 reply_id + 复位排空标志 + 按轮重开音频闸门
            reply_id = str(payload.get("reply_id", ""))
            if reply_id and reply_id != self._current_reply_id:
                # D1：换 reply_id = 换了一轮 AI 回复（豆包每个 350 句子都发
                # AI_TURN_START，同一 reply_id 的后续句子不算新一轮）⇒ 复位文本
                # 门闩。这里刻意不能无条件复位：最后一句 350 没带 text 时
                # 无条件复位会抹掉"前面已经上过屏"的事实，AI_TURN_END 会误报。
                self._ai_text_shown = False
                self._ai_full_text_seen = False
            self._current_reply_id = reply_id
            # 本轮回复已开始：THINKING 的自动收尾以它为轮次门槛
            self._ai_turn_started = True
            # 上一轮 359 可能残留 True：不复位的话，本轮句间瞬时空档会被
            # _check_drain 误判「已排空」提前切 LISTENING（半双工下还会
            # 误开上行闸门）——排空判定必须等本轮 AI_TURN_END
            self._tts_ended = False
            if (self._audio_gate_closed
                    and self._current_reply_id != self._invalidated_reply_id):
                self._audio_gate_closed = False
                logger.info("音频闸门重开（新 reply_id=%s）",
                            self._current_reply_id)
        elif neutral == AI_TRANSCRIPT:
            # gap 1：文本显示直接由 AI_TRANSCRIPT 驱动（累积责任在客户端）
            # 不再等首个音频帧。豆包每句 350 刷新一次、Qwen 流式 delta 刷新
            text = str(payload.get("text", ""))
            if text:
                self._ai_text_shown = True       # D1：本轮有 350 文本上过屏
                self._begin_turn(ROLE_AI)
                self._emit_turn(text, True)      # 客户端已累积，每次都是全量
        elif neutral == BARGE_IN:
            self._handle_barge_in(payload)
        elif neutral == AI_TURN_END:
            if bool(payload.get("exit_intent", False)):
                logger.info("服务端识别到退出意图，自动挂断")
                self._begin_stop()
                return
            self._tts_ended = True
            logger.info("AI_TURN_END：服务端本轮播报结束，等本地缓冲排空")
            if not self._ai_text_shown and not self._ai_full_text_seen:
                # 可观测性：这一轮两条文本通道都没内容 —— 真机上就是「有声音
                # 没文字」。留一条 WARNING（用户日志级别是 INFO，DEBUG 看不到）。
                logger.warning(
                    "本轮 AI 没有任何文本上屏（350 未带 text、550 也未带 content）"
                    "—— 若出现「有声音没文字」，请查该 provider 的文本事件")
        elif neutral == AI_FULL_TEXT:
            # D1：豆包这一版的 AI 正文在 550（ChatResponse）的 content 里，350 到了
            # 但没有 text。旧实现这里只写 DEBUG 日志（生产日志级别 INFO ⇒ 看不见）
            # 文本也永不上屏，用户看到的就是「只有我说的话」。
            text = str(payload.get("text", ""))
            # 可观测：提到 INFO，打出长度 + 前 40 字（下次真机排障靠这行确认）
            logger.info("AI 整轮完整文本（AI_FULL_TEXT）：len=%d 前 40 字=%s",
                        len(text), text[:40])
            if not text:
                return                       # 空 content 不上屏（简报要求）
            if self._audio_gate_closed:
                # barge-in 作废轮的迟到 550：闸门只由新 reply_id 的 AI_TURN_START
                # 重开，此刻还关着就说明这条属于被打断的旧轮。上屏会凭空开一个 AI
                # 轮次，把用户正在说的新一轮顶到上行（旧实现只记日志，无此风险）。
                logger.info("AI_FULL_TEXT 属 barge-in 作废轮（音频闸门关闭），不上屏")
                return
            self._ai_full_text_seen = True
            # 兜底上屏：本轮还没上过 AI 文本就用它开一轮；已有 350 文本则用整轮全文
            # 覆盖同一 AI 轮（_begin_turn(ROLE_AI) 角色没变 ⇒ 轮次号不自增，UI 按
            # "同一轮"覆盖当前行，属改进不是重复）。
            self._begin_turn(ROLE_AI)
            self._emit_turn(text, True)
        else:
            logger.info("未消费的中立事件 %s：%s", neutral, str(payload)[:100])

    def _handle_barge_in(self, payload: dict) -> None:
        """450 ASRInfo：毫秒级联动，刻意不绕主线程（spec 例外 2）。

        player.clear() 丢弃未播缓冲 → 关音频闸门（在途旧轮 352 拿不到 reply_id，
        无法按轮过滤）→ 复位排空标志 → 状态信号切 LISTENING。
        """
        if self._player is not None:
            self._player.clear()
        self._audio_gate_closed = True
        self._invalidated_reply_id = self._current_reply_id
        self._tts_ended = False
        # 被打断的那条 AI 文本原样重发一次并带 interrupted=True：UI 据此划线。
        # 不另开信号——一个通道承载全部显示语义，UI 少一条并行状态。
        if self._last_role == ROLE_AI and self._last_text:
            self._emit_turn(self._last_text, True, interrupted=True)
        if self._state == DIALOG_STATE_SPEAKING:
            self._set_state(DIALOG_STATE_LISTENING)
        logger.info("barge-in：已清播放缓冲并关音频闸门")

    def _handle_audio(self, pcm: bytes) -> None:
        """音频帧：闸门判断 → 播放 → 首个音频帧切 SPEAKING（文本另由 AI_TRANSCRIPT 驱动）。"""
        if self._audio_gate_closed:
            return  # barge-in 后在途的旧轮音频，丢弃
        if self._state in (DIALOG_STATE_THINKING, DIALOG_STATE_LISTENING):
            # 音频是「真的开始播报」的硬证据（AI_TURN_START 不是：无音频的
            # 异常路径会永久卡 SPEAKING）
            self._set_state(DIALOG_STATE_SPEAKING)
        if self._player is not None:
            self._player.write(pcm)

    def _handle_error(self, err: DialogErrorEvent) -> None:
        """会话期错误（client 已过滤握手期与收尾期）：可重连的先重连。

        err 是中立 DialogErrorEvent；错误码分类已下沉到客户端，控制器只保留「是否
        重连」的决策（auto_reconnect + 每轮一次限额）。
        """
        logger.error("对话错误 code=%s：%s（提示：%s）",
                     err.code, err.message, err.hint)
        if self._should_reconnect(err.reconnectable):
            self._reconnect_used = True
            self.transient_hint.emit(t("dialog.hint.reconnecting"), 2.0)
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_worker, daemon=True,
                name="dialog-reconnect")
            self._reconnect_thread.start()
            return
        # W3.3：非重连分支下，客户端还连着的 THINKING 卡死要收回 LISTENING
        #（Hermes 超时/流中断就是这种：连接健康、只是这一轮没了），否则半双工闸门
        # 永远不开、麦克风失聪，用户只能退出去重进。按连通性门控、provider 中立：
        # 客户端已死（豆包/Qwen 的典型不可重连错误）行为不变。
        if (self._state == DIALOG_STATE_THINKING and self._client is not None
                and self._client.is_connected()):
            self._enter_listening()
        self.error_occurred.emit(err.hint or t("dialog.err.code", code=err.code))

    def _should_reconnect(self, reconnectable: bool) -> bool:
        """客户端判定可重连 + 配置允许 + 每轮最多 1 次 + 非收尾中。"""
        d = getattr(self._app.cfg, "dialog", None)
        if not bool(getattr(d, "auto_reconnect", True)):
            return False
        if self._reconnect_used or self._state == DIALOG_STATE_STOPPING:
            return False
        return reconnectable

    def _reconnect_worker(self) -> None:
        """重连 worker：停旧 client → 新 client（豆包带原 dialog_id 续接）。

        player/capture 复用（设备没变）；失败才收场回 IDLE。重建一次会话
        = 一次 StartSession 计费，QPM 限流下桌面单用户远不会触顶（spec 风险 6）。
        """
        old, self._client = self._client, None
        if old is not None:
            try:
                old.stop()
            except Exception:
                logger.exception("重连：停止旧 client 失败")
        # 续接 id 只在会话确实是豆包时可用：会话中热改 provider 后，内存里的
        # _dialog_id 可能来自另一家（gap 2：Qwen 的 session.id 仅单次连接
        # 标识），喂错种类 id 会串味
        reconnect_id = (self._dialog_id
                        if self._session_provider == "doubao" else "")
        try:
            client = self._create_client(reconnect_id)
            self._session_provider = dialog_provider(self._app.cfg)
            client.start()   # 阻塞至 150（回调把状态带回 LISTENING）
        except Exception as exc:
            logger.error("自动重连失败：%s", exc)
            self.error_occurred.emit(t("dialog.err.reconnect_failed", error=exc))
            self._begin_stop()
            return
        if self._state in (DIALOG_STATE_IDLE, DIALOG_STATE_STOPPING):
            # 重连期间用户按热键退出：立即停掉刚建好的会话
            try:
                client.stop()
            except Exception:
                logger.exception("重连竞态清理失败")
            return
        self.transient_hint.emit(t("dialog.hint.reconnected"), 2.0)
        logger.info("对话已自动重连（dialog_id=%s）", self._dialog_id)

    # ---- 排空判定（主线程 QTimer 50ms 轮询）----

    def _apply_drain_poll(self, active: bool) -> None:
        """启停排空轮询（_drain_poll 的槽，必在主线程执行）。

        QTimer 亲和主线程，而 _start_worker/_stop_worker 跑在工作线程；跨线程直调 start()/stop() 会被 Qt 拒绝
        （isActive 恒 False、只打 qWarning），排空判定永不执行、状态永久卡 SPEAKING，半双工下上行音频随之被永久拦截。
        """
        if active:
            self._drain_timer.start()
        else:
            self._drain_timer.stop()

    def _check_drain(self) -> None:
        """SPEAKING / THINKING → LISTENING：359 已到且本地播放缓冲已排空。

        359 只代表服务端发完，本地可能还压着几百毫秒。THINKING 也可完结，为无音频 provider 而设（进 SPEAKING 的唯一入口是
        首个音频帧，否则「思考中」永远出不去），只能由 _tts_ended + 播放器恒空判定；并需轮次门槛 _ai_turn_started 挡住上一轮迟到的 AI_TURN_END。
        """
        if self._state not in (DIALOG_STATE_SPEAKING, DIALOG_STATE_THINKING):
            return
        if not self._tts_ended or self._player is None:
            return
        if self._state == DIALOG_STATE_THINKING and not self._ai_turn_started:
            return  # 本轮回复尚未开始：来的只可能是上一轮的迟到事件
        if self._player.pending_ms <= 0:
            self._enter_listening()

    # ---- 采集回调（PortAudio 线程）----

    def _on_mic_block(self, block) -> None:
        """20ms 一块：算电平（声纹显示）→ 长按闸门 → 半双工闸门 → 上行。

        跑在 PortAudio 回调线程：不加锁、不抛异常。本地不跑 VAD 状态机（服务端有 VAD），只调 VAD.compute_rms；长按分支只读
        三个缓存布尔（_hold_gate / _hold_uplink_open / _hold_client_gate），配置与热键串解析全在非音频线程做。
        """
        from ..audio.vad import VAD

        rms = VAD.compute_rms(block)
        self.level_changed.emit(min(1.0, rms / LEVEL_NORMALIZE))
        # 长按闸门优先：未按住时不上行（真对讲机）。它开启时半双工被跳过
        # ——半双工的职责（防外放自我打断）已被闸门覆盖，两者叠加会让
        # 「明明按住了却因为 AI 在说话而不上行」。
        if self._hold_gate:
            if not self._hold_uplink_open and not self._hold_client_gate:
                return
            # 例外：客户端自带上行闸门（Hermes）时帧照旧递过去，由它补静音保活——它
            # 的本地 ASR 是云引擎，收不到音频就断连，而「没按住」的时段可以很长（AI
            # 思考 + 用户不说话），断了整场对话失聪；它闸门关着时只送静音、不喂本地 VAD。
        else:
            # 半双工闸门动态读配置（每次一块，属性读取开销可忽略；配置热改即时生效）
            half_duplex = bool(getattr(getattr(self._app.cfg, "dialog", None),
                                        "half_duplex", False))
            if half_duplex and self._state == DIALOG_STATE_SPEAKING:
                return  # AI 播报中暂停上行（外放用户防自我打断）
        if self._client is not None and self._client.is_connected():
            self._client.send_audio(block.tobytes())

    # ---- 长按说话（可选）----

    def hold_enabled(self) -> bool:
        """长按模式是否生效（配置 + 探针可用性 + 对话热键主键可解析）。

        开关走共享的 hold_flag_enabled()（拼错字符串会被 bool() 静默开启，只认真与白名单）；结果写进 _hold_gate 缓存位供采集回调
        只读，主键键码一并刷新（对话热键可现改，缓存不清探针会读旧键→永不越阈值）；键码不可解析（裸键/多步组合）则回落「按一下启停」。
        """
        enabled = False
        try:
            hk = getattr(self._app.cfg, "hotkey", None)
            if hold_flag_enabled(getattr(hk, "hold_to_talk", False)):
                # 赋值发生在 RHS 求值之后：解析抛异常时保留旧值，而下面的
                # `> 0` 判定照样不成立，长按一样会关掉（fail closed）
                self._hold_vk = self._hold_vk_code()
                enabled = bool(_hold_probe.available()) and self._hold_vk > 0
        except Exception:
            enabled = False
        self._hold_gate = enabled
        return enabled

    def hold_press(self, vk: int = 0) -> None:
        """热键按下瞬间（由 app 侧的 on_press 在**热键线程**调用）。

        vk：本次按住真正被按下的键码；**0/缺省 = 控制器自解析对话热键**。必须能传：对话中按住录音热键也是「按住说话」，app 算的是
        录音热键、控制器自解析的是 dialog 键，不穿透则探针采另一个键→SPEAKING 下先不可逆打断 AI 再永久关死闸门。只有 SPEAKING 在此动手。
        """
        if self._state in (DIALOG_STATE_IDLE, DIALOG_STATE_STOPPING):
            return
        try:
            pressed = int(vk)
        except (TypeError, ValueError):
            pressed = 0
        pressed = pressed if pressed > 0 else 0
        if not self.hold_enabled():
            return
        # 赋值排在接管判定之后：没接管的那一次按下（长按没生效、态不对）不该
        # 改动一次仍在进行中的按住所采样的键
        self._hold_press_vk = pressed
        # 诊断留痕（打断链路此前是日志盲区：按下/取消/派发都不打日志，"想打断
        # 但没反应"只能靠猜是没触发还是触发了没生效）
        logger.info("对话热键按下：state=%s vk=%s（SPEAKING 立即打断；THINKING "
                    "需按住 %dms 越阈值才取消本轮）",
                    self._state, pressed or "(自解析)",
                    self._hold_threshold_ms())
        if self._state == DIALOG_STATE_SPEAKING:
            self._interrupt_client()
        self._hold_press_queued.emit()

    def _interrupt_client(self) -> None:
        """调用 provider 预留的打断接口（豆包 515 / Qwen response.cancel）。

        Hermes 是组合型客户端，没有 interrupt，能力探测跳过（与
        _cancel_current_turn 的既有做法一致）。失败只记日志不阻断。
        """
        fn = getattr(self._client, "interrupt", None)
        if not callable(fn):
            logger.info("当前 provider 无打断能力，跳过 interrupt()")
            return
        try:
            fn()
            logger.info("长按按下：已发送打断")
        except Exception:
            logger.warning("长按打断失败", exc_info=True)

    def set_hold_active(self, active: bool) -> None:
        """开/关上行闸门（由 app 侧 HoldTracker 的 ACTIVATE/END 驱动）。"""
        self._hold_uplink_open = bool(active)
        client = self._client
        setter = getattr(client, "set_uplink_open", None)
        if callable(setter):
            try:
                setter(bool(active))
            except Exception:
                logger.warning("同步客户端闸门失败", exc_info=True)

    def hold_end(self) -> None:
        """松手：关闸门 + 立即结束本轮（Hermes 借此让本地 ASR 收束成句）。"""
        self.set_hold_active(False)
        client = self._client
        fn = getattr(client, "hold_end_user_turn", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                logger.warning("通知客户端结束本轮失败", exc_info=True)

    # ---- 长按采样定时器（对话热键路径；录音路径的同类逻辑在 app 侧）----

    def _hold_poll_ms(self) -> int:
        try:
            return max(5, int(getattr(self._app.cfg.hotkey, "hold_poll_ms", 20) or 20))
        except Exception:
            return 20

    def _hold_threshold_ms(self) -> int:
        """与录音路径同源：两条路径共用同一个阈值配置。"""
        try:
            return int(getattr(self._app.cfg.hotkey, "hold_threshold_ms", 300) or 300)
        except Exception:
            return 300

    def _hold_vk_code(self) -> int:
        from ..hotkey.native import parse_hotkey
        spec = parse_hotkey(getattr(self._app.cfg.hotkey, "dialog", "") or "")
        return int(spec[1]) if spec else 0

    def _start_hold_watch(self) -> None:
        """建表起表（主线程；由 _hold_press_queued 排队而来）。

        已在 ARMED/HOLDING 时直接返回：系统自动重复的 keydown 会再次走到这里，
        重建状态机会把时间基准反复推后，一直按着也可能永远越不过阈值。
        """
        tracker = self._hold_tracker
        if tracker is not None and tracker.phase != "idle":
            return
        if self._hold_vk <= 0:
            self._hold_vk = self._hold_vk_code()
        if self._hold_timer is None:
            self._hold_timer = QTimer(self)
            self._hold_timer.timeout.connect(self._on_hold_tick)
        self._hold_timer.start(self._hold_poll_ms())
        self._hold_tracker = HoldTracker(self._hold_threshold_ms())
        self._hold_tracker.press()

    def _stop_hold_watch(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.stop()

    def _probe_held(self) -> bool:
        """采样一拍：优先用本次按住穿透进来的键码（见 hold_press(vk)）。

        0 表示「本次按住没人指定键」→ 用 hold_enabled() 刷新的对话热键缓存，
        与改动前逐字一致。
        """
        state = _hold_probe.is_key_down(self._hold_press_vk or self._hold_vk)
        return bool(state) if state is not None else False

    def _on_hold_tick(self) -> None:
        """对话侧采样：越过阈值开闸门；松手关闸门并结束本轮。

        异常必须吞掉（Qt 定时器回调抛出会中断后续调度），且异常时一律按
        松手处理，避免闸门永久卡在打开状态。
        """
        try:
            tracker = self._hold_tracker
            if tracker is None:
                self._stop_hold_watch()
                return
            action = tracker.poll(self._probe_held(), time.monotonic())
            if action == HoldAction.ACTIVATE:
                logger.info("长按越阈值：state=%s → %s", self._state,
                            "取消本轮" if self._state == DIALOG_STATE_THINKING
                            else "开闸门")
                if self._state == DIALOG_STATE_THINKING:
                    # 思考中按住 = 取消本轮回聆听（既有语义）
                    self._cancel_current_turn()
                self.set_hold_active(True)
            elif action in (HoldAction.END, HoldAction.DISCARD):
                self._stop_hold_watch()
                self._hold_tracker = None
                if action == HoldAction.END:
                    self.hold_end()
                else:
                    self.set_hold_active(False)   # 轻点：净效果仅"按下时的打断"
        except Exception:
            logger.exception("对话长按采样异常")
            self._stop_hold_watch()
            self._hold_tracker = None
            self.set_hold_active(False)

    # ---- 对话热键入口 ----

    def toggle(self) -> None:
        """对话热键（再按一次 = 结束）。忽略 + 提示，不抢占录音（spec）。"""
        # 两模式互斥：录音中（含 STOPPING）忽略（"idle" 与 app.APP_STATE_IDLE 同值）
        if str(getattr(self._app, "state", "idle")) != "idle":
            self.error_occurred.emit(t("dialog.err.recording_busy"))
            return
        with self._lock:
            if self._state == DIALOG_STATE_IDLE:
                # 总开关闸只拦「进入」不拦「退出」：对话进行中从设置页关掉
                # enable 时本方法走下面的 _begin_stop 分支，用户仍能正常退出
                # （闸放在 toggle 开头会把人困在对话态）。排在密钥检查之前：
                # 功能整体关闭比密钥未填更根本，提示也该指向开关而不是密钥
                if not dialog_enabled(self._app.cfg):
                    self.error_occurred.emit(t("dialog.err.disabled"))
                    return
                if not dialog_configured(self._app.cfg):
                    self.error_occurred.emit(t("dialog.err.no_key"))
                    return
                self._start_thread = threading.Thread(
                    target=self._start_worker, daemon=True, name="dialog-start")
                self._start_thread.start()
            elif self._state == DIALOG_STATE_STOPPING:
                return  # 收尾中，忽略连按
            elif self._state == DIALOG_STATE_THINKING:
                # 思考中按键 = 取消当前轮回到聆听，不是退出对话。
                # 用户此刻按键的意图大概率是「识别错了，重说」；想真正退出
                # 时再按一次即可（多一步，换来重说零成本）。
                self._cancel_current_turn()
            else:
                self._begin_stop()

    def _start_worker(self) -> None:
        """启动 worker：设备自检先行（无声卡不浪费 API 会话）→ 握手 → 开麦。

        client.start() 阻塞至 150（最多 ~30s），150 回调把状态切到 LISTENING；
        失败回滚已开的设备并回 IDLE。
        """
        global _HEADPHONE_HINTED
        with self._lock:
            if self._state != DIALOG_STATE_IDLE:
                return
            self._set_state(DIALOG_STATE_CONNECTING)
        # 会话启动即刷新长按闸门缓存位（本线程非音频线程，允许解析配置）：
        # 配置在进入对话之前就改过、又没走到热键重建时，这里兜住。
        self.hold_enabled()
        player = None
        capture = None
        try:
            player = self._create_player()
            player.start()          # 设备问题早发现（诊断脚本同款顺序）
            capture = self._create_capture()
            capture.start()
            self._session_provider = dialog_provider(self._app.cfg)
            client = self._create_client(self._load_saved_dialog_id())
            client.start()          # 150 回调 → LISTENING（_enter_listening）
            self._player = player
            self._capture = capture
            self._reconnect_used = False
            self._drain_poll.emit(True)   # 本线程非主线程，经信号转主线程启动
            # 复用录音的媒体中断决策（同一纯函数，零新增配置字段）
            from .asr_pipeline import decide_media_actions

            do_pause, do_mute = decide_media_actions(self._app.cfg)
            if do_pause or do_mute:
                self._app.media_pauser.engage(do_pause, do_mute)
            # 一次性耳机提示：全双工才有回声问题，半双工闸门本身就是解法；
            # 标记是模块级（进程内一次，跨多次进入对话）
            half_duplex = bool(getattr(getattr(self._app.cfg, "dialog", None),
                                       "half_duplex", False))
            if not half_duplex and not _HEADPHONE_HINTED:
                _HEADPHONE_HINTED = True
                self.transient_hint.emit(t("dialog.hint.headphone"), 3.0)
        except Exception as exc:
            logger.error("语音模式启动失败：%s", exc)
            self.error_occurred.emit(t("dialog.err.start_failed", error=exc))
            for obj in (capture, player):
                if obj is not None:
                    try:
                        obj.stop()
                    except Exception:
                        logger.debug("启动回滚失败", exc_info=True)
            self._client = None
            self._drain_poll.emit(False)
            self._set_state(DIALOG_STATE_IDLE)

    # ---- 收尾 ----

    def _cancel_current_turn(self) -> None:
        """取消在途轮次 → 回 LISTENING，会话不拆。

        与 _begin_stop 的区别：采集/连接/播放器全保留，只作废本轮；被取消的用户文本原样重发并带 interrupted=True（复用 barge-in 划线通道）。
        划线句必须与新句分属两轮：重发后清空 _turn_role，让下一句另起一轮，否则划线留在新句上、原句凭空消失（连按两次取消时第二次不重发）。
        """
        cancel = getattr(self._client, "cancel_current_turn", None)
        logger.info("取消本轮：state=%s 客户端=%s，取消能力=%s",
                    self._state, type(self._client).__name__,
                    "有" if callable(cancel) else "无（回落退出对话）")
        if not callable(cancel):
            # W2：客户端不具备「取消本轮」能力（豆包/Qwen）时整个方法回落到 _begin_stop
            # （思考中一按即退出对话）。不能只跳过分派那两行：划线重发是「能重说」的 UI
            # 承诺，那两家没有重说通路，划线只会留下一句凭空消失的话。
            self._begin_stop()
            return
        if (self._last_role == ROLE_USER and self._last_text
                and self._turn_role == ROLE_USER):
            self._emit_turn(self._last_text, True, interrupted=True)
            self._turn_role = ""
        try:
            cancel()
        except Exception:
            logger.warning("取消当前轮失败", exc_info=True)
        self._enter_listening()

    def _begin_stop(self) -> None:
        """请求停止（热键/退出意图/不可恢复错误/断连）：置 STOPPING 后台收尾。

        client.stop() 最多阻塞 ~10s（含 FinishSession 超时容忍路径），
        不能卡调用线程（可能是 asyncio 线程或 keyboard 线程）。
        """
        if self._state in (DIALOG_STATE_IDLE, DIALOG_STATE_STOPPING):
            return
        self._set_state(DIALOG_STATE_STOPPING)
        self._stop_thread = threading.Thread(
            target=self._stop_worker, daemon=True, name="dialog-stop")
        self._stop_thread.start()

    def _stop_worker(self) -> None:
        """收尾 worker：停 client → 播放器 → 采集 → 恢复后台音频 → 回 IDLE。"""
        client, self._client = self._client, None
        player, self._player = self._player, None
        capture, self._capture = self._capture, None
        if client is not None:
            try:
                client.stop()
            except Exception:
                logger.exception("停止对话客户端失败")
        if player is not None:
            try:
                player.stop()
            except Exception:
                logger.exception("停止播放器失败")
        if capture is not None:
            try:
                capture.stop()
            except Exception:
                logger.exception("停止对话采集失败")
        try:
            self._app.media_pauser.resume()
        except Exception:
            logger.exception("恢复后台音频失败")
        self._audio_gate_closed = False
        # 长按闸门一并复位：会话可能在按住期间结束，留着开着下次就是「没按住也照常
        # 上行」。定时器不在这里 stop（本方法跑在收尾 worker 线程，跨线程操作 QTimer
        # 会被 Qt 拒绝），把 tracker 置空后主线程下一拍自己收摊（_on_tick 的 None 分支）。
        self._hold_uplink_open = False
        self._hold_tracker = None
        self._tts_ended = False
        self._ai_turn_started = False
        self._ai_text_shown = False          # D1：会话结束，门闩一并复位
        self._ai_full_text_seen = False
        self._current_reply_id = ""
        self._invalidated_reply_id = ""
        self._session_provider = ""
        self._drain_poll.emit(False)
        self._set_state(DIALOG_STATE_IDLE)
        logger.info("语音模式已结束（dialog_id=%s）", self._dialog_id or "(无)")

    def shutdown(self) -> None:
        """程序退出清理（app._cleanup 调）：同步收尾，幂等。

        退出路径等得起（正常收尾 <1s，FinishSession 超时容忍路径最多 ~10s）；
        已在收尾（STOPPING 线程在跑）时直接同步清剩余字段，重复调用安全。
        """
        if self._state == DIALOG_STATE_IDLE:
            self._drain_poll.emit(False)
            return
        self._stop_worker()

    # ---- 工厂方法（延迟导入重模块；单测子类覆盖注入 Fake）----

    def _create_client(self, dialog_id: str):
        """按配置分派对话客户端：aliyun / hermes / 否则豆包。"""
        prov = dialog_provider(self._app.cfg)
        if prov == "aliyun":
            return self._create_qwen_client()
        if prov == "hermes":
            return self._create_hermes_client()
        return self._create_doubao_client(dialog_id)

    def _create_doubao_client(self, dialog_id: str):
        """创建 DoubaoDialogClient（握手参数采用 P0 定案值）。"""
        from ..asr.doubao_dialog import DoubaoDialogClient

        d = getattr(self._app.cfg, "dialog", None)
        api_key, app_id, access_token = resolve_dialog_credentials(self._app.cfg)
        client = DoubaoDialogClient(
            api_key=api_key, app_id=app_id, access_token=access_token,
            dialog_id=dialog_id,
            model=str(getattr(d, "model", "1.2.1.1")),
            bot_name=str(getattr(d, "bot_name", "")),
            system_role=str(getattr(d, "system_role", "")),
            speaking_style=str(getattr(d, "speaking_style", "")),
            character_manifest=str(getattr(d, "character_manifest", "")),
            speaker=str(getattr(d, "speaker", "zh_female_vv_jupiter_bigtts")),
            speech_rate=int(getattr(d, "speech_rate", 0)),
            loudness_rate=int(getattr(d, "loudness_rate", 0)),
            end_smooth_window_ms=int(getattr(d, "end_smooth_window_ms", 800)),
            enable_user_query_exit=bool(getattr(d, "enable_user_query_exit", True)),
            strict_audit=bool(getattr(d, "strict_audit", True)),
        )
        self._attach_client(client)
        return client

    def _create_qwen_client(self):
        """创建 QwenDialogClient（ws_url 由 build_qwen_ws_url 按 region 路由）。

        Qwen 无 dialog_id 续接（gap 2：session.id 仅单次连接标识），故不吃 dialog_id。
        """
        from ..asr.qwen_dialog import QwenDialogClient

        d = getattr(self._app.cfg, "dialog", None)
        q = getattr(d, "qwen", None)
        api_key, workspace_id = resolve_qwen_credentials(self._app.cfg)
        region = str(getattr(q, "region", "legacy"))
        model = str(getattr(q, "model", "qwen-audio-3.0-realtime-plus"))
        client = QwenDialogClient(
            api_key=api_key,
            ws_url=build_qwen_ws_url(region, workspace_id, model),
            voice=str(getattr(q, "voice", "longanqian")),
            instructions=str(getattr(q, "instructions", "")),
            turn_detection=str(getattr(q, "turn_detection", "server_vad")),
            vad_threshold=_safe_float(getattr(q, "vad_threshold", 0.5), 0.5),
            silence_duration_ms=int(getattr(q, "silence_duration_ms", 800)),
            enable_speech_emotion=bool(getattr(q, "enable_speech_emotion", True)),
            max_history_turns=int(getattr(q, "max_history_turns", 20)),
            enable_search=bool(getattr(q, "enable_search", False)),
        )
        self._attach_client(client)
        return client

    def _create_hermes_client(self, dialog_id: str = ""):
        """创建 HermesDialogClient（组合型：本地 VAD + ASR 引擎 + HTTP）。

        ASR 引擎用 asr_factory 延迟构造：对话专属引擎与听写引擎可能不是同一家，而各引擎构造参数不同，故把「按
        dialog_asr_engine 造一个引擎」交给工厂闭包。dialog_id 只保留形参（Hermes 会话 id 不跨连接续接，与 Qwen session.id 同）。
        """
        from ..asr.hermes_dialog import HermesDialogClient
        from ..config.loader import (
            dialog_asr_engine, dialog_asr_overrides,
            resolve_hermes_credentials,
        )

        base_url, api_key = resolve_hermes_credentials(self._app.cfg)
        d = getattr(self._app.cfg, "dialog", None)
        h = getattr(d, "hermes", None)
        engine = dialog_asr_engine(self._app.cfg)
        overrides = dialog_asr_overrides(self._app.cfg)

        client = HermesDialogClient(
            base_url=base_url,
            api_key=api_key,
            asr_factory=_make_asr_factory(self._app.cfg, engine, overrides),
            model=str(getattr(h, "model", "hermes-agent")),
            session_title=str(getattr(h, "session_title", t("dialog.session_title"))),
            system_hint=str(getattr(h, "system_hint", "")),
            connect_timeout_ms=int(getattr(h, "connect_timeout_ms", 5000)),
            turn_timeout_ms=int(getattr(h, "turn_timeout_ms", 60000)),
        )
        self._attach_client(client)
        return client

    def _create_player(self):
        from ..audio.playback import AudioPlayer

        device = str(getattr(getattr(self._app.cfg, "dialog", None),
                             "output_device", "") or "")
        return AudioPlayer(device=device or None)

    def _create_capture(self):
        from ..audio.capture import AudioCapture

        device = str(getattr(getattr(self._app.cfg, "audio", None),
                            "input_device", "") or "")
        return AudioCapture(
            sample_rate=16000, channels=1, block_size=DIALOG_BLOCK_FRAMES,
            dtype="int16", on_block=self._on_mic_block, device=device or None,
        )

    # ---- dialog_id 持久化（跨进程续接，spec「连接生命周期」）----

    def _persist_dialog_id(self) -> None:
        """150 返回后写入状态目录；keep_context=False 不写（下次全新会话）。"""
        d = getattr(self._app.cfg, "dialog", None)
        if not bool(getattr(d, "keep_context", True)):
            return
        if dialog_provider(self._app.cfg) != "doubao":
            # Qwen 的 session.id 仅单次连接标识（gap 2），不提供跨进程续接；
            # 落盘反而会覆盖豆包的 dialog_id，切回豆包时被误续接
            return
        try:
            _CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CONTEXT_FILE.write_text(
                json.dumps({"dialog_id": self._dialog_id}), encoding="utf-8")
        except Exception:
            logger.debug("dialog_id 持久化失败（不影响对话）", exc_info=True)

    def _load_saved_dialog_id(self) -> str:
        """读取上次会话的 dialog_id；keep_context=False 或文件损坏返回空。"""
        d = getattr(self._app.cfg, "dialog", None)
        if not bool(getattr(d, "keep_context", True)):
            return ""
        if dialog_provider(self._app.cfg) != "doubao":
            return ""  # 仅豆包有续接语义（gap 2：Qwen session.id 不跨连接）
        try:
            data = json.loads(_CONTEXT_FILE.read_text(encoding="utf-8"))
            return str(data.get("dialog_id", ""))
        except Exception:
            return ""


# ---- 错误码中文映射已下沉到 asr/doubao_dialog.py ----
