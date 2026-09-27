"""讯飞实时语音转写封装（大模型版 rtasr_llm + 标准版 rtasr）。

接口与 tencent_realtime.TencentRealtimeASR 完全一致，便于在 VoiceApp 中互换：
- start() 建立 WebSocket 连接（握手失败抛 ASRError）
- send_audio(block) 推送音频块（numpy int16 数组，以 binary message 发送）
- stop() 发送 end 信号并等待最终结果后关闭连接
- is_connected() 连接是否可用
- 回调：on_result(text, slice_type, index) / on_error(code, message) / on_state(state)

大模型版 rtasr_llm（XfyunRealtimeASR）：
- URL: wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1?{params}
- 鉴权：除 signature 外所有参数按 key 升序，键值分别 URL 编码后拼接为
  "k=v&k=v"，对该串做 HmacSHA1(APISecret) 再 Base64 得 signature
- 密钥：AppID / APIKey(accessKeyId) / APISecret(accessKeySecret)
- 消息路由：action / msg_type 两字段文档并存，取任一：
  started=握手确认，result=转写结果，error=异常；
  res_type=frc 或 data.normal=false 表示转写功能异常
- 结果树：data.cn.st.rt[].ws[].cw[0].w 逐词拼接为当前句文本；
  st.type 为字符串："1"=中间结果 -> SLICE_INTERMEDIATE，"0"=确定结果 -> SLICE_FINAL；
  data.ls=true 为最后一帧，服务端随后结束推送
- 结束：发送 {"end": true, "sessionId": "..."}（text message）
- 注意：code 字段为字符串（"0" 表示成功），不能与整数直接比较

标准版 rtasr（XfyunStdASR）：
- URL: wss://rtasr.xfyun.cn/v1/ws?{appid&ts&signa&lang[&pd]}
- 鉴权：signa = Base64(HmacSHA1(MD5(appid + ts), APIKey))，无需 APISecret
- 结果：action=result 时 data 为 JSON 字符串，需二次解析，
  内部结构与大模型版相同（cn.st.rt.ws.cw）；无 ls 最终帧标记，
  发送 end 后服务端推完剩余结果主动断连
- 翻译消息（data 内 biz=trans）忽略
- 结束：发送 {"end": true}（binary message）
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np
import websockets

from .tencent_realtime import (
    ASRError,
    SLICE_INTERMEDIATE,
    SLICE_FINAL,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_DISCONNECTED,
    STATE_ERROR,
)

logger = logging.getLogger(__name__)

ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[int, str], None]
StateCallback = Callable[[str], None]


class XfyunRealtimeASR:
    """讯飞实时语音转写大模型（rtasr_llm）客户端。

    在独立线程运行 asyncio 事件循环，对外提供同步接口；
    回调在 asyncio 线程触发，UI 层需经信号槽切回主线程。
    """

    WS_HOST = "office-api-ast-dx.iflyaisol.com"
    WS_PATH = "/ast/communicate/v1"
    SILENCE_CHUNK = b"\x00" * 1280  # 40ms@16kHz16bit 静音帧（保活用）
    KEEPALIVE_INTERVAL = 3.0  # 静音保活间隔（服务端 15s 无数据断连）

    # 常见错误码的人类可读提示（完整错误码表见官方文档）
    _ERROR_MESSAGES = {
        35001: "鉴权失败（AppID/APIKey/APISecret 不匹配或服务未开通）",
        35002: "服务用量不足",
        35030: "签名过期或重复（常见于本机时间偏差过大，请校准系统时间）",
        37005: "连接后未发送音频超时",
        37007: "单次转写音频时长达上限（8 小时）",
        37010: "发送 end 后继续发送数据",
        100001: "上传音频速度超出限制",
        100012: "UTC 时间偏差过大，请校准系统时间",
    }

    def __init__(
        self,
        app_id: str,
        api_key: str,
        api_secret: str,
        lang: str = "autodialect",
        pd: str = "",
        filter_modal: bool = False,
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.lang = lang  # 大模型版：autodialect=中英+202方言（默认）| autominor=37语种（需工单开通）
        self.pd = pd  # 仅标准版（rtasr v1）支持垂直领域参数；大模型版无此参数，不发送
        self.filter_modal = filter_modal  # True 时丢弃 wp=s 顺滑语气词

        self.on_result = on_result
        self.on_error = on_error
        self.on_state = on_state

        self._state = STATE_DISCONNECTED
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session_id = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    # ---- 状态 ----

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    # ---- 鉴权 URL（大模型版：参数升序 + HmacSHA1(APISecret)）----

    def _build_request(self) -> str:
        """构建带签名的 WebSocket URL。

        签名串 = 参数按 key 升序，键值分别 URL 编码后 "k=v&..." 拼接；
        signature = Base64(HmacSHA1(APISecret, 签名串))，放入 URL 前再编码一次。
        """
        utc = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        params = {
            "appId": self.app_id,
            "accessKeyId": self.api_key,
            "uuid": uuid.uuid4().hex,
            "utc": utc,
            "lang": self.lang,
            "audio_encode": "pcm_s16le",
            "samplerate": "16000",
        }
        # 注意：大模型版无 pd 参数（垂直领域仅标准版支持），不发送

        encoded = sorted(
            (
                urllib.parse.quote(str(k), safe=""),
                urllib.parse.quote(str(v), safe=""),
            )
            for k, v in params.items()
        )
        query = "&".join(f"{k}={v}" for k, v in encoded)

        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        signature_encoded = urllib.parse.quote(signature, safe="")

        return (
            f"wss://{self.WS_HOST}{self.WS_PATH}"
            f"?{query}&signature={signature_encoded}"
        )

    # ---- 协议钩子（标准版子类覆写）----

    def _is_error_message(self, data: dict) -> bool:
        """转写功能异常消息（文档示例：res_type=frc / data.normal=false）。"""
        if str(data.get("res_type") or "") == "frc":
            return True
        payload = data.get("data")
        return isinstance(payload, dict) and payload.get("normal") is False

    def _extract_payload(self, data: dict) -> Optional[dict]:
        """从 result 消息中取转写 payload（大模型版：data 为 dict）。"""
        payload = data.get("data")
        return payload if isinstance(payload, dict) else None

    def _is_final_frame(self, payload: dict) -> bool:
        """是否为整个转写的最后一帧（大模型版：data.ls=true）。"""
        return payload.get("ls") is True

    async def _send_end(self) -> None:
        """发送结束标识（大模型版：text message，带 sessionId）。"""
        await self._ws.send(
            json.dumps({"end": True, "sessionId": self._session_id})
        )

    # ---- 回调通知 ----

    def _set_state(self, state: str) -> None:
        self._state = state
        logger.info("ASR 状态：%s", state)
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                logger.exception("on_state 回调异常")

    def _notify_result(self, text: str, slice_type: int, index: int) -> None:
        logger.debug("识别结果 slice_type=%s index=%s text=%s", slice_type, index, text)
        if self.on_result:
            try:
                self.on_result(text, slice_type, index)
            except Exception:
                logger.exception("on_result 回调异常")

    @staticmethod
    def _code_int(raw) -> int:
        """code/seg_id 等字段兼容 int 与数字字符串，其余归为 -1。"""
        s = str(raw) if raw is not None else ""
        return int(s) if s.isdigit() else -1

    def _notify_error(self, code, message: str) -> None:
        code_int = self._code_int(code)
        hint = self._ERROR_MESSAGES.get(code_int)
        full = f"{message}（{hint}）" if hint else message
        logger.error("ASR 错误 code=%s message=%s", code_int, full)
        if self.on_error:
            try:
                self.on_error(code_int, full)
            except Exception:
                logger.exception("on_error 回调异常")

    # ---- 消息解析 ----

    @staticmethod
    def _message_action(data: dict) -> str:
        """action / msg_type 文档两处写法并存，统一取一。"""
        return str(data.get("action") or data.get("msg_type") or "")

    def _verify_handshake(self, data: dict) -> None:
        """校验握手确认消息（started）。失败抛 ASRError。

        code 为字符串 "0"，不能与整数比较（曾因此导致启动必失败）。
        """
        code_raw = str(data.get("code") or "")
        if self._message_action(data) == "error" or (code_raw and code_raw != "0"):
            desc = data.get("desc") or data.get("message") or ""
            raise ASRError(
                f"握手失败：{desc}", code=self._code_int(code_raw)
            )
        self._session_id = (
            str(data.get("sid") or data.get("sessionId") or "")
            or uuid.uuid4().hex
        )

    def _handle_message(self, data: dict) -> bool:
        """处理一条服务端消息，返回 True 表示接收循环应结束。"""
        code_raw = str(data.get("code") or "")
        action = self._message_action(data)
        if action == "error" or (code_raw and code_raw != "0"):
            self._notify_error(
                code_raw,
                data.get("desc") or data.get("message") or "服务端返回错误",
            )
            return True

        if self._is_error_message(data):
            payload = data.get("data")
            desc = payload.get("desc", "") if isinstance(payload, dict) else ""
            self._notify_error(-1, desc or "服务端转写功能异常")
            return True

        if action and action != "result":
            return False  # started 等非结果消息

        payload = self._extract_payload(data)
        if not isinstance(payload, dict):
            return False

        st = (payload.get("cn") or {}).get("st") or {}
        if st:
            text = self._extract_text(st)
            seg_id = self._code_int(payload.get("seg_id", 0))
            slice_type = (
                SLICE_INTERMEDIATE if str(st.get("type")) == "1" else SLICE_FINAL
            )
            self._notify_result(text, slice_type, seg_id)

        if self._is_final_frame(payload):
            logger.info("识别全部结束（最终帧）")
            return True
        return False

    def _extract_text(self, st: dict) -> str:
        """拼接 data.cn.st.rt[].ws[].cw[0].w 为整句文本。

        cw 为候选词数组，取第一个（最优候选）；
        wp 词标识在 cw 元素内（n 普通 / s 顺滑语气词 / p 标点 / g 分段），
        filter_modal 时丢弃 wp="s" 的顺滑语气词（嗯/啊等）。
        """
        parts: list[str] = []
        for rt in st.get("rt") or []:
            for ws in rt.get("ws") or []:
                cw_list = ws.get("cw") or []
                if not cw_list:
                    continue
                best = cw_list[0] or {}
                if self.filter_modal and str(best.get("wp")) == "s":
                    continue
                parts.append(str(best.get("w", "")))
        return "".join(parts)

    # ---- asyncio 会话 ----

    # 讯飞网关用非标准 HTTP 状态码返回错误时的常见提示
    _HTTP_STATUS_HINTS = {
        "35001": "账号鉴权失败，请核对 AppID / APIKey / APISecret 是否为该服务的密钥",
        "35002": "用量不足，请到控制台领取免费额度或购买套餐",
        "35004": "appId 不存在，请核对 AppID",
        "35010": "accessKeyId 不存在：APIKey 与服务不匹配，请核对密钥来源",
        "35014": "本机时间偏差过大，请校准系统时间",
        "35020": "语种不受支持：autominor 需工单开通；"
        "请右键引擎菜单重选「实时转写大模型」（autodialect 中英+方言）",
        "35030": "签名过期或重复：通常是本机时间偏差过大，请校准系统时间"
        "（设置 → 时间和语言 → 日期和时间 → 立即同步）",
        "100012": "本机时间偏差过大，请校准系统时间",
    }

    def _raw_handshake_probe(self, url: str) -> str:
        """用原始 TLS 请求复现握手，取回服务端真实错误。

        讯飞网关对无效请求返回非标准 HTTP 状态码（如 "HTTP/1.1 35010 ..."），
        websockets 库解析不了这种状态行，只报 InvalidMessage 把真实原因吞掉。
        """
        try:
            parsed = urllib.parse.urlsplit(url)
            ctx = ssl.create_default_context()
            with socket.create_connection((parsed.hostname, 443), timeout=6) as sock:
                with ctx.wrap_socket(sock, server_hostname=parsed.hostname) as tls:
                    key = base64.b64encode(os.urandom(16)).decode()
                    req = (
                        f"GET {parsed.path}?{parsed.query} HTTP/1.1\r\n"
                        f"Host: {parsed.hostname}\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        "\r\n"
                    )
                    tls.sendall(req.encode())
                    data = tls.recv(2048).decode("utf-8", "replace")
            head = data.split("\r\n\r\n", 1)[0]
            lines = [ln for ln in head.splitlines() if ln.strip()]
            body = data.split("\r\n\r\n", 1)[-1].strip()
            status = lines[0] if lines else ""
            detail = f"{status} | {body}" if body else status
            for code, hint in self._HTTP_STATUS_HINTS.items():
                if code in detail:
                    detail = f"{detail}（{hint}）"
                    break
            return detail
        except Exception as probe_exc:
            logger.debug("握手错误探测失败：%s", probe_exc)
            return ""

    async def _connect(self) -> None:
        url = self._build_request()
        logger.info(
            "建立讯飞 ASR 连接 engine=%s lang=%s pd=%s",
            type(self).__name__, self.lang, self.pd or "-",
        )
        self._set_state(STATE_CONNECTING)

        try:
            # close_timeout=1：讯飞标准版服务端收到 end 后不回 close 握手帧，
            # 默认 10 秒的关闭等待会拖垮整个 stop 流程（曾致"关闭超时"）
            self._ws = await websockets.connect(url, max_size=None, close_timeout=1)
        except Exception as exc:
            self._set_state(STATE_ERROR)
            detail = ""
            if "valid HTTP response" in str(exc):
                try:
                    detail = await asyncio.to_thread(self._raw_handshake_probe, url)
                except Exception:
                    detail = ""
            raise ASRError(f"WebSocket 连接失败：{detail or exc}") from exc

        try:
            handshake = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(handshake)
        except asyncio.TimeoutError:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError("握手超时，未收到服务端确认")
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"握手响应解析失败：{exc}") from exc

        try:
            self._verify_handshake(data)
        except ASRError:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise

        self._set_state(STATE_CONNECTED)
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _send_loop(self) -> None:
        try:
            last_send = time.monotonic()
            while not self._stop_event.is_set():
                try:
                    audio = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # 用户长时间停顿（无真实音频）：周期性喂静音帧保活，
                    # 避免服务端因发送间隔超时断连；正常说话时不插入
                    if time.monotonic() - last_send >= self.KEEPALIVE_INTERVAL:
                        await self._ws.send(self.SILENCE_CHUNK)
                        last_send = time.monotonic()
                    continue
                if audio is None:
                    break
                await self._ws.send(audio)
                last_send = time.monotonic()
        except websockets.ConnectionClosed:
            logger.info("发送循环：连接已关闭")
        except Exception:
            logger.exception("发送循环异常")

    async def _recv_loop(self) -> None:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    if self._stop_event.is_set():
                        break
                    continue
                except websockets.ConnectionClosed:
                    logger.info("接收循环：连接已关闭")
                    break

                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    logger.warning("无法解析消息：%s", str(msg)[:200])
                    continue

                if self._handle_message(data):
                    break
        except Exception:
            logger.exception("接收循环异常")
        finally:
            self._set_state(STATE_DISCONNECTED)

    async def _shutdown(self) -> None:
        """发送 end 信号，等接收循环收完剩余结果后关闭连接。

        不直接调 ws.recv()（会和 _recv_loop 冲突导致 ConcurrencyError），
        而是等 _recv_task 自然结束（大模型版收到 ls=true 后 break；
        标准版由服务端推完结果主动断连触发）。
        """
        # 1. 发送结束标识（大模型版带 sessionId；标准版为 binary 帧）
        if self._ws is not None:
            try:
                await self._send_end()
                logger.info("已发送 end 信号")
            except websockets.ConnectionClosed:
                logger.info("发送 end 时连接已断开")
            except Exception:
                logger.exception("发送 end 失败")

        # 2. 等待接收循环自然结束，最多 3 秒
        if self._recv_task and not self._recv_task.done():
            try:
                await asyncio.wait_for(self._recv_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.info("等待最终结果超时，取消接收循环")
                self._recv_task.cancel()
                # 等取消真正落地，否则任务挂在事件循环上，循环停止后
                # 报 "Task was destroyed but it is pending!"
                try:
                    await self._recv_task
                except asyncio.CancelledError:
                    pass  # 取消是正常控制流，保持静默
                except Exception:
                    logger.debug("等待已取消接收循环异常，忽略", exc_info=True)
            except Exception:
                logger.debug("等待最终结果异常，忽略", exc_info=True)

        # 3. 停止发送循环（同样等取消落地）
        if self._stop_event:
            self._stop_event.set()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # 4. 关闭连接（状态由 _recv_loop 的 finally 统一设置，不重复）。
        # 限时 1 秒：部分服务端不回 close 握手帧，干等只会拖垮 stop() 的
        # 5 秒总预算（3s 收尾 + 1s 关闭 = 4s < 5s）
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.info("WebSocket 关闭握手超时，放弃等待")
            except Exception:
                pass

    # ---- 同步接口 ----

    def start(self) -> None:
        """建立连接（在新线程的 asyncio loop 中执行）。握手失败抛出 ASRError。

        "over max connect limit"（并发连接数超限）为瞬态错误：服务端释放
        上一个连接有数秒延迟，立即重连会被拒。自动退避重试。
        """
        attempt = 0
        while True:
            try:
                self._start_once()
                return
            except ASRError as exc:
                attempt += 1
                if "max connect limit" in str(exc) and attempt <= 3:
                    wait = 1.5 * attempt
                    logger.info(
                        "讯飞并发连接数超限，%.1f 秒后重试（第 %d 次）",
                        wait, attempt,
                    )
                    time.sleep(wait)
                    continue
                raise

    def _start_once(self) -> None:
        if self._thread is not None:
            raise ASRError("ASR 已启动，请先 stop")

        self._loop = asyncio.new_event_loop()
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()

        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True, name="asr-loop")
        self._thread.start()

        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            future.result(timeout=15)
        except ASRError:
            self._cleanup_loop()
            raise
        except Exception as exc:
            self._cleanup_loop()
            # 带上异常类型名：CancelledError/TimeoutError 等的 str 为空，
            # 只拼 str 会得到 "启动失败：" 空消息，无从排查
            raise ASRError(f"启动失败：{type(exc).__name__}: {exc}") from exc

    def _cleanup_loop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._loop = None
        self._audio_queue = None
        self._stop_event = None

    def send_audio(self, block: np.ndarray) -> None:
        """推送音频块。block 为 int16 numpy 数组。"""
        if not self.is_connected() or self._audio_queue is None or self._loop is None:
            return
        data = np.ascontiguousarray(block.reshape(-1)).tobytes()
        try:
            asyncio.run_coroutine_threadsafe(self._audio_queue.put(data), self._loop)
        except Exception:
            # 停机会话瞬间音频回调仍可能调用本方法：检查通过后循环恰好
            # 停止/置空，抛 RuntimeError/AttributeError 等。捕获一切异常
            # 防止它打进 PortAudio 回调线程导致进程崩溃
            logger.warning("事件循环不可用，丢弃音频块")

    def stop(self) -> None:
        """发送结束信号并关闭连接。"""
        if self._loop is None or self._stop_event is None:
            return
        # 不在这里 set _stop_event，让 _shutdown 统一控制时序
        if self._audio_queue and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._audio_queue.put(None), self._loop)
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                logger.exception("关闭超时")
        self._cleanup_loop()


class XfyunStdASR(XfyunRealtimeASR):
    """讯飞实时语音转写标准版（rtasr v1/ws）客户端。

    使用标准版独立密钥（console.xfyun.cn/services/rtasr 领取，与大模型版互不通用）；
    signa = Base64(HmacSHA1(MD5(appid + ts), APIKey))。
    结果消息的 data 为 JSON 字符串需二次解析；标准版无 ls 最终帧标记，
    发送 {"end": true}（binary）后服务端推完剩余结果主动断连。
    """

    WS_HOST = "rtasr.xfyun.cn"
    WS_PATH = "/v1/ws"

    _ERROR_MESSAGES = {
        10105: "没有权限（检查 APIKey / IP 白名单 / 系统时间）",
        10106: "无效参数",
        10107: "非法参数值",
        10110: "无授权许可（服务未开通或授权路数已满）",
        10202: "WebSocket 连接错误",
        10700: "引擎错误",
        10800: "超过授权的连接数",
        37005: "超过 15 秒未发送音频",
    }

    def __init__(
        self,
        app_id: str,
        api_key: str,
        lang: str = "cn",
        pd: str = "",
        punc: bool = False,
        filter_modal: bool = False,
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        super().__init__(
            app_id,
            api_key,
            api_secret="",
            lang=lang,  # 标准版：cn=中文/中英混合 | en=英文
            pd=pd,
            filter_modal=filter_modal,
            on_result=on_result,
            on_error=on_error,
            on_state=on_state,
        )
        self.punc = punc  # 标准版专用：True 时传 punc=0 过滤标点（大模型版无此参数）

    def _build_request(self) -> str:
        """构建带 signa 的 WebSocket URL（appid + ts 秒级时间戳）。"""
        ts = str(int(time.time()))
        md5_hex = hashlib.md5(
            f"{self.app_id}{ts}".encode("utf-8")
        ).hexdigest()
        signa = base64.b64encode(
            hmac.new(
                self.api_key.encode("utf-8"),
                md5_hex.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        params = {
            "appid": self.app_id,
            "ts": ts,
            "signa": signa,
            "lang": self.lang or "cn",
        }
        if self.pd:
            params["pd"] = self.pd
        if self.punc:
            params["punc"] = "0"  # 过滤结果中的标点
        query = "&".join(
            f"{urllib.parse.quote(str(k), safe='')}="
            f"{urllib.parse.quote(str(v), safe='')}"
            for k, v in params.items()
        )
        return f"wss://{self.WS_HOST}{self.WS_PATH}?{query}"

    def _is_error_message(self, data: dict) -> bool:
        return False  # 标准版异常统一走 action=error

    def _extract_payload(self, data: dict) -> Optional[dict]:
        """标准版 data 为 JSON 字符串，二次解析；翻译消息（biz=trans）忽略。"""
        raw = data.get("data")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("标准版结果 JSON 解析失败：%s", raw[:200])
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("biz") == "trans":
            return None
        return payload

    def _is_final_frame(self, payload: dict) -> bool:
        return False  # 标准版无最终帧标记，靠服务端断连结束

    async def _send_end(self) -> None:
        # 标准版结束标识要求为 binary message
        await self._ws.send(json.dumps({"end": True}).encode("utf-8"))
