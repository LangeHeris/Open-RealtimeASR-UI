"""豆包端到端实时语音大模型（S2S RealtimeAPI）—— 客户端类层。

接口文档：火山引擎《豆包端到端实时语音大模型 API》（修订记录最新 26.04.29）。
帧编解码纯函数在 doubao_dialog_codec.py（Plan 1，已逐字节联调定案）；本模块
只做 IO 编排：独立线程跑 asyncio loop、两段式握手时序、收发循环、生命周期
与 4 个回调。

线程模型（照搬 volcengine_realtime.VolcengineRealtimeASR）：
- 主线程调 start() / send_audio() / interrupt() / finish_session() / stop() 同步接口
- "dialog-loop" 守护线程跑 asyncio loop：_connect / _send_loop / _recv_loop / _shutdown
- 回调（on_state / on_event / on_audio / on_error）都在 asyncio 线程触发，
  UI 层需经信号槽切回主线程（DialogController，Plan 3）

与 ASR 客户端的关键差异（P0 联调定案，见设计文档「联调待验证清单」）：
- 握手两段式：StartConnection(1) → 等 ConnectionStarted(50) →
  StartSession(100) → 等 SessionStarted(150)（返回 dialog_id）
- 无 sequence、无 gzip、无静音保活（server_vad 模式文档明确无需补静音）
- 音频帧必须带 session id（定案 #2）
- stop() 必须容忍 FinishSession 超时路径（前置约束 #2）：AI 正要开口时收尾，
  等 152 超时 5s 后 FinishConnection 会收到 55000000 "the stream is done" 错误
  帧——常态。_finishing 置位后所有错误帧/错误事件只记日志、不回调 on_error
- 握手失败只打 status_code / 响应体 / X-Tt-Logid 三个字段，绝不 dump 鉴权头
  （前置约束 #1：build_auth_headers 的 dict 含 X-Api-Access-Key）

回调归属（消除歧义）：
- 352 等音频帧 → on_audio(pcm)，不走 on_event
- 51 / 153 / 599 / 0b1111 错误帧 → on_error(DialogErrorEvent)，不走 on_event
- 其余 JSON 事件经 _translate_event 译成中立词汇 → on_event(neutral_event, payload)
- 握手期间（150 未回）的错误不回调 on_error，统一由 start() 抛 DialogError 上报
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Callable, Optional

import websockets

from .doubao_dialog_codec import (
    EV_CLIENT_INTERRUPT,
    EV_FINISH_CONNECTION,
    EV_FINISH_SESSION,
    EV_START_CONNECTION,
    EV_START_SESSION,
    MSG_AUDIO_ONLY_RESPONSE,
    MSG_ERROR_RESPONSE,
    DialogFrame,
    WS_URL,
    build_audio_frame,
    build_auth_headers,
    build_event_frame,
    build_start_session_payload,
    parse_frame,
)
from .dialog_events import (
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

logger = logging.getLogger(__name__)

# ---- 状态（on_state 回调的取值，spec「类接口与线程模型」）----
STATE_CONNECTING = "connecting"        # 建 ws + StartConnection → 等 50
STATE_CONNECTED = "connected"          # ConnectionStarted(50) 已回，未开会话
STATE_SESSION_READY = "session_ready"  # SessionStarted(150) 已回，可收发
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"

# ---- 客户端逻辑用到的服务端事件 ID（全集见设计文档「事件 ID 常量」）----
SERVER_CONNECTION_STARTED = 50
SERVER_CONNECTION_FAILED = 51
SERVER_SESSION_STARTED = 150
SERVER_SESSION_FINISHED = 152
SERVER_SESSION_FAILED = 153
SERVER_DIALOG_ERROR = 599
# 翻译层（_translate_event）用到的会话期事件
SERVER_TTS_SENTENCE_START = 350
SERVER_TTS_ENDED = 359
SERVER_ASR_INFO = 450
SERVER_ASR_RESPONSE = 451
SERVER_ASR_ENDED = 459
SERVER_CHAT_RESPONSE = 550

# 359 的 status_code=20000002：服务端识别到用户说了「退出/再见」
_EXIT_INTENT_STATUS = "20000002"

StateCallback = Callable[[str], None]
EventCallback = Callable[[str, dict], None]          # (neutral_event, payload)
AudioCallback = Callable[[bytes], None]
ErrorCallback = Callable[[DialogErrorEvent], None]   # 中立错误对象

# 需要走 on_error（而非 on_event）的服务端错误事件
_ERROR_EVENTS = (SERVER_CONNECTION_FAILED, SERVER_SESSION_FAILED,
                 SERVER_DIALOG_ERROR)

# ---- 豆包错误码 → 中文提示 / 可重连判定（从 core/dialog.py 下沉，spec §3.2）----

DIALOG_ERROR_HINTS = {
    42000020: "对话配置有误，请到设置检查模型与人设",
    45000003: "长时间无交互，连接已释放",        # → auto_reconnect 重建
    50000000: "模型推理出错，请重试",
    50700000: "模型推理超时",
    52000011: "模型回复超时",
    52000016: "语音合成超时",
    52000022: "对话服务出错，请重试",
    52000035: "对话服务连接出错",
    52000042: "音频上行空闲超时",
    55000001: "服务端错误，请重试",
}

_ERROR_KEYWORD_HINTS = (
    ("InvalidSpeaker",
     "音色与模型版本不匹配（O2.0 用 zh_*_jupiter_bigtts，SC2.0 用 saturn_*）"),
    ("ContextCanceled", "会话未正常结束（检查 FinishSession 时序）"),
)


def _doubao_error_hint(code: int, message: str) -> str:
    """错误码/关键字 → 单行中文提示（悬浮条单行右截断，只留可行动信息）。"""
    hint = DIALOG_ERROR_HINTS.get(code)
    if hint:
        return hint
    for keyword, text in _ERROR_KEYWORD_HINTS:
        if keyword in (message or ""):
            return text
    if message:
        return f"对话错误[{code}]：{message[:60]}"
    return f"对话错误[{code}]"


def _doubao_reconnectable(code: int) -> bool:
    """45000003（空闲释放）与 5xx 类错误可自动重建（决策仍在控制器）。"""
    return code == 45000003 or 50000000 <= code < 60000000


def _make_error_event(code: int, message: str) -> DialogErrorEvent:
    """把豆包错误码打包成中立 DialogErrorEvent（分类在客户端完成）。"""
    return DialogErrorEvent(code, message,
                            hint=_doubao_error_hint(code, message),
                            reconnectable=_doubao_reconnectable(code))


def _header_items(headers) -> list[tuple[str, str]]:
    """从 websockets 的 Headers 对象安全取 (name, value) 列表。

    websockets 17 的 Headers 是多值容器，dict()/items() 遇到重复头
    （实测服务端会回两个 server-timing）会抛 KeyError，必须走 raw_items()；
    旧版 websockets 的 response_headers 本身就是 (name, value) 列表，直接迭代。
    """
    if headers is None:
        return []
    if hasattr(headers, "raw_items"):          # websockets >= 13
        try:
            return [(str(k), str(v)) for k, v in headers.raw_items()]
        except Exception:
            return []
    if hasattr(headers, "items"):              # 通用 mapping
        try:
            return [(str(k), str(v)) for k, v in headers.items()]
        except Exception:
            return []
    try:                                        # 旧版 pair 列表
        return [(str(k), str(v)) for k, v in headers]
    except Exception:
        return []


class DoubaoDialogClient:
    """豆包 S2S 对话客户端（一次实例 = 一条 WebSocket = 一个会话）。

    每次进入对话新建实例（不复用连接）；跨轮上下文由上层把旧 dialog_id
    传入构造参数、服务端在 150 返回新值续接（前置约束 #3）。

    超时类常量做类属性，便于测试按实例覆盖加速：
        CONNECT_TIMEOUT        建 ws + 等 ConnectionStarted(50)
        SESSION_TIMEOUT        等 SessionStarted(150)
        FINISH_SESSION_TIMEOUT 等 SessionFinished(152)——超时是常态（见模块 docstring）
        SHUTDOWN_TIMEOUT       stop() 整体上限（含上一项）
    """

    CONNECT_TIMEOUT = 10.0
    SESSION_TIMEOUT = 15.0
    FINISH_SESSION_TIMEOUT = 5.0
    SHUTDOWN_TIMEOUT = 10.0
    # start() 总预算在 CONNECT+SESSION 之上追加的余量：覆盖 ws 建连
    # （websockets open_timeout 默认 10s）与跨线程调度开销
    START_MARGIN = 15.0
    # 上行音频队列上限（包数）：20ms/包 → 50 包 = 1s；发送滞后超上限时
    # 丢最旧包（实时性优先），防止无界积压
    AUDIO_QUEUE_MAX = 50

    def __init__(
        self,
        api_key: str = "",
        app_id: str = "",
        access_token: str = "",
        *,
        dialog_id: str = "",
        model: str = "1.2.1.1",
        bot_name: str = "",
        system_role: str = "",
        speaking_style: str = "",
        character_manifest: str = "",
        speaker: str = "zh_female_vv_jupiter_bigtts",
        speech_rate: int = 0,
        loudness_rate: int = 0,
        end_smooth_window_ms: int = 800,
        enable_user_query_exit: bool = True,
        strict_audit: bool = True,
        on_state: Optional[StateCallback] = None,
        on_event: Optional[EventCallback] = None,
        on_audio: Optional[AudioCallback] = None,
        on_error: Optional[ErrorCallback] = None,
    ):
        self.api_key = api_key
        self.app_id = app_id
        self.access_token = access_token

        # StartSession payload 参数（build_start_session_payload 的子集，
        # DialogController 在 Plan 3 从配置节读出来传进来）
        self._session_kwargs: dict = {
            "model": model,
            "bot_name": bot_name,
            "system_role": system_role,
            "speaking_style": speaking_style,
            "character_manifest": character_manifest,
            "dialog_id": dialog_id,
            "speaker": speaker,
            "speech_rate": speech_rate,
            "loudness_rate": loudness_rate,
            "end_smooth_window_ms": end_smooth_window_ms,
            "enable_user_query_exit": enable_user_query_exit,
            "strict_audit": strict_audit,
        }
        self._initial_dialog_id = dialog_id

        self.on_state = on_state
        self.on_event = on_event
        self.on_audio = on_audio
        self.on_error = on_error

        self._state = STATE_DISCONNECTED
        self._ws: Optional[object] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

        self._reset_session_state()

    # ---- 会话状态 ----

    def _reset_session_state(self) -> None:
        """每次 start() 前复位会话相关状态（事件对象换新，避免跨 loop 复用）。"""
        self._session_id = ""
        self.dialog_id = self._initial_dialog_id
        self.last_logid = ""
        self._session_ready = False
        self._finishing = False
        self._handshake_error: Optional[tuple[int, str]] = None
        self._handshake_failed = asyncio.Event()
        self._finished_evt = asyncio.Event()
        self._send_task = None
        self._recv_task = None
        self._audio_queue = None
        self._stop_event = None
        self._ws = None
        self._stop_requested = False       # 一次性 loop.stop 投递标志（见 _cleanup_loop）

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        """会话是否就绪（可 send_audio）。"""
        return self._session_ready and self._loop is not None

    def _set_state(self, state: str) -> None:
        """状态迁移 + 回调（带变化守卫；回调异常只记日志不外抛）。"""
        if state == self._state:
            return
        self._state = state
        logger.info("对话客户端状态：%s", state)
        if self.on_state is not None:
            try:
                self.on_state(state)
            except Exception:
                logger.exception("on_state 回调异常")

    # ---- 回调通知 ----

    def _notify_event(self, neutral: str, payload: dict) -> None:
        logger.debug("中立事件 %s：%s", neutral, payload)
        if self.on_event is not None:
            try:
                self.on_event(neutral, payload)
            except Exception:
                logger.exception("on_event 回调异常（event=%s）", neutral)

    def _notify_audio(self, pcm: bytes) -> None:
        if self.on_audio is not None:
            try:
                self.on_audio(pcm)
            except Exception:
                logger.exception("on_audio 回调异常（%d 字节）", len(pcm))

    def _notify_error(self, err: DialogErrorEvent) -> None:
        logger.error("对话错误 code=%s message=%s（提示：%s，可重连=%s）",
                     err.code, err.message, err.hint, err.reconnectable)
        if self.on_error is not None:
            try:
                self.on_error(err)
            except Exception:
                logger.exception("on_error 回调异常")

    # ---- 帧分发 ----

    @staticmethod
    def _event_error(payload: dict) -> tuple[int, str]:
        """从 51/153/599 的 payload 提取 (code, message)。"""
        code = payload.get("code", payload.get("error_code", -1))
        message = (payload.get("message") or payload.get("error")
                   or payload.get("error_message") or "")
        try:
            return int(code), str(message)
        except (TypeError, ValueError):
            return -1, str(message)

    @staticmethod
    def _frame_error_message(frame: DialogFrame) -> str:
        """从错误帧 payload 提取 message（布局见 P0 定案 #6，payload 是 JSON）。"""
        payload = frame.json()
        return str(payload.get("message") or payload.get("error")
                   or payload.get("error_message") or "")

    def _translate_event(self, event_id: int, payload: dict) -> list[tuple[str, dict]]:
        """豆包 wire 事件 → 0..N 个中立 (event, payload)（spec 表 A）。

        350 一对多：AI_TURN_START（开闸门/记 reply_id）+ AI_TRANSCRIPT（上屏文本）。
        50/152/351/559/154 等不驱动状态机的事件落到末尾 return []，由该处记
        INFO 日志留痕（DEBUG 默认不开，生产排障需 INFO），不上抛控制器。
        """
        if event_id == SERVER_SESSION_STARTED:
            return [(SESSION_READY, {"dialog_id": self.dialog_id})]
        if event_id == SERVER_ASR_RESPONSE:            # 451
            results = payload.get("results") or []
            if not results:
                return []
            last = results[-1]
            text = str(last.get("text", ""))
            if not text:
                return []
            return [(USER_TRANSCRIPT,
                     {"text": text,
                      "is_final": not bool(last.get("is_interim", True))})]
        if event_id == SERVER_ASR_ENDED:               # 459
            return [(USER_TURN_END, {})]
        if event_id == SERVER_TTS_SENTENCE_START:      # 350
            out: list[tuple[str, dict]] = [
                (AI_TURN_START, {"reply_id": str(payload.get("reply_id", ""))})]
            text = str(payload.get("text", ""))
            if text:
                out.append((AI_TRANSCRIPT, {"text": text}))
            return out
        if event_id == SERVER_ASR_INFO:                # 450
            return [(BARGE_IN, {})]
        if event_id == SERVER_TTS_ENDED:               # 359
            return [(AI_TURN_END,
                     {"exit_intent":
                      str(payload.get("status_code", "")) == _EXIT_INTENT_STATUS})]
        if event_id == SERVER_CHAT_RESPONSE:           # 550
            return [(AI_FULL_TEXT, {"text": str(payload.get("content", ""))})]
        # 未匹配任何已知 wire 事件（50/152/351/559/154 等轮末类，不驱动状态机）：
        # 记 INFO 留痕供生产排障（承接旧 dialog.py else 分支；DEBUG 默认不开）。
        # 451/350 等已知事件的空内容在上方各自 return []，不落这里、不误记。
        logger.info("未翻译的服务端事件 %s（不驱动状态机）：%s",
                    event_id, str(payload)[:100])
        return []

    def _handle_frame(self, frame: DialogFrame) -> None:
        """把一帧解析结果分发给回调/内部事件。只在 asyncio 线程调用。"""
        if frame.message_type == MSG_ERROR_RESPONSE:
            self._on_error_frame(frame)
            return
        if frame.message_type == MSG_AUDIO_ONLY_RESPONSE:
            self._notify_audio(frame.payload)
            return
        if frame.event_id == 0:
            logger.warning("服务端 JSON 帧缺 event id，丢弃：%s", frame.payload[:120])
            return
        payload = frame.json()
        if frame.event_id in _ERROR_EVENTS:
            self._on_error_event(frame.event_id, payload)
            return
        if frame.event_id == SERVER_SESSION_STARTED:
            self.dialog_id = str(payload.get("dialog_id", ""))
            self._session_ready = True
        elif frame.event_id == SERVER_SESSION_FINISHED:
            self._finished_evt.set()
        for neutral, norm in self._translate_event(frame.event_id, payload):
            self._notify_event(neutral, norm)

    def _on_error_frame(self, frame: DialogFrame) -> None:
        """错误帧（0b1111）分发：收尾期抑制、握手期存档、会话期回调。"""
        message = self._frame_error_message(frame)
        if self._finishing:
            logger.debug("收尾期间错误帧 code=%s：%s（常态，不回调）",
                         frame.error_code, message)
            return
        if not self._session_ready:
            self._handshake_error = (frame.error_code, message)
            self._handshake_failed.set()
            logger.warning("握手期间错误帧 code=%s：%s", frame.error_code, message)
            return
        self._notify_error(_make_error_event(frame.error_code, message))

    def _on_error_event(self, event_id: int, payload: dict) -> None:
        """51/153/599 错误事件分发：同 _on_error_frame 的三段式。"""
        code, message = self._event_error(payload)
        if not message:
            message = f"服务端错误事件 {event_id}"
        if not self._session_ready:
            self._handshake_error = (code, message)
            self._handshake_failed.set()
            logger.warning("握手期间错误事件 %s code=%s：%s", event_id, code, message)
            return
        if self._finishing:
            logger.debug("收尾期间错误事件 %s code=%s：%s（忽略）",
                         event_id, code, message)
            return
        self._notify_error(_make_error_event(code, message))

    # ---- WebSocket 连接 ----

    async def _ws_connect(self):
        """建立 WebSocket（独立方法，测试子类替换它注入 FakeWS）。"""
        headers = build_auth_headers(api_key=self.api_key, app_id=self.app_id,
                                     access_token=self.access_token)
        try:
            return await websockets.connect(WS_URL, max_size=None,
                                            additional_headers=headers)
        except TypeError:  # 旧版 websockets 兼容（同现有 ASR 客户端）
            return await websockets.connect(WS_URL, max_size=None,
                                            extra_headers=headers)

    @staticmethod
    def _response_header_items(ws) -> list[tuple[str, str]]:
        """取握手响应头的 (name, value) 列表（兼容 websockets 新旧两版）。"""
        resp = getattr(ws, "response", None)
        items = _header_items(getattr(resp, "headers", None))
        if items:
            return items
        return _header_items(getattr(ws, "response_headers", None))

    @staticmethod
    def _header_get(items: list[tuple[str, str]], name: str) -> str:
        """在 (name, value) 列表里大小写不敏感取第一个匹配。"""
        for key, value in items:
            if key.lower() == name.lower():
                return value
        return ""

    def _logid_hint(self) -> str:
        """错误消息尾缀（文档 2.1：报障只认 X-Tt-Logid）。"""
        return f"（X-Tt-Logid={self.last_logid}）" if self.last_logid else ""

    @staticmethod
    def _describe_connect_failure(exc: Exception) -> str:
        """把建连异常转成 DialogError 消息。

        前置约束 #1：鉴权头含 X-Api-Access-Key，绝不能 dump headers；
        只允许 status_code / 响应体 / X-Tt-Logid 三个字段进消息与日志。
        """
        resp = getattr(exc, "response", None)
        msg = f"WebSocket 连接失败：{exc}"
        details: list[str] = []
        status = getattr(resp, "status_code", None)
        if status is not None:
            details.append(f"status={status}")
        body = getattr(resp, "body", None)
        if isinstance(body, (bytes, bytearray)) and body:
            details.append(f"响应体={bytes(body).decode('utf-8', 'replace')[:300]}")
        headers = getattr(resp, "headers", None)
        if headers is not None:
            logid = DoubaoDialogClient._header_get(
                _header_items(headers), "X-Tt-Logid")
            if logid:
                details.append(f"X-Tt-Logid={logid}")
        if details:
            msg += "（" + "，".join(details) + "）"
        if status in (401, 403):
            msg += ("（鉴权失败：请到设置 -> 引擎密钥核对火山密钥，并确认已开通"
                    "「豆包端到端实时语音大模型」服务）")
        return msg

    # ---- 握手时序 ----

    async def _connect(self) -> None:
        """两段式握手：建 ws → StartConnection(1) → 等 50 → StartSession(100) → 等 150。

        任一步失败/超时抛 DialogError（消息含 code 与 X-Tt-Logid）；失败与取消
        （start 超时会 cancel 本协程）路径统一由 finally 关闭 ws，成功路径保留
        连接并启动收发循环。
        """
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._set_state(STATE_CONNECTING)
        try:
            self._ws = await self._ws_connect()
        except Exception as exc:
            self._set_state(STATE_ERROR)
            raise DialogError(self._describe_connect_failure(exc)) from exc

        self.last_logid = self._header_get(
            self._response_header_items(self._ws), "X-Tt-Logid")
        logger.info("对话 WebSocket 已建立，X-Tt-Logid=%s",
                    self.last_logid or "(未返回)")
        self._session_id = str(uuid.uuid4())

        try:
            await self._ws.send(build_event_frame(EV_START_CONNECTION))
            await self._recv_until(SERVER_CONNECTION_STARTED, self.CONNECT_TIMEOUT,
                                   "ConnectionStarted(50)")
            self._set_state(STATE_CONNECTED)

            payload = build_start_session_payload(**self._session_kwargs)
            await self._ws.send(
                build_event_frame(EV_START_SESSION, payload, self._session_id))
            await self._recv_until(SERVER_SESSION_STARTED, self.SESSION_TIMEOUT,
                                   "SessionStarted(150)")
        except DialogError:
            self._set_state(STATE_ERROR)
            raise
        except Exception as exc:
            self._set_state(STATE_ERROR)
            raise DialogError(f"握手发送/接收失败：{type(exc).__name__}: {exc}"
                              f"{self._logid_hint()}") from exc
        finally:
            # 失败/取消（start 超时 cancel 本协程）路径统一关连接防泄漏；
            # 成功路径 _session_ready 已被 150 帧置位，保留连接
            if not self._session_ready:
                await self._close_ws_quietly()

        self._set_state(STATE_SESSION_READY)
        self._send_task = asyncio.create_task(self._send_loop(), name="dialog-send")
        self._recv_task = asyncio.create_task(self._recv_loop(), name="dialog-recv")

    async def _recv_until(self, event_id: int, timeout: float, what: str) -> None:
        """握手期收帧：逐帧走 _handle_frame 分发，直到目标事件 / 错误 / 超时。"""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DialogError(
                    f"等待 {what} 超时（{timeout:.0f}s）{self._logid_hint()}")
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise DialogError(
                    f"等待 {what} 超时（{timeout:.0f}s）{self._logid_hint()}") from None
            except websockets.ConnectionClosed as exc:
                raise DialogError(
                    f"等待 {what} 时连接关闭 code={exc.code}"
                    f"{self._logid_hint()}") from exc
            if isinstance(msg, str):
                logger.warning("握手期收到非二进制消息：%s", msg[:200])
                continue
            frame = parse_frame(msg)
            if frame is None:
                continue
            self._handle_frame(frame)
            if self._handshake_failed.is_set():
                code, message = self._handshake_error or (-1, "未知错误")
                raise DialogError(
                    f"握手失败[{code}]：{message}{self._logid_hint()}", code=code)
            if frame.event_id == event_id:
                return

    # ---- 收发循环 ----

    async def _send_loop(self) -> None:
        """上行循环：audio_queue → TaskRequest(200) 音频帧（带 sid，定案 #2）。

        server_vad 模式文档明确无需静音保活（模块 docstring），队列为空时只等待。
        """
        try:
            while not self._stop_event.is_set():
                try:
                    pcm = await asyncio.wait_for(self._audio_queue.get(),
                                                 timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if pcm is None:  # stop() 的退出信号
                    break
                await self._ws.send(build_audio_frame(pcm, self._session_id))
        except websockets.ConnectionClosed:
            logger.info("发送循环：连接已关闭")
        except Exception:
            logger.exception("发送循环异常")

    async def _recv_loop(self) -> None:
        """下行循环：解析帧 → _handle_frame 分发；连接关闭后置 disconnected。"""
        try:
            while True:
                try:
                    msg = await self._ws.recv()
                except websockets.ConnectionClosed as exc:
                    logger.info("接收循环：连接关闭 code=%s reason=%s",
                                exc.code, exc.reason)
                    break
                if isinstance(msg, str):
                    logger.warning("收到非二进制消息：%s", msg[:200])
                    continue
                frame = parse_frame(msg)
                if frame is not None:
                    self._handle_frame(frame)
        except Exception:
            logger.exception("接收循环异常")
        finally:
            if not self._session_ready:
                # 握手中途断连：兜底唤醒 _recv_until（正常它会因断连自行抛错）
                self._handshake_failed.set()
            self._set_state(STATE_DISCONNECTED)

    async def _close_ws_quietly(self) -> None:
        """关连接（幂等）。close 最多等 3s：websockets 的 close_timeout 默认
        10s，对端不回 close 帧时会拖穿 stop() 的 SHUTDOWN_TIMEOUT 预算。"""
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=3.0)
            except Exception:
                logger.debug("关闭 WebSocket 超时/失败（忽略）", exc_info=True)

    # ---- 收尾链路（文档 25.06.05 修订记录推荐顺序）----

    async def _finish_session_core(self) -> None:
        """FinishSession(102) → 等 SessionFinished(152)。超时是常态不抛。"""
        try:
            await self._ws.send(
                build_event_frame(EV_FINISH_SESSION, None, self._session_id))
        except websockets.ConnectionClosed:
            logger.info("发送 FinishSession 时连接已关闭")
            return
        except Exception:
            logger.exception("发送 FinishSession 失败")
            return
        try:
            await asyncio.wait_for(self._finished_evt.wait(),
                                   timeout=self.FINISH_SESSION_TIMEOUT)
        except asyncio.TimeoutError:
            # 前置约束 #2：AI 正要开口时收尾，152 不回是常态；继续走
            # FinishConnection（随后服务端会回 55000000 "the stream is done"，
            # 由 _finishing 抑制，见 _on_error_frame）
            logger.warning("等待 SessionFinished(152) 超时 %.0fs（收尾常态路径）",
                           self.FINISH_SESSION_TIMEOUT)

    async def _shutdown(self) -> None:
        """完整收尾：FinishSession → FinishConnection → 停循环 → 关 ws。"""
        self._finishing = True
        ws = self._ws
        if ws is not None and self._session_ready and not self._finished_evt.is_set():
            await self._finish_session_core()
        if ws is not None:
            try:
                await ws.send(build_event_frame(EV_FINISH_CONNECTION))
                # 给服务端回 52 ConnectionFinished（或 55000000）的机会
                await asyncio.sleep(0.3)
            except websockets.ConnectionClosed:
                logger.info("发送 FinishConnection 时连接已关闭")
            except Exception:
                logger.exception("发送 FinishConnection 失败")

        if self._stop_event is not None:
            self._stop_event.set()
        if self._audio_queue is not None:
            try:
                self._audio_queue.put_nowait(None)   # _send_loop 的退出信号
            except Exception:
                pass
        for task in (self._send_task, self._recv_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        await self._close_ws_quietly()

    async def _send_event(self, frame: bytes) -> None:
        """在 loop 线程发一帧客户端事件（interrupt 等单帧路径）。"""
        try:
            await self._ws.send(frame)
        except websockets.ConnectionClosed:
            logger.info("发送事件帧时连接已关闭：%dB", len(frame))
        except Exception:
            logger.exception("发送事件帧失败")

    # ---- 同步接口（任意线程调用；照搬 ASR 客户端的线程编排）----

    def start(self) -> None:
        """建连并开会话（阻塞至 SessionStarted 或失败）。

        失败抛 DialogError（消息含错误码与 X-Tt-Logid）；成功时 on_state 已
        依次回调 connecting → connected → session_ready。超时预算含 ws 建连
        （open_timeout 默认 10s）+ 两段握手 + 调度余量；超时会 cancel 建连
        协程（触发其收尾关 ws）再清理线程。
        """
        if self._thread is not None:
            raise DialogError("对话客户端已启动，请先 stop")
        self._reset_session_state()

        self._loop = asyncio.new_event_loop()

        def _run() -> None:
            # 会话局部引用：_cleanup_loop 在收尾超时分支可能仍会（防御性）
            # 触碰 self._loop，线程 finally 绝不能动态读取它——否则 loop 被
            # 抢置 None 时 close() 抛 AttributeError、loop 永不 close。
            loop = self._loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            finally:
                # 线程收尾兜底：cancel 残留任务（超时路径的 _connect、收尾
                # 未走完的 send/recv 等）、关残留 ws、close loop（否则每次
                # 会话泄漏一个 selector fd，pending 任务留告警）。
                try:
                    pending = [t for t in asyncio.all_tasks(loop)
                               if not t.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.wait(pending, timeout=3.0))
                    ws, self._ws = self._ws, None
                    if ws is not None:
                        loop.run_until_complete(
                            asyncio.wait_for(ws.close(), timeout=3.0))
                except Exception:
                    logger.debug("dialog-loop 收尾清理异常（忽略）", exc_info=True)
                finally:
                    # H1 硬保证：无论上面是否异常，loop 必须 close
                    try:
                        loop.close()
                    except Exception:
                        logger.debug("关闭事件循环失败（忽略）", exc_info=True)

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="dialog-loop")
        self._thread.start()

        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        timeout = (self.CONNECT_TIMEOUT + self.SESSION_TIMEOUT
                   + self.START_MARGIN)
        try:
            future.result(timeout=timeout)
        except DialogError:
            self._cleanup_loop()
            raise
        except TimeoutError:
            future.cancel()
            try:
                future.result(timeout=3.0)   # 等取消传播完成（_connect 关 ws）
            except BaseException:
                pass
            self._set_state(STATE_ERROR)
            self._cleanup_loop()
            raise DialogError(f"启动超时（>{timeout:.0f}s）：请检查网络后重试")
        except Exception as exc:
            self._cleanup_loop()
            raise DialogError(f"启动失败：{type(exc).__name__}: {exc}") from exc

    def send_audio(self, pcm: bytes) -> None:
        """上行一包 PCM（int16 小端 16k，20ms=640B）。未就绪时静默丢弃。

        本方法从 PortAudio 采集回调线程调用（20ms 一次），不能抛异常：经
        call_soon_threadsafe 投递（不建协程，无「检查后 loop 被清理」的
        TOCTOU 泄漏），入队与背压（超上限丢最旧）都在 loop 线程内做。
        """
        loop = self._loop
        if not self._session_ready or loop is None or self._audio_queue is None:
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_audio, pcm)
        except RuntimeError:
            logger.warning("事件循环已关闭，丢弃音频块")

    def _enqueue_audio(self, pcm: bytes) -> None:
        """loop 线程内入队；队列积压达上限时丢最旧一包（实时性优先）。"""
        queue = self._audio_queue
        if queue is None:
            return
        if queue.qsize() >= self.AUDIO_QUEUE_MAX:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(pcm)

    def interrupt(self) -> None:
        """ClientInterrupt(515)。

        MVP 不调用：server_vad 模式下服务端检测到用户开口会自行停止生成并
        用 ASRInfo(450) 通知停播；515 是 push_to_talk 模式的打断手段，
        留给将来接 push_to_talk（DialogController 不引用）。
        """
        loop = self._loop
        if not self._session_ready or loop is None or not loop.is_running():
            # 未就绪 / 已清理 / 已停（收尾中）一律丢弃：call_soon_threadsafe
            # 对「已停未关」的 loop 会静默入队、永不执行 → 协程泄漏
            return
        coro = self._send_event(
            build_event_frame(EV_CLIENT_INTERRUPT, None, self._session_id))
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except (RuntimeError, AttributeError):
            # 检查与投递之间 loop 被 cleanup 的竞态：丢弃且不外抛
            coro.close()
            logger.warning("事件循环已关闭，丢弃打断帧")

    def finish_session(self) -> None:
        """会话收尾：FinishSession(102) → 等 152（超时容忍）。重复调用安全。"""
        if self._loop is None or not self._session_ready:
            return
        if self._finished_evt.is_set() or not self._loop.is_running():
            return
        future = asyncio.run_coroutine_threadsafe(self._finish_session_core(),
                                                  self._loop)
        try:
            future.result(timeout=self.FINISH_SESSION_TIMEOUT + 2.0)
        except Exception:
            logger.exception("FinishSession 异常")

    def stop(self) -> None:
        """完整收尾并停线程（阻塞，含最多 FINISH_SESSION_TIMEOUT 的等待）。重复调用安全。

        收尾超时不外抛：cancel 收尾协程后强制清理（loop 线程的 finally
        兜底关闭残留 ws 与任务）。
        """
        if self._loop is None:
            return
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                future.result(timeout=self.SHUTDOWN_TIMEOUT)
            except TimeoutError:
                logger.warning("对话客户端收尾超时（>%.0fs），强制清理",
                               self.SHUTDOWN_TIMEOUT)
                future.cancel()
                try:
                    future.result(timeout=2.0)
                except BaseException:
                    pass
            except Exception:
                logger.exception("对话客户端收尾失败")
        self._cleanup_loop()

    def _cleanup_loop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running() and not self._stop_requested:
            # 只投递一次：收尾进行中再投一次 loop.stop 会打断 _run finally 的
            # run_until_complete（RuntimeError），跳过残留 ws 的 close。
            self._stop_requested = True
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread              # 会话局部：并发 cleanup 可能已清 None
        if thread is not None:
            thread.join(timeout=3)
            if thread.is_alive():
                # 线程仍在收尾（loop 侧最多再 ~6s 自行 close loop / 关残留
                # ws）。此处绝不能抢置 self._loop/_ws：_run 的 finally 还要
                # 用它们完成收尾，抢置会导致 loop 永不 close、ws 泄漏。
                # 保留全部引用，线程（daemon）自会收尾。
                logger.warning(
                    "dialog-loop 线程未在 3s 内退出（daemon，由其自行收尾）")
                return
            self._thread = None
        self._loop = None
        self._audio_queue = None
        self._stop_event = None
        self._send_task = None
        self._recv_task = None
        self._ws = None
        self._session_ready = False
