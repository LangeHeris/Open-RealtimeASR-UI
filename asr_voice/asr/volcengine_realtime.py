"""火山引擎豆包大模型流式语音识别（sauc bigmodel）封装。

接口与 tencent_realtime.TencentRealtimeASR 完全一致，便于在 VoiceApp 中互换：
- start() 建立 WebSocket 连接（握手失败抛 ASRError）
- send_audio(block) 推送音频块（numpy int16 数组，以 audio only 包发送）
- stop() 发送负包（结束信号）并等待最终结果后关闭连接
- is_connected() 连接是否可用
- 回调：on_result(text, slice_type, index) / on_error(code, message) / on_state(state)

协议要点（docs.volcengine.com/docs/6561/2630027，2026-08 版）：
- 接口：wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
  （双向流式优化版：结果有变化才返回，首字/尾字时延更优，官方推荐）
- 鉴权（WebSocket 握手 HTTP 头，二选一）：
  新版控制台单密钥：X-Api-Key（API Key）+ X-Api-Resource-Id
    + X-Api-Request-Id（UUID）+ X-Api-Sequence: -1
  旧版控制台双密钥：X-Api-App-Key（App ID）+ X-Api-Access-Key（Access Token）
    + X-Api-Resource-Id + X-Api-Connect-Id（UUID）
  判定：access_token 与 app_id 均非空 -> 旧版；否则用 api_key -> 新版
- payload：audio + request（model_name 目前仅支持 "bigmodel"，
  2.0 资源 volc.seedasr.sauc.* 同样填 bigmodel）
- 二进制协议：4 字节头 + payload size（4B 大端）+ payload
  - 头：version=1 | header_size=1(即 4 字节) | 消息类型 | flags | 序列化 | 压缩 | 保留
  - 消息类型：0x1 full client request / 0x2 audio only / 0x9 full server
    response / 0xF 服务端错误
  - audio only 包：flags=0x1（payload 前 4 字节为大端 sequence，正数自增）；
    负包（结束）：flags=0x3，sequence=-1
  - full client request：JSON + Gzip 压缩；服务器按相同序列化/压缩方式返回
- 结果：result.utterances[]（需 show_utterances=true），每句含 definite 标志；
  开启二遍识别（enable_nonstream，仅 async 端点支持）后静音 800ms 判停，
  该句 definite=true 即二遍（非流式重识别）高准确率 final
- 建连后需持续发包，静音期由发送循环周期性喂静音帧保活
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct
import threading
import time
import uuid
from typing import Callable, Optional

import numpy as np
import websockets

from .tencent_realtime import (
    ASRError,
    SLICE_FINAL,
    SLICE_INTERMEDIATE,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_DISCONNECTED,
    STATE_ERROR,
)

logger = logging.getLogger(__name__)

ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[int, str], None]
StateCallback = Callable[[str], None]

# 二进制协议常量（见模块 docstring）
PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001  # 实际 4 字节

MSG_FULL_CLIENT_REQUEST = 0b0001
MSG_AUDIO_ONLY_REQUEST = 0b0010
MSG_FULL_SERVER_RESPONSE = 0b1001
MSG_ERROR_RESPONSE = 0b1111

FLAG_NONE = 0b0000
FLAG_POS_SEQUENCE = 0b0001
FLAG_NEG_SEQUENCE = 0b0011  # 最后一包（负包），payload 前 4 字节为负 sequence

SERIAL_NONE = 0b0000
SERIAL_JSON = 0b0001
COMPRESS_NONE = 0b0000
COMPRESS_GZIP = 0b0001


def _build_header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    """构造 4 字节协议头。"""
    byte0 = (PROTOCOL_VERSION << 4) | HEADER_SIZE
    byte1 = (message_type << 4) | flags
    byte2 = (serialization << 4) | compression
    return bytes([byte0, byte1, byte2, 0x00])


def build_full_client_request(payload: dict) -> bytes:
    """构造首包（参数 JSON，Gzip 压缩）。"""
    raw = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(raw)
    header = _build_header(
        MSG_FULL_CLIENT_REQUEST, FLAG_NONE, SERIAL_JSON, COMPRESS_GZIP
    )
    return header + struct.pack(">I", len(compressed)) + compressed


def build_audio_request(audio: bytes, sequence: int) -> bytes:
    """构造音频包（新协议：Header + Sequence(4B) + Size(4B) + 音频字节）。

    注意：sequence 位于 Size 之前（2026-08 文档 2630027 版协议，
    与响应包布局对称；旧文档 1354869 的"序号嵌在 payload 开头"已过时）。
    """
    header = _build_header(
        MSG_AUDIO_ONLY_REQUEST, FLAG_POS_SEQUENCE, SERIAL_NONE, COMPRESS_NONE
    )
    return (
        header
        + struct.pack(">i", sequence)
        + struct.pack(">I", len(audio))
        + audio
    )


def build_last_request(sequence: int) -> bytes:
    """构造负包（结束信号，sequence 为负数，无音频数据）。"""
    header = _build_header(
        MSG_AUDIO_ONLY_REQUEST, FLAG_NEG_SEQUENCE, SERIAL_NONE, COMPRESS_NONE
    )
    return header + struct.pack(">i", sequence) + struct.pack(">I", 0)


def parse_response(message: bytes) -> tuple[int, dict]:
    """解析服务端二进制响应（官方布局：Header + [Sequence] + Size + Payload）。

    注意：响应包的 sequence 位于 Size 之前（与请求包的"sequence 嵌在 payload
    开头"不同）；错误包（0xF）此处为 4 字节错误码。
    返回 (sequence, payload_dict)。payload_dict 在无法解析为 JSON 时为 {}。
    sequence 为 -1 表示负包响应（识别全部结束）。
    """
    if len(message) < 8:
        logger.warning("响应过短：%s", message.hex())
        return -1, {}

    message_type = message[1] >> 4
    flags = message[1] & 0x0F
    serialization = message[2] >> 4
    compression = message[2] & 0x0F

    if message_type == MSG_ERROR_RESPONSE:
        # Header + 错误码(4B) + Size(4B) + Payload
        code = struct.unpack(">i", message[4:8])[0]
        size = struct.unpack(">I", message[8:12])[0] if len(message) >= 12 else 0
        data = message[12 : 12 + size]
        if compression == COMPRESS_GZIP and data:
            try:
                data = gzip.decompress(data)
            except OSError:
                logger.warning("错误包解压失败，按原文处理")
        msg_text = ""
        try:
            err_json = json.loads(data) if data else {}
            msg_text = str(
                err_json.get("message") or err_json.get("error") or ""
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            msg_text = data.decode("utf-8", "replace")
        return -1, {"message_type": message_type, "error_code": code,
                    "message": msg_text}

    # 正常响应：Header + [Sequence(4B)] + Size(4B) + Payload
    offset = 4
    sequence = -1
    if flags & 0b0001:
        sequence = struct.unpack(">i", message[4:8])[0]
        offset = 8
    if len(message) < offset + 4:
        logger.warning("响应缺少 Size 字段：%s", message.hex())
        return -1, {}
    payload_size = struct.unpack(">I", message[offset : offset + 4])[0]
    offset += 4
    data = message[offset : offset + payload_size]

    if compression == COMPRESS_GZIP and data:
        try:
            data = gzip.decompress(data)
        except OSError:
            logger.warning("响应解压失败，按原文处理")

    result: dict = {}
    if serialization == SERIAL_JSON and data:
        try:
            result = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("响应 JSON 解析失败：%s", data[:200])
    return sequence, {"message_type": message_type, **result}


class VolcengineRealtimeASR:
    """火山引擎豆包大模型流式语音识别客户端。

    在独立线程运行 asyncio 事件循环，对外提供同步接口；
    回调在 asyncio 线程触发，UI 层需经信号槽切回主线程。
    """

    # 停止后服务端会回吐尾句 final（实测：stop 发负包后约 1s 内收到最后一句
    # 的 FINAL，见 2026-09-09 日志「丢弃结果帧」）——按腾讯 preview 同语义
    # 放行「停止即上屏」。不声明的话，逐字同步下正常停止会先退格擦掉已上屏
    # 文字、又把来补的 final 丢弃，整句凭空消失
    flush_tail_on_stop = True

    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    SILENCE_CHUNK = b"\x00" * 3200  # 100ms@16kHz16bit 静音帧（保活用）
    KEEPALIVE_INTERVAL = 3.0

    def __init__(
        self,
        api_key: str = "",
        resource_id: str = "volc.bigasr.sauc.duration",
        app_id: str = "",
        access_token: str = "",
        enable_ddc: bool = False,
        enable_nonstream: bool = True,
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        self.api_key = api_key              # 新版单密钥（X-Api-Key）
        self.app_id = app_id                # 旧版 App ID（X-Api-App-Key）
        self.access_token = access_token    # 旧版 Access Token（X-Api-Access-Key）
        self.resource_id = resource_id
        self.enable_ddc = enable_ddc  # 语义顺滑（去语气词/重复词）
        self.enable_nonstream = enable_nonstream  # 二遍识别（final 更准）

        self.on_result = on_result
        self.on_error = on_error
        self.on_state = on_state

        self._state = STATE_DISCONNECTED
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._sequence = 1  # 首个音频包序号为 2（首包 full request 占用 1）
        self._confirmed_count = 0  # 已回调 definite 句的数量（去重）
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

    # ---- 鉴权头 ----

    def _build_headers(self) -> dict:
        """按 2026-08 官方文档（docs/6561/2630027）构造握手头。

        新版控制台鉴权：X-Api-Key + X-Api-Resource-Id
                        + X-Api-Request-Id（UUID）+ X-Api-Sequence: -1
        旧版控制台鉴权（docs/6561/2534847）：
                        X-Api-App-Key + X-Api-Access-Key + X-Api-Connect-Id

        判定：access_token 与 app_id 均非空 -> 旧版；否则用 api_key -> 新版。
        """
        if self.access_token and self.app_id:
            # 旧版双密钥：App ID + Access Token
            return {
                "X-Api-App-Key": self.app_id,
                "X-Api-Access-Key": self.access_token,
                "X-Api-Resource-Id": self.resource_id,
                "X-Api-Connect-Id": str(uuid.uuid4()),
            }
        # 新版单密钥：API Key（控制台"API Key 管理"生成）
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }

    def _build_request_payload(self) -> dict:
        # 按官方 demo（2630027）：payload 仅 audio + request；
        # model_name 目前仅支持 "bigmodel"（2.0 seedasr 资源同样填 bigmodel）
        return {
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,   # 数字/货币规范化（"一百二十三"->"123"）
                "enable_punc": True,  # 标点
                "enable_ddc": self.enable_ddc,
                "enable_nonstream": self.enable_nonstream,
                "result_type": "full",       # 全量返回（含此前分句）
                "show_utterances": True,     # 需要分句 definite 标志
            },
        }

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
        logger.debug(
            "识别结果 slice_type=%s index=%s text=%s", slice_type, index, text
        )
        if self.on_result:
            try:
                self.on_result(text, slice_type, index)
            except Exception:
                logger.exception("on_result 回调异常")

    def _notify_error(self, code: int, message: str) -> None:
        logger.error("ASR 错误 code=%s message=%s", code, message)
        if self.on_error:
            try:
                self.on_error(code, message)
            except Exception:
                logger.exception("on_error 回调异常")

    # ---- 结果解析 ----

    def _handle_payload(self, payload: dict) -> None:
        """处理 full server response：definite 句回调 FINAL，未定句回调 INTERMEDIATE。

        全量模式下 utterances 累积包含此前分句，用 _confirmed_count 去重，
        只对新增 definite 句回调 FINAL；最后一个未定句作为当前句中间结果。
        """
        message_type = payload.get("message_type")
        if message_type == MSG_ERROR_RESPONSE:
            code = payload.get("error_code",
                               payload.get("code", -1))
            message = payload.get("message",
                                  payload.get("error_message", "服务端错误"))
            self._notify_error(code, str(message))
            return

        # 新版响应在 JSON 顶层携带 code（0=成功），非 0 为会话内错误
        code = payload.get("code")
        if code not in (None, 0):
            self._notify_error(code, str(payload.get("message", "服务端错误")))
            return

        result = payload.get("result") or {}
        utterances = result.get("utterances") or []
        if not utterances:
            return

        for idx, utt in enumerate(utterances):
            if idx >= self._confirmed_count and utt.get("definite"):
                text = str(utt.get("text", "")).strip()
                if text:
                    self._notify_result(text, SLICE_FINAL, idx)
                self._confirmed_count = idx + 1

        # 未确定的最后一句：当前句中间结果
        for idx in range(self._confirmed_count, len(utterances)):
            text = str(utterances[idx].get("text", "")).strip()
            if text:
                self._notify_result(text, SLICE_INTERMEDIATE, idx)

    # ---- asyncio 会话 ----

    async def _connect(self) -> None:
        headers = self._build_headers()
        logid_hint = "resource_id=%s" % self.resource_id
        logger.info("建立火山 ASR 连接 %s", logid_hint)
        self._set_state(STATE_CONNECTING)

        try:
            try:
                # websockets >= 13
                self._ws = await websockets.connect(
                    self.WS_URL, max_size=None, additional_headers=headers
                )
            except TypeError:  # pragma: no cover - 旧版库兼容
                self._ws = await websockets.connect(
                    self.WS_URL, max_size=None, extra_headers=headers
                )
        except Exception as exc:
            self._set_state(STATE_ERROR)
            msg = f"WebSocket 连接失败：{exc}"
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            body = getattr(response, "body", None)
            body_text = ""
            if isinstance(body, (bytes, bytearray)):
                body_text = bytes(body).decode("utf-8", "replace")
            if status in (401, 403) or " 401 " in f" {msg} " or " 403 " in f" {msg} ":
                if body_text:
                    logger.error("火山 401 响应体：%s", body_text[:300])
                if "Invalid X-Api-Key" in body_text:
                    msg += (
                        "（该密钥不是有效的火山 API Key：若您用的是应用详情页的"
                        " Access Token，请在设置中填写 App ID + Access Token 启用旧版鉴权；"
                        "新版 API Key 需在控制台\"API Key 管理\"生成）"
                    )
                elif "grant" in body_text:
                    msg += (
                        "（火山查不到该密钥的应用授权：请核对 App ID 与 Access Token"
                        " 必须来自同一个应用（console.volcengine.com/speech/app"
                        " 应用详情页），且该应用已开通\"流式语音识别\"服务"
                        "（免费试用过期也会如此））"
                    )
                else:
                    msg += "（鉴权失败，请到火山引擎语音技术控制台核对密钥与服务开通状态）"
            raise ASRError(msg) from exc

        # 建连后发送首包（full client request）
        try:
            request = build_full_client_request(self._build_request_payload())
            await self._ws.send(request)
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"发送首包失败：{exc}") from exc

        # 若鉴权失败，服务端通常在首个响应中返回错误消息
        try:
            first = await asyncio.wait_for(self._ws.recv(), timeout=10)
            sequence, payload = parse_response(first)
            if payload.get("message_type") == MSG_ERROR_RESPONSE:
                await self._ws.close()
                self._set_state(STATE_ERROR)
                code = payload.get("error_code", payload.get("code", -1))
                message = payload.get("message",
                                      payload.get("error_message", "鉴权失败"))
                raise ASRError(f"握手失败[{code}]：{message}", code=code)
            self._handle_payload(payload)
        except asyncio.TimeoutError:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError("握手超时，未收到服务端响应")
        except ASRError:
            raise
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"握手响应解析失败：{exc}") from exc

        self._set_state(STATE_CONNECTED)
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    def _next_sequence(self) -> int:
        # 序号从 2 开始：首包 full client request 占用序号 1，
        # 音频包序号必须与服务端的包计数严格一致（实测：首音频包=2）
        self._sequence += 1
        return self._sequence

    async def _send_loop(self) -> None:
        try:
            last_send = time.monotonic()
            while not self._stop_event.is_set():
                try:
                    audio = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # 用户长时间停顿（无真实音频）：周期性喂静音帧保活
                    if time.monotonic() - last_send >= self.KEEPALIVE_INTERVAL:
                        await self._ws.send(
                            build_audio_request(
                                self.SILENCE_CHUNK, self._next_sequence()
                            )
                        )
                        last_send = time.monotonic()
                    continue
                if audio is None:
                    break
                await self._ws.send(
                    build_audio_request(audio, self._next_sequence())
                )
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

                if isinstance(msg, str):
                    logger.warning("收到非二进制消息：%s", msg[:200])
                    continue

                sequence, payload = parse_response(msg)
                self._handle_payload(payload)

                # 负包响应：识别全部结束
                if sequence < 0:
                    logger.info("识别全部结束 sequence=%s", sequence)
                    break
        except Exception:
            logger.exception("接收循环异常")
        finally:
            self._set_state(STATE_DISCONNECTED)

    async def _shutdown(self) -> None:
        """发送负包（结束信号），等接收循环收完剩余结果后关闭连接。"""
        if self._ws is not None:
            try:
                # 负包序号 = -(下一个正序号)：与服务端包计数严格一致
                # （实测：音频包到 16 时负包须为 -17，否则报序号不匹配）
                await self._ws.send(build_last_request(-(self._sequence + 1)))
                logger.info("已发送负包（结束信号）sequence=%s", -(self._sequence + 1))
            except websockets.ConnectionClosed:
                logger.info("发送负包时连接已断开")
            except Exception:
                logger.exception("发送负包失败")

        # 等待接收循环自然结束（负包响应 sequence<0 后 break），最多 3 秒
        if self._recv_task and not self._recv_task.done():
            try:
                await asyncio.wait_for(self._recv_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.info("等待最终结果超时，取消接收循环")
                self._recv_task.cancel()
            except Exception:
                pass

        if self._stop_event:
            self._stop_event.set()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    # ---- 同步接口 ----

    def start(self) -> None:
        """建立连接（在新线程的 asyncio loop 中执行）。握手失败抛出 ASRError。"""
        if self._thread is not None:
            raise ASRError("ASR 已启动，请先 stop")

        self._loop = asyncio.new_event_loop()
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._sequence = 1  # 首个音频包序号为 2（首包 full request 占用 1）
        self._confirmed_count = 0

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
        except RuntimeError:
            logger.warning("事件循环已关闭，丢弃音频块")

    def stop(self) -> None:
        """发送负包（结束信号）并关闭连接。"""
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
