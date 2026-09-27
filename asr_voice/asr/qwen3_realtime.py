"""阿里云百炼 Qwen3-ASR-Flash-Realtime 实时语音识别（Realtime 协议）封装。

与 run-task 协议（paraformer / qwen-audio / fun-asr，见 aliyun_realtime.py）
不同，qwen3-asr-flash-realtime 走 OpenAI Realtime 风格协议：
- URL: wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=<model>
  （子域为百炼业务空间 ID，需在设置页阿里云「业务空间 ID」中配置）
- 鉴权: 请求头 Authorization: Bearer <百炼 API Key> + OpenAI-Beta: realtime=v1
- 会话: WebSocket 连接后服务端发 session.created；客户端发 session.update
  （VAD 断句配置）后开始推音频
- 音频: input_audio_buffer.append 事件，audio 字段为 PCM 16k 单声道的 Base64
- 结束: 发送 session.finish，收到 session.finished 后关闭
- 结果: conversation.item.input_audio_transcription.text（中间结果，text 字段）/
        conversation.item.input_audio_transcription.completed（最终结果，transcript
        字段），映射到统一的 SLICE_INTERMEDIATE / SLICE_FINAL 回调
  注意：该模型不返回时间戳（index 恒为 0）；情感识别固定开启（emotion
  字段，本应用不消费）。

文档: https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-interaction-process
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import threading
import time
import uuid
from typing import Callable, Optional

import numpy as np
import websockets

logger = logging.getLogger(__name__)

# 语义与 aliyun_realtime 对齐，app 层共用同一套回调
SLICE_START = 0         # 一段话开始（本协议无显式开始事件，不使用）
SLICE_INTERMEDIATE = 1  # 一段话中间（非稳态，可能变化）
SLICE_FINAL = 2         # 一段话结束（稳态最终结果）

STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"


class ASRError(Exception):
    """ASR 调用异常。"""

    def __init__(self, message: str, code=-1):
        super().__init__(message)
        self.code = code


# 建连错误码：与 aliyun_realtime 一致，app 层据此判定是否自动重试
CODE_CONNECT_TIMEOUT = "connect_timeout"

ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[object, str], None]
StateCallback = Callable[[str], None]


class Qwen3RealtimeASR:
    """阿里云百炼 Qwen3-ASR-Flash-Realtime 客户端（Realtime 协议）。

    对外接口与 AliyunRealtimeASR / TencentRealtimeASR 相同：
    - start() 建立连接并完成 session.update 握手
    - send_audio(block) 推送音频块（numpy int16 数组，16k 采集）
    - stop() 发送 session.finish 并关闭连接
    回调：on_result(text, slice_type, index) / on_error(code, message) /
    on_state(state)。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-asr-flash-realtime",
        workspace_id: str = "",
        sample_rate: int = 16000,
        max_sentence_silence: int = 800,
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.workspace_id = str(workspace_id or "").strip()
        self.sample_rate = int(sample_rate)
        # VAD 静音判停（毫秒）：映射 session.update 的
        # turn_detection.silence_duration_ms（服务端默认 800）
        self.max_sentence_silence = int(max_sentence_silence or 800)
        self._open_timeout = 10.0

        self.on_result = on_result
        self.on_error = on_error
        self.on_state = on_state

        self._state = STATE_DISCONNECTED
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        # 中间/最终结果计数（会话统计用）
        self._stat_events: dict = {}
        self._stat_sent_chunks = 0

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    def _ws_url(self) -> str:
        host = f"{self.workspace_id}.cn-beijing.maas.aliyuncs.com"
        return f"wss://{host}/api-ws/v1/realtime?model={self.model}"

    def _build_session_update(self) -> str:
        """构建 session.update 指令 JSON（VAD 断句模式）。"""
        session = {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": self.sample_rate,
            "turn_detection": {
                "type": "server_vad",
                "silence_duration_ms": self.max_sentence_silence,
            },
        }
        message = {
            "event_id": uuid.uuid4().hex,
            "type": "session.update",
            "session": session,
        }
        return json.dumps(message)

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

    def _notify_error(self, code, message: str) -> None:
        logger.error("ASR 错误 code=%s message=%s", code, message)
        if self.on_error:
            try:
                self.on_error(code, message)
            except Exception:
                logger.exception("on_error 回调异常")

    async def _connect(self) -> None:
        logger.info("建立阿里云 Qwen3 Realtime 连接 model=%s", self.model)
        self._set_state(STATE_CONNECTING)

        if not self.workspace_id:
            self._set_state(STATE_ERROR)
            raise ASRError(
                "Qwen3 实时模型需要在设置中配置阿里云「业务空间 ID」"
                "（百炼控制台 -> 业务空间），或改用其他阿里云模型"
            )

        try:
            t0 = time.monotonic()
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    f"{self.workspace_id}.cn-beijing.maas.aliyuncs.com",
                    443, type=socket.SOCK_STREAM,
                ),
                timeout=5,
            )
            logger.info(
                "DNS 解析完成 -> %d 个地址（示例 %s），耗时 %.0f ms",
                len(infos), infos[0][4][0] if infos else "-",
                (time.monotonic() - t0) * 1000,
            )
        except asyncio.TimeoutError:
            self._set_state(STATE_ERROR)
            raise ASRError(
                "DNS 解析超时（5 秒无响应，疑似网络/DNS 瞬时问题）",
                code=CODE_CONNECT_TIMEOUT,
            ) from None
        except socket.gaierror as exc:
            self._set_state(STATE_ERROR)
            raise ASRError(
                f"WebSocket 连接失败：域名解析失败（业务空间 ID 是否正确）：{exc}"
            ) from exc

        headers = [
            ("Authorization", f"Bearer {self.api_key}"),
            ("OpenAI-Beta", "realtime=v1"),
        ]
        try:
            t0 = time.monotonic()
            try:
                self._ws = await websockets.connect(
                    self._ws_url(),
                    max_size=None,
                    additional_headers=headers,
                    open_timeout=self._open_timeout,
                )
            except TypeError:
                self._ws = await websockets.connect(
                    self._ws_url(),
                    max_size=None,
                    extra_headers=headers,
                    open_timeout=self._open_timeout,
                )
            logger.info(
                "WebSocket 握手完成，耗时 %.0f ms",
                (time.monotonic() - t0) * 1000,
            )
        except TimeoutError as exc:
            self._set_state(STATE_ERROR)
            raise ASRError(
                f"握手超时（服务端 {self._open_timeout:.1f} 秒无响应，"
                "疑似网络瞬时抖动或服务端繁忙）",
                code=CODE_CONNECT_TIMEOUT,
            ) from exc
        except Exception as exc:
            self._set_state(STATE_ERROR)
            hint = (
                "WebSocket 连接失败（API Key 无效或网络不通）"
                if "401" in str(exc) or "403" in str(exc)
                else f"WebSocket 连接失败：{exc}"
            )
            raise ASRError(hint) from exc

        try:
            # 等 session.created（连接即建会话）
            reply = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(reply)
        except asyncio.TimeoutError:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError("会话建立超时，未收到 session.created")
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"会话建立失败：{exc}") from exc

        if data.get("type") != "session.created":
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"未预期的启动响应：{str(reply)[:120]}")

        try:
            await self._ws.send(self._build_session_update())
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"发送 session.update 失败：{exc}") from exc

        logger.info("Qwen3 Realtime 会话已建立 session=%s",
                    data.get("session", {}).get("id", "?"))
        self._set_state(STATE_CONNECTED)
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _send_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    audio = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if audio is None:
                    break
                # Realtime 协议：音频走 input_audio_buffer.append（Base64）
                message = json.dumps({
                    "event_id": uuid.uuid4().hex,
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("ascii"),
                })
                await self._ws.send(message)
                self._stat_sent_chunks += 1
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

                etype = data.get("type", "")
                self._stat_events[etype or "?"] = \
                    self._stat_events.get(etype or "?", 0) + 1

                if etype == "conversation.item.input_audio_transcription.text":
                    # 中间结果（实时刷新当前句）
                    text = data.get("text", "")
                    if text:
                        self._notify_result(text, SLICE_INTERMEDIATE, 0)
                elif etype == "conversation.item.input_audio_transcription.completed":
                    # 最终结果（稳态句）
                    text = data.get("transcript", "")
                    if text:
                        self._notify_result(text, SLICE_FINAL, 0)
                elif etype == "session.finished":
                    logger.info("识别会话结束 session.finished")
                    break
                elif etype == "error":
                    err = data.get("error", {})
                    self._notify_error(
                        err.get("code", "error"),
                        err.get("message", str(msg)[:200]),
                    )
                    break
                elif etype in ("input_audio_buffer.speech_started",
                               "input_audio_buffer.speech_stopped",
                               "session.updated", "session.created"):
                    logger.debug("忽略事件：%s", etype)
                else:
                    logger.debug("忽略事件：%s", etype)
        except Exception:
            logger.exception("接收循环异常")
        finally:
            self._set_state(STATE_DISCONNECTED)

    async def _shutdown(self) -> None:
        """发送 session.finish，等接收循环收完剩余结果后关闭连接。"""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({
                    "event_id": uuid.uuid4().hex,
                    "type": "session.finish",
                }))
                logger.info("已发送 session.finish")
            except websockets.ConnectionClosed:
                logger.info("发送 session.finish 时连接已断开")
            except Exception:
                logger.exception("发送 session.finish 失败")

        if self._recv_task and not self._recv_task.done():
            try:
                await asyncio.wait_for(self._recv_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.info("等待最终结果超时，取消接收循环")
                self._recv_task.cancel()
            except Exception:
                logger.debug("等待接收循环结束异常，忽略", exc_info=True)

        if self._stop_event:
            self._stop_event.set()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("关闭 WebSocket 异常（停止路径尽力而为）", exc_info=True)

        logger.info("Qwen3 Realtime 会话统计：发送音频 %d 块，收到事件 %s",
                    self._stat_sent_chunks, self._stat_events or "无")

    def start(self) -> None:
        """建立连接（在新线程的 asyncio loop 中执行）。握手失败抛出 ASRError。"""
        if self._thread is not None:
            raise ASRError("ASR 已启动，请先 stop")

        self._loop = asyncio.new_event_loop()
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()

        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="qwen3-asr-loop")
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
        """推送音频块。block 为 int16 numpy 数组（16k 单声道）。"""
        if not self.is_connected() or self._audio_queue is None or self._loop is None:
            return
        payload = np.ascontiguousarray(
            np.asarray(block).reshape(-1)).tobytes()
        try:
            asyncio.run_coroutine_threadsafe(
                self._audio_queue.put(payload), self._loop)
        except RuntimeError:
            logger.warning("事件循环已关闭，丢弃音频块")

    def stop(self) -> None:
        """发送 session.finish 并关闭连接。"""
        if self._loop is None or self._stop_event is None:
            return
        if self._audio_queue and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._audio_queue.put(None), self._loop)
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                logger.exception("关闭超时")
        self._cleanup_loop()
