"""Hermes Agent API server 客户端（纯网络层，不碰音频）。

分两层，便于单测：
- `SSEFrameBuffer` / `HermesFrameMapper`：纯解析，零 IO
- `HermesClient`：HTTP 会话（建会话 / 发起轮次 / 取消），见 Task 3

为什么不复用 OpenAI 客户端：Hermes 的 `/api/sessions/{id}/chat/stream`
返回的是它自己的「命名事件流」（`event: run.started` / `assistant.delta`
/ ...），不是 OpenAI 的 `chat.completion.chunk`。原始帧见设计文档 §3.5。

音频能力：Hermes 的 API server `audio_api=false`（实测），既不识别也不
合成。断句与识别由 `HermesDialogClient` 在本端补齐。
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime

from .dialog_events import (
    AI_FULL_TEXT, AI_TRANSCRIPT, AI_TURN_END, AI_TURN_START,
    SESSION_READY, DialogError, DialogErrorEvent,
)

logger = logging.getLogger(__name__)

# 与各引擎/客户端模块的取值保持一致，便于控制器侧统一判断
STATE_CONNECTING = "connecting"
STATE_SESSION_READY = "session_ready"
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"

# 取消失败专用错误码：表示 /stop 这次请求本身没发出去（服务端可能还在跑），
# 与三种轮次失败出口（code=-1）区分开。兄弟模块据此判定「这不是本轮结束」——
# /stop 的 POST 可能拖到下一轮已经在途时才落地（最长 connect_timeout_ms），
# 若按轮次失败重开半双工闸门，会把刚关上的麦克风在回答中途放开。
CODE_CANCEL_FAILED = -2

# stop() 等在途 worker 退出的上限：关流后读线程通常立刻返回，卡住时只记警告
# 不阻塞收尾（daemon 线程自会了结，与 qwen_dialog.py 收尾同量级）。
_STOP_JOIN_TIMEOUT_S = 3.0

# SSE 帧以空行分隔；服务端可能用 \n 或 \r\n
_FRAME_SEP = re.compile(r"\r?\n\r?\n")


def _parse_frame(raw: str) -> tuple[str, str]:
    """解析单个 SSE 帧文本，返回 (event, data)。

    data 可以是多行（SSE 规范允许多个 data: 行，用 \n 连接）。
    event 缺省 "message"（规范默认值），Hermes 实际总会带 event:。
    """
    event = "message"
    data_lines: list[str] = []
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if not line or line.startswith(":"):
            continue                      # 空行与注释行（心跳）跳过
        if ":" in line:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]         # 规范：冒号后恰好一个空格要吃掉
        else:
            field, value = line, ""
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    return event, "\n".join(data_lines)


class SSEFrameBuffer:
    """把 SSE 文本流切成 (event, data) 帧。纯解析、零 IO，可单测。

    必须缓冲而不是逐行处理：网络分片的边界与帧边界无关，一帧可能被切成
    两段到达，也可能几帧挤在一段里。尾巴留在缓冲等下一次 feed。
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """喂入一段文本，返回本次能完整解析出的帧列表。"""
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while True:
            m = _FRAME_SEP.search(self._buf)
            if m is None:
                break
            raw = self._buf[: m.start()]
            self._buf = self._buf[m.end():]
            event, data = _parse_frame(raw)
            if data:
                out.append((event, data))
        return out


