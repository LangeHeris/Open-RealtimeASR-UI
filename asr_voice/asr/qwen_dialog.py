"""阿里云百炼 Qwen-Audio Realtime —— 对话客户端类。

协议：OpenAI-Realtime 风格的纯 JSON over WebSocket（无豆包那套二进制帧
codec）。一次实例 = 一条 WebSocket = 一个会话。线程模型照搬豆包客户端
（独立 dialog-loop 守护线程跑 asyncio loop），但不继承——两家 wire 协议、
握手时序、错误模型都不同，共享的只有线程编排骨架与 dialog_events 中立词汇。

关键差异（对豆包）：
- 鉴权：HTTP 头 Authorization: Bearer <api_key>（复用 aliyun.api_key），
  无 app_id/access_token 三元组、无二进制签名；模型走 URL ?model= 查询参
- 握手：单段——connect → 收 session.created（仅日志）→ 发 session.update →
  收 session.updated = 就绪（无 StartConnection/StartSession 两段）
- 音频：上行 input_audio_buffer.append {audio: base64(pcm16k)}，
  下行 response.audio.delta {delta: base64(pcm24k)}——PCM 参数与豆包一致
- 收尾：无 FinishSession/FinishConnection 握手，stop() 直接 cancel tasks +
  close ws（response.cancel 预留给打断，MVP 不调用）
- 错误：error 事件的 code 是字符串（如 invalid_value），按 error.type 分
  invalid_request_error（不可重连）/ server_error（可重连）
- session.id 仅本次连接标识，重连即新会话（不跨进程续接上下文）

事件翻译（wire JSON type → 中立词汇）见 _handle_event；中立词汇定义在
dialog_events.py。设计文档：docs/superpowers/specs/2026-09-11-qwen-audio-realtime-provider-design.md。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import threading
import time
from typing import Callable, Optional

import websockets

from .dialog_events import (
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

# 默认端点（legacy 旧域名，含默认模型）；region/workspace 路由由 loader 传 ws_url 覆盖
DEFAULT_WS_URL = ("wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
                  "?model=qwen-audio-3.0-realtime-plus")

# ---- 状态（on_state 回调取值；无豆包的 CONNECTED 中间态，单段握手）----
STATE_CONNECTING = "connecting"
STATE_SESSION_READY = "session_ready"
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"

StateCallback = Callable[[str], None]
EventCallback = Callable[[str, dict], None]        # (中立事件名, payload)
AudioCallback = Callable[[bytes], None]
ErrorCallback = Callable[[DialogErrorEvent], None]


def _qwen_error_hint(etype: str, message: str) -> str:
    """把 Qwen 错误类型/消息映射成单行中文提示（悬浮条右截断）。"""
    text = f"{etype} {message}".lower()
    if any(k in text for k in ("auth", "unauthorized", "access denied",
                               "apikey", "api-key", "invalid_api_key")):
        return "鉴权失败：请核对阿里云百炼 API-KEY"
    if "model" in text and any(k in text for k in ("not", "found", "exist")):
        return "模型不可用：请检查 Qwen 模型名与开通状态"
    if any(k in text for k in ("rate", "limit", "quota", "throttl")):
        return "触发限流/额度不足，请稍后重试"
    if etype == "server_error":
        return "服务端错误，将尝试重连"
    if etype == "invalid_request_error":
        return "请求参数有误：请到设置检查 Qwen 配置"
    return (message[:40] or "对话服务异常")


class QwenDialogClient:
    """Qwen-Audio Realtime 对话客户端（一次实例 = 一条 WebSocket = 一个会话）。

    超时类常量做类属性，便于测试按实例覆盖加速：
        CONNECT_TIMEOUT   建 ws + 等 session.created
        SESSION_TIMEOUT   发 session.update + 等 session.updated
        SHUTDOWN_TIMEOUT  stop() 整体上限（无收尾握手，比豆包短）
        START_MARGIN      start() 预算在 CONNECT+SESSION 之上的余量
        AUDIO_QUEUE_MAX   上行音频队列上限（包），满丢最旧
    """

    CONNECT_TIMEOUT = 10.0
    SESSION_TIMEOUT = 15.0
    SHUTDOWN_TIMEOUT = 5.0
    # start() 总预算在 CONNECT+SESSION 之上追加的余量：覆盖 ws 建连
    # （websockets open_timeout 默认 10s）与跨线程调度开销
    START_MARGIN = 15.0
    # 上行音频队列上限（包数）：20ms/包 → 50 包 = 1s；发送滞后超上限时
    # 丢最旧包（实时性优先），防止无界积压
    AUDIO_QUEUE_MAX = 50

    def __init__(
        self,
        api_key: str = "",
        ws_url: str = DEFAULT_WS_URL,
        *,
        voice: str = "longanqian",
        instructions: str = "",
        turn_detection: str = "server_vad",
        vad_threshold: float = 0.5,
        silence_duration_ms: int = 800,
        enable_speech_emotion: bool = True,
        max_history_turns: int = 20,
        enable_search: bool = False,
        on_state: Optional[StateCallback] = None,
        on_event: Optional[EventCallback] = None,
        on_audio: Optional[AudioCallback] = None,
        on_error: Optional[ErrorCallback] = None,
    ):
        self.api_key = api_key
        self.ws_url = ws_url
        self.voice = voice
        self.instructions = instructions
        self.turn_detection = turn_detection
        self.vad_threshold = vad_threshold
        self.silence_duration_ms = silence_duration_ms
        self.enable_speech_emotion = enable_speech_emotion
        self.max_history_turns = max_history_turns
        self.enable_search = enable_search

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
        """每次 start() 前复位会话状态（事件对象换新，避免跨 loop 复用）。"""
        self.session_id = ""
        self._session_ready = False
        self._finishing = False            # 收尾期错误抑制标志（对齐豆包）
        self._ai_transcript_buf = ""       # Task 3：AI 字幕增量累积缓冲
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
        if state == self._state:
            return
        self._state = state
        logger.info("Qwen 对话客户端状态：%s", state)
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
        # 收尾期抑制（对齐豆包 _finishing，兑现 dialog.py 契约「client 已过滤
        # 握手期与收尾期」）：用户主动挂断时在途的服务端 error / response.done
        # {failed} 只记日志、不回调，避免正常退出却弹出误导性错误提示条。
        if self._finishing:
            logger.debug("收尾期间错误 code=%s（%s），常态，不回调 on_error",
                         err.code, err.message)
            return
        logger.error("Qwen 对话错误 code=%s message=%s", err.code, err.message)
        if self.on_error is not None:
            try:
                self.on_error(err)
            except Exception:
                logger.exception("on_error 回调异常")

    # ---- 握手 payload / 连接 ----

    def _build_session_update(self) -> dict:
        """构造 session.update 请求体（承载「完整暴露」的全部可调项，spec §5.4）。

        turn_detection：server_vad 带 threshold + silence_duration_ms；smart_turn
        只带 type（阈值/静音时长由服务端语义模型自管）。input_audio_format/
        output_audio_format 固定 pcm（不暴露）；instructions 总是下发（空串=无人设）。
        """
        if self.turn_detection == "smart_turn":
            td: dict = {"type": "smart_turn"}
        else:
            td = {"type": "server_vad",
                  "threshold": self.vad_threshold,
                  "silence_duration_ms": self.silence_duration_ms}
        return {"type": "session.update", "session": {
            "modalities": ["audio", "text"],
            "voice": self.voice,
            "instructions": self.instructions,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "enable_speech_emotion": self.enable_speech_emotion,
            "max_history_turns": self.max_history_turns,
            "enable_search": self.enable_search,
            "turn_detection": td,
        }}

    async def _ws_connect(self):
        """建立 WebSocket（独立方法，测试子类替换它注入 FakeWS）。"""
        headers = [("Authorization", f"Bearer {self.api_key}")]
        try:
            return await websockets.connect(self.ws_url, max_size=None,
                                            additional_headers=headers)
        except TypeError:  # 旧版 websockets 兼容
            return await websockets.connect(self.ws_url, max_size=None,
                                            extra_headers=headers)

    @staticmethod
    def _describe_connect_failure(exc: Exception) -> str:
        """把建连异常转成 DialogError 消息。前置约束：绝不 dump Authorization 头，
        只允许 status_code / 响应体两个字段进消息与日志。"""
        resp = getattr(exc, "response", None)
        msg = f"WebSocket 连接失败：{exc}"
        details: list[str] = []
        status = getattr(resp, "status_code", None)
        if status is not None:
            details.append(f"status={status}")
        body = getattr(resp, "body", None)
        if isinstance(body, (bytes, bytearray)) and body:
            details.append(f"响应体={bytes(body).decode('utf-8', 'replace')[:300]}")
        if details:
            msg += "（" + "，".join(details) + "）"
        if status in (401, 403):
            msg += ("（鉴权失败：请到设置 -> 引擎密钥核对阿里云百炼 API-KEY，并"
                    "确认已开通「Qwen-Audio Realtime」服务）")
        return msg

    @staticmethod
    def _dumps(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _parse_msg(raw) -> Optional[dict]:
        """容错解析一条服务端消息（bytes/str → dict）；坏 JSON / 非 dict 返回 None。"""
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("收到非 JSON 消息：%s", raw[:200])
            return None
        return obj if isinstance(obj, dict) else None

    async def _connect(self) -> None:
        """单段握手：建 ws → 收 session.created → 发 session.update → 收 session.updated。

        任一步失败/超时抛 DialogError（消息含 code）；失败与取消（start 超时
        会 cancel 本协程）路径统一由 finally 关闭 ws；成功后置 session_ready、
        发 SESSION_READY 中立事件、启动收发循环。
        """
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._set_state(STATE_CONNECTING)
        try:
            self._ws = await self._ws_connect()
        except Exception as exc:
            self._set_state(STATE_ERROR)
            raise DialogError(self._describe_connect_failure(exc)) from exc

        try:
            created = await self._recv_until_type("session.created",
                                                  self.CONNECT_TIMEOUT,
                                                  "session.created")
            logger.info("Qwen session.created：%s",
                        (created.get("session") or {}))
            await self._ws.send(self._dumps(self._build_session_update()))
            updated = await self._recv_until_type("session.updated",
                                                  self.SESSION_TIMEOUT,
                                                  "session.updated")
            session = updated.get("session") if isinstance(updated, dict) else {}
            session = session if isinstance(session, dict) else {}
            self.session_id = str(session.get("id", ""))
            self._session_ready = True
        except DialogError:
            self._set_state(STATE_ERROR)
            raise
        except Exception as exc:
            self._set_state(STATE_ERROR)
            raise DialogError(
                f"握手发送/接收失败：{type(exc).__name__}: {exc}") from exc
        finally:
            # 失败/取消（start 超时 cancel 本协程）路径统一关连接防泄漏；
            # 成功路径 _session_ready 已置位，保留连接
            if not self._session_ready:
                await self._close_ws_quietly()

        self._set_state(STATE_SESSION_READY)
        self._notify_event(SESSION_READY, {"dialog_id": self.session_id})
        self._send_task = asyncio.create_task(self._send_loop(),
                                              name="qwen-dialog-send")
        self._recv_task = asyncio.create_task(self._recv_loop(),
                                              name="qwen-dialog-recv")

    async def _recv_until_type(self, want_type: str, timeout: float,
                               what: str) -> dict:
        """握手期收消息：逐条 _parse_msg，命中 want_type 返回该 dict；遇 error
        事件抛 DialogError；超时/断连抛 DialogError。"""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DialogError(f"等待 {what} 超时（{timeout:.0f}s）")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise DialogError(f"等待 {what} 超时（{timeout:.0f}s）") from None
            except websockets.ConnectionClosed as exc:
                raise DialogError(
                    f"等待 {what} 时连接关闭 code={exc.code}") from exc
            msg = self._parse_msg(raw)
            if msg is None:
                continue
            mtype = msg.get("type", "")
            if mtype == "error":
                err = self._error_from_msg(msg)
                raise DialogError(f"握手失败[{err.code}]：{err.message}",
                                  code=err.code)
            if mtype == want_type:
                return msg
            logger.debug("握手期忽略事件 %s", mtype)

    # ---- 收发循环 ----

    async def _send_loop(self) -> None:
        """上行循环：audio_queue → input_audio_buffer.append {audio: base64(pcm)}。"""
        try:
            while not self._stop_event.is_set():
                try:
                    pcm = await asyncio.wait_for(self._audio_queue.get(),
                                                 timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if pcm is None:  # stop() 的退出信号
                    break
                evt = {"type": "input_audio_buffer.append",
                       "audio": base64.b64encode(pcm).decode("ascii")}
                await self._ws.send(self._dumps(evt))
        except websockets.ConnectionClosed:
            logger.info("发送循环：连接已关闭")
        except Exception:
            logger.exception("发送循环异常")

    async def _recv_loop(self) -> None:
        """下行循环：_parse_msg → _handle_event；连接关闭后置 disconnected。

        握手已在 _connect 同步完成（recv_loop 仅就绪后才 spawn），无需
        豆包那套 _handshake_failed 兵底。
        """
        try:
            while True:
                try:
                    raw = await self._ws.recv()
                except websockets.ConnectionClosed as exc:
                    logger.info("接收循环：连接关闭 code=%s reason=%s",
                                exc.code, exc.reason)
                    break
                msg = self._parse_msg(raw)
                if msg is not None:
                    self._handle_event(msg)
        except Exception:
            logger.exception("接收循环异常")
        finally:
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

    # ---- 下行分发（表 B：wire JSON type → 回调/中立事件）----

    def _handle_event(self, msg: dict) -> None:
        """wire JSON type → 回调/中立事件（表 B）。只在 asyncio 线程调用。"""
        mtype = msg.get("type", "")

        # —— 下行音频 / 错误 / 响应结束 ——
        if mtype == "response.audio.delta":
            pcm = self._decode_audio(msg.get("delta", ""))
            if pcm:
                self._notify_audio(pcm)
            return
        if mtype == "error":
            self._on_error_event(msg)
            return
        if mtype == "response.done":
            self._on_response_done(msg)
            return

        # —— 用户转写（delta = text已确定 + stash暂存 = 当前完整假设，无需累积）——
        if mtype == "conversation.item.input_audio_transcription.delta":
            text = str(msg.get("text", "")) + str(msg.get("stash", ""))
            if text:
                self._notify_event(USER_TRANSCRIPT,
                                   {"text": text, "is_final": False})
            return
        if mtype == "conversation.item.input_audio_transcription.completed":
            text = str(msg.get("transcript", ""))
            if text:
                self._notify_event(USER_TRANSCRIPT,
                                   {"text": text, "is_final": True})
            return

        # —— 轮次边界 ——
        if mtype == "input_audio_buffer.speech_started":
            # 用户开口打断：清字幕累积 + BARGE_IN（服务端会自动 cancel 当前响应）
            self._ai_transcript_buf = ""
            self._notify_event(BARGE_IN, {})
            return
        if mtype == "input_audio_buffer.speech_stopped":
            # gap 3：smart_turn 的 turn_invalid 不 emit（barge-in 已切 LISTENING）
            if str(msg.get("reason", "")) != "turn_invalid":
                self._notify_event(USER_TURN_END, {})
            return

        # —— AI 回复 ——
        if mtype == "response.created":
            resp = msg.get("response")
            resp = resp if isinstance(resp, dict) else {}
            self._ai_transcript_buf = ""           # 新一轮，重置字幕累积
            self._notify_event(AI_TURN_START,
                               {"reply_id": str(resp.get("id", ""))})
            return
        if mtype == "response.audio_transcript.delta":
            # gap 1：字幕 delta 是增量，客户端累积后 emit 完整本轮字幕
            delta = str(msg.get("delta", ""))
            if delta:
                self._ai_transcript_buf += delta
                self._notify_event(AI_TRANSCRIPT, {"text": self._ai_transcript_buf})
            return

        # —— 忽略类（不驱动状态机：session.created、ambient_*、conversation.item.*、
        # input_audio_buffer.committed/cleared、response.audio_transcript.done、
        # response.audio.done、content_part.*、output_item.*、function_call_*）——
        logger.debug("忽略事件 %s", mtype)

    @staticmethod
    def _decode_audio(b64: str) -> bytes:
        """base64 → PCM；空/非法容错返回 b\"\"。"""
        if not b64 or not isinstance(b64, str):
            return b""
        try:
            return base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            logger.warning("response.audio.delta base64 解码失败（%d 字符）",
                           len(b64))
            return b""

    @staticmethod
    def _error_from_msg(msg: dict) -> DialogErrorEvent:
        """error 事件 → DialogErrorEvent（code 是字符串；按 type 分可重连性）。"""
        err = msg.get("error")
        err = err if isinstance(err, dict) else {}
        etype = str(err.get("type", ""))
        code = err.get("code", "") or etype or "unknown"
        message = str(err.get("message", "") or etype or "服务端错误")
        return DialogErrorEvent(code, message,
                                hint=_qwen_error_hint(etype, message),
                                reconnectable=(etype == "server_error"))

    def _on_error_event(self, msg: dict) -> None:
        self._notify_error(self._error_from_msg(msg))

    def _on_response_done(self, msg: dict) -> None:
        """response.done：failed → on_error（可重连）；completed/cancelled → AI_TURN_END。

        cancelled（reason=turn_detected）= 被 barge-in 打断，控制器已切 LISTENING，
        再收 AI_TURN_END 与豆包 450→359 同构（控制器幂等处理）。exit_intent 恒 False（gap 4）。
        """
        resp = msg.get("response")
        resp = resp if isinstance(resp, dict) else {}
        status = str(resp.get("status", ""))
        if status == "failed":
            details = resp.get("status_details")
            details = details if isinstance(details, dict) else {}
            reason = str(details.get("reason", "") or details.get("type", ""))
            self._notify_error(DialogErrorEvent(
                reason or "response_failed", f"响应失败：{reason}",
                hint="模型响应失败，将尝试重连", reconnectable=True))
            return
        self._notify_event(AI_TURN_END, {"exit_intent": False})

    # ---- 收尾（无握手）----

    async def _shutdown(self) -> None:
        """无收尾握手：置停止位 → 唤醒 send_loop → cancel tasks → close ws。"""
        self._finishing = True             # 收尾期错误抑制（见 _notify_error）
        if self._stop_event is not None:
            self._stop_event.set()
        if self._audio_queue is not None:
            try:
                self._audio_queue.put_nowait(None)
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

    async def _send_event(self, evt: dict) -> None:
        """在 loop 线程发一条客户端事件（interrupt 等单帧路径）。"""
        try:
            await self._ws.send(self._dumps(evt))
        except websockets.ConnectionClosed:
            logger.info("发送事件时连接已关闭：%s", evt.get("type"))
        except Exception:
            logger.exception("发送事件失败")

    # ---- 同步接口（任意线程调用；照搬豆包线程编排）----

    def start(self) -> None:
        """建连并开会话（阻塞至 session.updated 或失败）。失败抛 DialogError。

        超时预算含 ws 建连（open_timeout 默认 10s）+ 单段握手 + 调度余量；
        超时会 cancel 建连协程（触发其收尾关 ws）再清理线程。
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
                    logger.debug("qwen-dialog-loop 收尾清理异常（忽略）",
                                 exc_info=True)
                finally:
                    # H1 硬保证：无论上面是否异常，loop 必须 close
                    try:
                        loop.close()
                    except Exception:
                        logger.debug("关闭事件循环失败（忽略）", exc_info=True)

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="qwen-dialog-loop")
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
        """response.cancel（push-to-talk 打断预留；MVP 不调用）。

        server_vad/smart_turn 下服务端检测到用户开口自行 cancel 当前响应
        （speech_started→response.done{cancelled}），与豆包 515 地位一致。
        """
        loop = self._loop
        if not self._session_ready or loop is None or not loop.is_running():
            # 未就绪 / 已清理 / 已停（收尾中）一律丢弃：call_soon_threadsafe
            # 对「已停未关」的 loop 会静默入队、永不执行 → 协程泄漏
            return
        coro = self._send_event({"type": "response.cancel"})
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except (RuntimeError, AttributeError):
            # 检查与投递之间 loop 被 cleanup 的竞态：丢弃且不外抛
            coro.close()
            logger.warning("事件循环已关闭，丢弃打断事件")

    def stop(self) -> None:
        """收尾并停线程（阻塞）。无 FinishSession 握手，重复调用安全。

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
                logger.warning("Qwen 对话客户端收尾超时（>%.0fs），强制清理",
                               self.SHUTDOWN_TIMEOUT)
                future.cancel()
                try:
                    future.result(timeout=2.0)
                except BaseException:
                    pass
            except Exception:
                logger.exception("Qwen 对话客户端收尾失败")
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
                    "qwen-dialog-loop 线程未在 3s 内退出（daemon，由其自行收尾）")
                return
            self._thread = None
        self._loop = None
        self._audio_queue = None
        self._stop_event = None
        self._send_task = None
        self._recv_task = None
        self._ws = None
        self._session_ready = False