class HermesFrameMapper:
    """Hermes 命名事件 → dialog_events 中立事件。

    **必须按 event: 名分派，不能按 data 里有没有 delta 字段判断** ——
    `tool.progress` 帧同样带 `delta`（如 tool_name=_thinking 时回显正文），
    `assistant.completed` 带完整 `content`。若图省事「见到 delta 就累加」，
    正文会被重复累积（实测得到「收到收到」）。

    run_id 单独留存：取消要走 `POST /v1/runs/{run_id}/stop`，而它只在
    `run.started` 帧里出现一次。
    """

    def __init__(self) -> None:
        self.run_id = ""
        self.session_id = ""
        self.reply_id = ""
        self._text_buf = ""               # 本轮正文的累积缓冲（见 assistant.delta）

    def map(self, event: str, data: str) -> list[tuple[str, dict]]:
        """返回该帧对应的中立事件列表（可能为空）。

        形状与豆包一致（`DoubaoDialogClient._translate_event`）：一帧可产出
        0..N 个中立事件（assistant.completed 同时产出 AI_FULL_TEXT 与
        AI_TURN_END）；且 `AI_TRANSCRIPT` 的 text 与豆包/Qwen 一样是**全量**
        —— Hermes 给的是增量，累积在本卡完成（控制器只认全量）。
        """
        try:
            payload = json.loads(data) if data else {}
        except (ValueError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []

        if event == "run.started":
            self.run_id = str(payload.get("run_id", "") or "")
            self.session_id = str(payload.get("session_id", "") or "")
            return []
        if event == "message.started":
            msg = payload.get("message")
            self.reply_id = str((msg or {}).get("id", "") or "")
            self._text_buf = ""           # 新消息：正文累积从头开始
            return [(AI_TURN_START, {"reply_id": self.reply_id})]
        if event == "assistant.delta":
            delta = str(payload.get("delta", "") or "")
            if not delta:
                return []
            # Hermes 只给增量，但中立事件契约要求 AI_TRANSCRIPT 是**全量**
            # （core/dialog.py「累积责任在客户端」），故先累积再抛，与 qwen 的
            # _ai_transcript_buf 同形；str() 顺带兜住非字符串 delta。
            self._text_buf += delta
            return [(AI_TRANSCRIPT, {"text": self._text_buf})]
        if event == "assistant.completed":
            out: list[tuple[str, dict]] = []
            content = str(payload.get("content", "") or "")
            if content:
                out.append((AI_FULL_TEXT, {"text": content}))
            # exit_intent 恒 False：Hermes 协议没有「用户要退出」的信号，线里的
            # interrupted 只表示「本轮被打断」。core/dialog.py 会 bool() 它并据此
            # _begin_stop() 挂断整个对话 —— 而按热键重说只是取消本轮回到聆听
            # （与 qwen_dialog.py 同一处置）。
            out.append((AI_TURN_END, {"exit_intent": False}))
            self._text_buf = ""
            return out
        if event == "run.cancelled":
            # 同上：取消本轮 ≠ 退出对话；抛真值会让「能重说」被直接挂断
            self._text_buf = ""
            return [(AI_TURN_END, {"exit_intent": False})]
        # tool.progress 及其余事件不进正文（见类 docstring 的重复累积陷阱）
        return []


class HermesClient:
    """Hermes API server 的 HTTP 会话层。纯网络，不碰音频。

    线程模型：start()/cancel() 在调用方线程同步执行；start_turn() 起一个
    worker 线程读 SSE，回调从该线程发出 —— 与 DoubaoDialogClient 的
    asyncio 线程回调同语义，控制器侧已是 queued 信号，无需改动。

    用标准库 urllib 而非 requests/httpx：与 postprocess/openai_client.py
    同策略，不给打包版 exe 增重。
    """

    def __init__(self, *, base_url: str, api_key: str,
                 model: str = "hermes-agent",
                 session_title: str = "ORI 语音对话",
                 system_hint: str = "",
                 connect_timeout_ms: int = 5000,
                 turn_timeout_ms: int = 60000,
                 on_state=None, on_event=None, on_audio=None, on_error=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self._session_title = session_title
        self._system_hint = system_hint
        self._connect_timeout_s = max(0.5, connect_timeout_ms / 1000.0)
        self._turn_timeout_s = max(1.0, turn_timeout_ms / 1000.0)

        self.on_state = on_state
        self.on_event = on_event
        self.on_audio = on_audio
        self.on_error = on_error

        self._state = STATE_DISCONNECTED
        self._session_id = ""
        self._active_title = ""    # 最近一次实际发出的会话标题（带唯一后缀，诊断用）
        self._title_seq = 0        # 同进程内标题序号：时间戳只到秒，重试可能落在同一秒
        self._run_id = ""
        self._stop_fired = False
        self._cancel_requested = False
        self._stream_resp = None          # 在途 SSE 响应：stop() 要能关它唤醒读线程
        self._stopped = False             # stop() 之后不再发任何事件回调（终态）
        self._turn_thread = None          # type: ignore[var-annotated]
        self._pending_text = ""           # 被「上一轮还活着」拒掉的那句：轮末补发（W1b）
        self._turn_lock = threading.Lock()   # 串行化「上一轮是否还活着」的判定
        self._stop_lock = threading.Lock()   # 串行化 取消闩/run_id/是否已打 stop

    # ---- 状态与回调 ----

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        return self._state == STATE_SESSION_READY

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if self.on_state is not None:
            self.on_state(state)

    def _notify_event(self, neutral: str, payload: dict) -> None:
        # stop() 之后一律不再回调：控制器收尾后会把同一批回调接到下一个会话，
        # 残留事件会把上一轮的正文喂进新会话（见 stop 的 docstring）。
        if self._stopped:
            return
        if self.on_event is not None:
            self.on_event(neutral, payload)

    def _notify_error(self, err: DialogErrorEvent) -> None:
        if self.on_error is not None:
            self.on_error(err)

    # ---- HTTP ----

    def _open(self, path: str, *, method: str = "GET",
              body: dict = None, timeout_s: float = 5.0):
        """发起 HTTP 请求，返回响应对象（调用方负责 close）。"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method)
        return urllib.request.urlopen(req, timeout=timeout_s)

    @staticmethod
    def _read_json(resp) -> dict:
        raw = resp.read().decode("utf-8", "replace")
        try:
            out = json.loads(raw)
        except ValueError:
            return {}
        return out if isinstance(out, dict) else {}

    # ---- 生命周期 ----

    def start(self) -> None:
        """健康检查 + 建会话。阻塞至就绪；失败抛 DialogError。

        先探 /health 再建会话：地址填错与密钥填错是两种最常见的配置失误，
        分开报错能省一轮排查（与豆包客户端分两步握手同理）。两步**各自**捕获
        HTTPError：合在一起时 /health 的 HTTP 错误会被报成「建会话失败」，
        与上面这句承诺自相矛盾（H52）。
        """
        self._set_state(STATE_CONNECTING)
        try:
            with self._open("/health", timeout_s=self._connect_timeout_s) as resp:
                if resp.status != 200:
                    raise DialogError(f"Hermes 健康检查失败：HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            raise self._http_dialog_error("Hermes 健康检查失败", exc) from exc
        except OSError as exc:
            raise DialogError(f"Hermes 连不上：{exc}") from exc

        body = self._create_session()
        sid = str((body.get("session") or {}).get("id", "") or "")
        if not sid:
            raise DialogError("Hermes 建会话失败：响应里没有 session.id")
        self._session_id = sid
        self._set_state(STATE_SESSION_READY)
        self._notify_event(SESSION_READY, {"dialog_id": sid})

    def _build_session_title(self) -> str:
        """配置标题 + 唯一后缀（月日-时分秒-序号-随机）。配置为空则只用后缀。

        序号是为「同一秒内的重试也必得不同标题」：时间戳精度只到秒，仅靠随机数
        仍有小概率撞上，而重试恰恰就发生在同一秒里（撞上就白重试一次）。
        """
        self._title_seq += 1
        suffix = (f"{datetime.now():%m%d-%H%M%S}-{self._title_seq:02d}"
                  f"-{secrets.token_hex(2)}")
        base = str(self._session_title or "").strip()
        return f"{base} {suffix}" if base else suffix

    def _create_session(self) -> dict:
        """建会话；标题撞车时换一个后缀重试一次（H51）。

        为什么标题必须带唯一后缀：Hermes 服务端要求标题**全局唯一**，重复即
        400 invalid_title；而本客户端从不关闭会话（stop() 只 cancel 当轮 run，
        全仓没有 DELETE 会话的调用），若用配置里的常量标题，**第二次进对话必被
        拒**（实测：第一次正常，之后每次都 400）。配置标题保留为前缀，用户仍能
        在服务端会话列表里认出自己的会话。
        只对 invalid_title 重试一次：其余 4xx/5xx 重发没有意义，只会把真错误
        拖慢、还可能把状态码换成更晚的那一个。
        """
        for attempt in (1, 2):
            title = self._build_session_title()
            self._active_title = title
            try:
                with self._open("/api/sessions", method="POST",
                                body={"title": title},
                                timeout_s=self._connect_timeout_s) as resp:
                    return self._read_json(resp)
            except urllib.error.HTTPError as exc:
                code, message = self._read_error(exc)
                if attempt == 1 and (code == "invalid_title"
                                     or "already in use" in message.lower()):
                    logger.info("Hermes 会话标题被占用，换后缀重试：%s", title)
                    continue
                raise self._http_dialog_error("Hermes 建会话失败", exc,
                                              code=code, message=message) from exc
            except OSError as exc:
                raise DialogError(f"Hermes 连不上：{exc}") from exc

    @staticmethod
    def _read_error(exc) -> tuple:
        """读 HTTPError 的响应体 → (error.code, error.message)（取不到给空串）。

        Hermes 的错误体形如
        {"error": {"message": "Title already in use by session api_…",
                   "type": "invalid_request_error", "code": "invalid_title"}}，
        可诊断的只有 message 那一句：原实现只报 HTTP 状态码，日志里看不出原因
        （H51 是手工复现才定位的），故把体读出来（H52）。解析不了就退化成原文
        （空白折叠 + 截断 200 字）。
        """
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            return "", ""
        flat = " ".join(raw.split())[:200]
        try:
            payload = json.loads(raw)
        except ValueError:
            return "", flat
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                msg = str(err.get("message") or "").strip()[:200]
                return code, (msg or flat)
            if isinstance(err, str) and err.strip():
                return "", err.strip()[:200]
        return "", flat

    @classmethod
    def _http_dialog_error(cls, prefix: str, exc, code: str = "",
                           message: str = "") -> DialogError:
        """HTTPError → DialogError：401/403 保持既有「鉴权失败」文案（H46 已实测），
        其余带上状态码与服务端错误体（未预解析时才读体）。"""
        if exc.code in (401, 403):
            return DialogError("Hermes 鉴权失败：请检查 API Key")
        if not code and not message:
            code, message = cls._read_error(exc)
        detail = f"（{message}）" if message else ""
        return DialogError(f"{prefix}：HTTP {exc.code}{detail}")

    def start_turn(self, text: str) -> None:
        """发起一轮对话。立即返回，SSE 在 worker 线程读。"""
        with self._turn_lock:
            if self._turn_thread is not None and self._turn_thread.is_alive():
                # 不丢句（W1b）：这一句先存下，等旧轮收尾时补发起轮（见 _finish_turn）。
                # 用户按取消后立刻重说时，/stop 到服务端 ai_turn_end 之间真有
                # 1.14~4.11 秒窗口（真机实测），以前这里只报一句「还在处理上一句」
                # 就把刚识别出来的整句静默吞掉了。错误提示照发：用户有权知道这句
                # 得等上一轮收尾。后到覆盖先到（同一窗口内连说两句，留最后一句）。
                self._pending_text = text
                self._notify_error(DialogErrorEvent(
                    -1, "上一轮尚未结束", "还在处理上一句"))
                return
            # 取消闩故意不在开轮时清：用户可能在 run.started 帧到达前就按了取消
            # （Task 5 的取消由 THINKING 迁移触发，可能早于 start_turn），那次取消
            # 要靠 _maybe_fire_stop 补发；闩只在轮次结束时清（见 _finish_turn），
            # 避免上一轮的取消波及下一轮。别在开轮时清它 —— 那会让
            # 早按的取消被静默丢掉：界面显示已取消，服务端其实还在跑 run/工具。
            with self._stop_lock:
                self._stop_fired = False
            self._turn_thread = threading.Thread(
                target=self._run_turn, args=(text,),
                daemon=True, name="hermes-turn")
            self._turn_thread.start()

    def _run_turn(self, text: str) -> None:
        """worker：POST 流式接口，逐帧映射成中立事件发出去。"""
        mapper = HermesFrameMapper()
        buf = SSEFrameBuffer()
        # 本轮是否已经把 AI_TURN_END 交给控制器了（mapper 对 assistant.completed
        # 与 run.cancelled 都会发）：读循环正常结束时靠它判断「是不是还欠一句收场」
        turn_ended = False
        message = (f"{self._system_hint}\n\n{text}"
                   if self._system_hint else text)
        try:
            resp = self._open(
                f"/api/sessions/{self._session_id}/chat/stream",
                method="POST", body={"message": message},
                timeout_s=self._turn_timeout_s)
        except Exception as exc:
            self._notify_error(DialogErrorEvent(
                -1, f"Hermes 发起失败：{exc}", "Hermes 请求失败"))
            self._finish_turn()          # 没跑起来的轮次也要清闩，否则泄漏到下一轮
            return
        self._stream_resp = resp         # 挂出来：stop() 要靠它关流唤醒本线程
        try:
            if self._stopped:
                return                   # 与 stop() 擦肩而过（它没来得及关流）：直接收尾
            for raw in resp:
                if self._stopped:        # stop() 已收场：别再往下读、别再发事件
                    break
                for event, data in buf.feed(raw.decode("utf-8", "replace")):
                    for neutral, payload in mapper.map(event, data):
                        if neutral == AI_TURN_END:
                            turn_ended = True
                        self._notify_event(neutral, payload)
                self._maybe_fire_stop(mapper.run_id)
            # 流「正常结束」不等于本轮完成：服务端或代理优雅关流（chunked end、
            # 连接复用超时）时，读循环只是自然退出 —— 既不抛异常，也没有
            # assistant.completed / run.cancelled 帧。不补发这一条，控制器就永远
            # 等不到 AI_TURN_END：半双工闸门再也不开，麦克风永久失聪，而且界面上
            # 一个字都不提示（比 H21 那种「至少还有横幅」的静默更隐蔽）。
            # _stopped 时不补：那是收场，不是轮次结束；异常路径也不补 —— 那里已经
            # 报了「回答未完成」，是另一种用户可见的结局。
            # exit_intent 恒 False，与 mapper 同一条契约（H11）：补一次收场 ≠ 退出对话。
            # 取消轮不补：控制器 cancel 分支已经把状态收回 LISTENING，迟到的
            # AI_TURN_END 会在**下一轮思考期**把客户端闸门重新打开（与 W1 同源的
            # 竞态）。_cancel_requested 由 _finish_turn 清，此处必然还没清（陷阱 2）。
            # 干净 EOF（非取消）路径照旧补发 —— 那是承重路径
            # （test_orderly_eof_emits_turn_end_once），别顺手删。
            if not turn_ended and not self._stopped and not self._cancel_requested:
                self._notify_event(AI_TURN_END, {"exit_intent": False})
        except Exception as exc:
            # 收场时关流会让读线程抛异常，那不是故障；取消后主动关流（W1a）同样是
            # **预期收束**：控制器已按 cancel 回到 LISTENING，再报一条只会白发横幅。
            if self._stopped:
                pass
            elif self._cancel_requested:
                logger.debug("Hermes 流在取消后收束：%s", exc)
            else:
                # 超时与其余中断分开报（W3.1）：spec §十 第 2 行的文案是
                # 「Hermes 响应超时」；socket.timeout 是 TimeoutError 的别名
                timeout = isinstance(exc, (socket.timeout, TimeoutError))
                logger.warning("Hermes 流中断：%s", exc)
                self._notify_error(DialogErrorEvent(
                    -1,
                    "Hermes 响应超时" if timeout else f"Hermes 流中断：{exc}",
                    "Hermes 响应超时" if timeout else "回答未完成"))
                # ⚠️ 顺序不能反（W3.2 陷阱 4）：先发错误（控制器据此回 LISTENING 开闸），
                # 再 best-effort /stop。cancel() 会置 _cancel_requested —— W1 之后它抑制
                # 兜底 AI_TURN_END，这正是期望；而 /stop 失败会来一条 CODE_CANCEL_FAILED，
                # 那条**不开闸门**，所以必须在它之前把闸门打开。
                # 目的：超时后那个 run 可能还在服务端跑工具，_finish_turn 一清 _run_id
                # 就再也没人停得掉它。
                if self._run_id:
                    self.cancel()
        finally:
            self._stream_resp = None
            try:
                resp.close()
            except Exception:
                logger.debug("关闭 Hermes 流失败", exc_info=True)
            self._finish_turn()

    def _finish_turn(self) -> None:
        """轮次生命周期结束：清取消闩与 run_id。

        闩的复位放在这里而不是 start_turn：开轮时清会擦掉「run.started 帧还没到
        就按下的那次取消」，让它既发不出去也补发不了（见 start_turn 的注释）。
        放到轮次结束清同样能达到「上一轮的取消不波及下一轮」的目的。

        run_id 也必须在这里清空：留着上一轮的值，下一轮的 cancel() 会照着旧 id
        打 /stop（打到早已结束的 run），顺手把 _stop_fired 置真 —— 等本轮的
        run.started 真到了，_maybe_fire_stop 见它已置位就不再停这一轮的 run，
        正是「按了没用，它还在答」；stop() 收场时也会白发一次注定 404 的 /stop，
        让用户每次退出都看到假的「取消失败」。一轮已结束就没有 run 可停。

        残留行为：完全没有轮次在途时调 cancel()，会把闩挂到下一轮上（下一轮一读到
        run.started 就被 stop）。可接受：Task 5 只在 THINKING（有轮次在途或即将发起）
        时取消；且任何一轮结束都会清闩。
        """
        with self._stop_lock:
            self._cancel_requested = False
            self._run_id = ""
        # 被拒的那句在这里补发（W1b，恰好一次）。
        # ⚠️ 陷阱 3：本方法跑在**旧轮自己那个线程**上，若直接调 start_turn，它会看到
        # _turn_thread 就是自己（is_alive 为真）而再次拒绝。所以先在锁内把
        # _turn_thread 置 None，**出锁之后**再起轮（两把锁不许嵌套）。
        # stop() 收场时 _stopped 为真：不得补发，否则残留事件会喂给已收场/新会话。
        with self._turn_lock:
            self._turn_thread = None
            pending, self._pending_text = self._pending_text, ""
        if pending and not self._stopped:
            self.start_turn(pending)

    def _maybe_fire_stop(self, run_id: str) -> None:
        """记录 run_id；若用户已按过取消，在这里补发。

        run_id 只在 run.started 帧里出现一次。用户完全可能在那一帧到达前
        就按了热键 —— 那时 cancel() 拿不到 run_id，只能置标志，靠这里补。
        少这一步，早按的取消会被静默丢掉（表现为「按了没用，它还在答」）。

        「查闩 → 置 _stop_fired → 发 /stop」必须在同一把锁里：cancel() 可能正在
        主线程上走同一条路，不锁就会对同一个 run 打出两次 /stop。
        """
        if not run_id:
            return
        with self._stop_lock:
            self._run_id = run_id
            if not self._cancel_requested or self._stop_fired:
                return
            self._stop_fired = True      # 先置位再交接：每轮至多一次
        self._spawn_stop(run_id)

    def cancel(self) -> None:
        """取消当前轮。线程安全、**不阻塞**，可从主线程（热键）调用。

        必须打服务端的 /stop：Hermes 的 agent 会在服务器上执行工具，
        「断开 HTTP 连接」不等于服务端停止运行（设计文档 §3.4 实测）。

        /stop 的 HTTP 交给短命线程去发：本方法跑在 Qt 主线程（热键）上，遇到
        黑洞端点会白等满 _connect_timeout_s（默认 5s），界面直接冻住 —— 全局
        约束要求所有 HTTP 调用都在 worker 线程里。_stop_fired 在交接前就置位，
        所以每轮仍至多一次。
        """
        with self._stop_lock:
            self._cancel_requested = True
            run_id = self._run_id
            if not run_id or self._stop_fired:
                # run.started 还没到：/stop 靠读循环走到 _maybe_fire_stop 补发。
                # 这是"按了取消但服务端还在跑"的一种正常情形，留痕便于排查
                logger.info("Hermes 取消：本轮到 /stop 尚未派发（run_id=%s，"
                            "stop_fired=%s）", run_id or "(空)", self._stop_fired)
                return
            self._stop_fired = True
        logger.info("Hermes 取消：已派发 /stop（run=%s）", run_id)
        # run.started 已到、/stop 已交接：顺手结束本地读循环，不再等那 1~2 秒的
        # 服务端尾巴（真机实测 /stop 到 ai_turn_end 要 1.14~4.11 秒）。这 1~2 秒里
        # 用户重说会撞上「上一轮尚未结束」，正是 W1 要消掉的那句丢失。
        # ⚠️ _run_id 为空时**绝不能**关流：那条路径要靠读循环走到 _maybe_fire_stop
        # 才把 /stop 补发出去（用户在 run.started 之前按的取消），关了就再也发不出。
        self._close_stream()
        self._spawn_stop(run_id)

    def _close_stream(self) -> None:
        """best-effort 关掉在途 SSE 响应：唤醒阻塞在 read 上的读线程。

        幂等、绝不抛：关流是收束手段，本身失败不该盖过调用方要报的那件事。
        """
        resp = self._stream_resp
        if resp is None:
            return
        try:
            resp.close()
        except Exception:
            logger.debug("关闭 Hermes 流失败", exc_info=True)

    def _spawn_stop(self, run_id: str) -> None:
        """把 /stop 交给短命 daemon 线程（daemon：绝不拖着进程不退）。"""
        threading.Thread(target=self._post_stop, args=(run_id,),
                         daemon=True, name="hermes-stop").start()

    def _post_stop(self, run_id: str) -> None:
        try:
            with self._open(f"/v1/runs/{run_id}/stop", method="POST",
                            timeout_s=self._connect_timeout_s) as resp:
                resp.read()
        except Exception as exc:
            logger.warning("Hermes 取消失败 run=%s：%s", run_id, exc)
            # 不可静默：服务器上可能还在跑工具
            self._notify_error(DialogErrorEvent(
                CODE_CANCEL_FAILED, f"取消失败：{exc}",
                "取消失败，Hermes 可能仍在执行"))

    def stop(self) -> None:
        """收场：停掉在途轮次**及其回调**，再置断开态。

        「返回即已停」是控制器的契约（core/dialog.py:_begin_stop 之后会拆播放器/
        采集，并把同一批回调接到下一个会话）：只要 worker 还在读流，它就会继续
        _notify_event —— 上一轮的正文会追加进已收场的会话，甚至喂给新会话。
        所以这里要：挂牌 → 关流（唤醒阻塞在 read 上的读线程）→ join 等它退。
        join 超时只记警告：线程是 daemon，且不该让收尾卡住。

        _stopped 是终态：本实例收场后不再产出任何事件（控制器每次会话都新建
        实例，见 core/dialog.py:_create_client），不要拿它再 start_turn。
        再调 start() 也一样：服务端会真的建出会话，但 SESSION_READY 被终态吃掉
        ——「连上了却什么都不发生」。要再次对话请新建实例，别复用本对象。
        """
        self._stopped = True             # 先挂牌：此后不会再有任何事件回调
        self.cancel()                    # 非阻塞：/stop 由短命线程发
        self._close_stream()
        thread = self._turn_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_STOP_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning(
                    "hermes-turn 线程未在 %.0fs 内退出（daemon，由其自行收尾）",
                    _STOP_JOIN_TIMEOUT_S)
        self._set_state(STATE_DISCONNECTED)
