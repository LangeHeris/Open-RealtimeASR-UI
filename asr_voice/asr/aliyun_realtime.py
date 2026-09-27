"""阿里云百炼实时语音识别（WebSocket）封装。

协议要点（DashScope 全双工 WebSocket，2026 版 v2 协议无需 continue-task）：
- URL: wss://dashscope.aliyuncs.com/api-ws/v1/inference
- 鉴权: 请求头 Authorization: bearer <百炼 API Key>（握手阶段校验，无效返回 401/403）
- 开任务: 发送 {"header":{"action":"run-task","task_id":<32位hex>,"streaming":"duplex"},
          "payload":{"task_group":"audio","task":"asr","function":"recognition",
                     "model":<模型名>,"parameters":{...},"input":{}}}
- 启动确认: 收到 header.event == "task-started" 后才能发音频
- 音频: binary message（PCM 单声道，按实时率发送）
- 结束: 发送 {"header":{"action":"finish-task","task_id":...,"streaming":"duplex"},
          "payload":{"input":{}}}，收到 task-finished 后关闭
- 结果: header.event == "result-generated"，
       payload.output.sentence = {"text": <当前句累积文本>,
                                  "begin_time": <ms>, "sentence_end": <bool>}
       sentence_end=true 表示一句最终结果（稳态），对应腾讯 slice_type=2
- 失败: header.event == "task-failed"，error_code / error_message 在 header 内

文档: https://help.aliyun.com/zh/model-studio/websocket-for-paraformer-real-time-service
"""

from __future__ import annotations

import asyncio
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

# sentence_end 语义（与腾讯云 slice_type 对齐，app 层共用同一套回调）
SLICE_START = 0         # 一段话开始（阿里云无显式开始事件，不使用）
SLICE_INTERMEDIATE = 1  # 一段话中间（非稳态，可能变化）
SLICE_FINAL = 2         # 一段话结束（稳态最终结果）

# 状态（与腾讯云客户端一致）
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"


class ASRError(Exception):
    """ASR 调用异常。"""

    def __init__(self, message: str, code=-1):
        super().__init__(message)
        self.code = code


# 建连错误码：app 层据此区分"瞬时网络问题（值得自动重试）"与
# 配置/密钥类错误（直接提示用户，不重试）
CODE_CONNECT_TIMEOUT = "connect_timeout"

# 阿里云"空任务"错误码：未发送任何音频就结束任务时服务端返回。
# 会话在发过音频前就被结束（如热键快速连点：开始→立即取消）属预期
# 行为，降级为 INFO、不提示用户，避免"快速取消一次就弹一次错"。
ERROR_NO_VALID_AUDIO = "NO_VALID_AUDIO_ERROR"


ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[object, str], None]  # code 为字符串（阿里云）或 int（对齐腾讯）
StateCallback = Callable[[str], None]


class AliyunRealtimeASR:
    """阿里云百炼实时语音识别客户端。

    在独立线程运行 asyncio 事件循环，对外提供与 TencentRealtimeASR
    相同的同步接口，app 层无需区分引擎：
    - start() 建立连接并完成 run-task 握手
    - send_audio(block) 推送音频块（numpy int16 数组）
    - stop() 发送 finish-task 并关闭连接

    通过回调返回结果：
    - on_result(text, slice_type, index)：sentence_end=false -> 1，true -> 2
    - on_error(code, message)：code 为阿里云 error_code 字符串
    - on_state(state)
    """

    WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    # 分阶段计时诊断用（DNS 解析 vs TCP/TLS/WebSocket 握手）
    WS_HOST = "dashscope.aliyuncs.com"
    WS_PORT = 443

    def __init__(
        self,
        api_key: str,
        model: str = "paraformer-realtime-v2",
        sample_rate: int = 16000,
        disfluency_removal: bool = False,
        max_sentence_silence: int = 800,
        vocabulary_id: str = "",
        language_hints: str = "",
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        # 8k 电话场景模型（paraformer-realtime-8k-v2 / fun-asr-flash-8k-realtime）：
        # 麦克风固定 16k 采集，发送前降采样到 8k，sample_rate 参数报 8000
        self._is_8k = model.startswith(
            ("paraformer-realtime-8k", "fun-asr-flash-8k"))
        self.send_sample_rate = 8000 if self._is_8k else int(sample_rate)
        self.disfluency_removal = disfluency_removal
        self.max_sentence_silence = max_sentence_silence
        self.vocabulary_id = vocabulary_id
        self.language_hints = language_hints
        # WebSocket 握手超时（秒）：服务端在该时间内无响应即判定失败。
        # 显式限定，避免依赖 websockets 库默认值随版本升级而漂移。
        self._open_timeout = 10.0

        self.on_result = on_result
        self.on_error = on_error
        self.on_state = on_state

        self._state = STATE_DISCONNECTED
        self._ws = None
        self._task_id = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        # 会话统计（每次 start 重置；stop 时打 INFO 汇总，便于排查"没结果"类问题）
        self._stat_sent_blocks = 0
        self._stat_sent_bytes = 0
        self._stat_events: dict = {}

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    def _build_run_task(self) -> str:
        """构建 run-task 指令 JSON。

        参数按模型区分：语气词过滤/心跳/热词/语种等 Paraformer v2 专属
        参数只对该模型发送；断句控制（关语义断句 + VAD 静音阈值）对
        paraformer v2 / qwen-audio 均发送。其余模型仅发通用必选项
        format/sample_rate，避免不支持的模型因未知参数报 task-failed。
        """
        params: dict = {
            "format": "pcm",
            "sample_rate": self.send_sample_rate,
        }
        # 持续静音保活（语音输入场景常有无声段，默认 false 会超时断连）：
        # paraformer 实时系列支持；其余模型走服务端默认
        if self.model.startswith("paraformer-realtime"):
            params["heartbeat"] = True
        if self.model.startswith("paraformer-realtime-v2"):
            params["disfluency_removal_enabled"] = bool(self.disfluency_removal)
        # 热词/语言提示：paraformer v2/8k-v2 在百炼热词 API 支持列表内；
        # qwen-audio 未列入，不发
        if self.model.startswith(
            ("paraformer-realtime-v2", "paraformer-realtime-8k-v2")
        ):
            if self.vocabulary_id:
                params["vocabulary_id"] = self.vocabulary_id
            if self.language_hints:
                hints = [h.strip() for h in str(self.language_hints).split(",") if h.strip()]
                if hints:
                    params["language_hints"] = hints
        # 断句控制：paraformer 实时系列与 qwen-audio 官方均支持这两个参数
        # （见百炼实时识别文档）。不发送时服务端按自身默认策略断句（语义断句
        # 切小语义块，表现为"两个字两个字一段"）。显式关语义断句走 VAD 断句，
        # 静音阈值用 max_sentence_silence（ms）。
        if self.model.startswith(("paraformer-realtime", "qwen-audio")):
            params["semantic_punctuation_enabled"] = False
            if self.max_sentence_silence:
                params["max_sentence_silence"] = int(self.max_sentence_silence)

        self._task_id = uuid.uuid4().hex
        # 重置会话统计
        self._stat_sent_blocks = 0
        self._stat_sent_bytes = 0
        self._stat_events = {}
        message = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": params,
                "input": {},
            },
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
        logger.info("建立阿里云 ASR 连接 model=%s", self.model)
        self._set_state(STATE_CONNECTING)

        # 分阶段计时（DNS 解析 -> TCP/TLS/WS 握手），超时/异常时能区分
        # "DNS 卡顿"与"握手无响应"，并供 app 层判定是否自动重试。
        try:
            t0 = time.monotonic()
            # 注意：asyncio 模块级 getaddrinfo 在 Python 3.12 已移除，
            # 需用事件循环的方法（loop.getaddrinfo）。
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    self.WS_HOST, self.WS_PORT, type=socket.SOCK_STREAM
                ),
                timeout=5,
            )
            logger.info(
                "DNS 解析 %s -> %d 个地址（示例 %s），耗时 %.0f ms",
                self.WS_HOST,
                len(infos),
                infos[0][4][0] if infos else "-",
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
            raise ASRError(f"WebSocket 连接失败：域名解析失败：{exc}") from exc

        headers = [("Authorization", f"bearer {self.api_key}")]
        try:
            # websockets>=14 参数名 additional_headers，旧版为 extra_headers
            t0 = time.monotonic()
            try:
                self._ws = await websockets.connect(
                    self.WS_URL,
                    max_size=None,
                    additional_headers=headers,
                    open_timeout=self._open_timeout,
                )
            except TypeError:
                self._ws = await websockets.connect(
                    self.WS_URL,
                    max_size=None,
                    extra_headers=headers,
                    open_timeout=self._open_timeout,
                )
            logger.info(
                "WebSocket 握手完成，耗时 %.0f ms",
                (time.monotonic() - t0) * 1000,
            )
        except TimeoutError as exc:
            # websockets 在 open_timeout 内未完成握手即抛 TimeoutError
            # （消息 timed out during opening handshake）。带 code 供上层
            # 判定为瞬时网络问题并自动重试。
            self._set_state(STATE_ERROR)
            hint = (
                f"握手超时（服务端 {self._open_timeout:.1f} 秒无响应，"
                "疑似网络瞬时抖动或服务端繁忙）"
            )
            raise ASRError(hint, code=CODE_CONNECT_TIMEOUT) from exc
        except Exception as exc:
            self._set_state(STATE_ERROR)
            hint = "WebSocket 连接失败（API Key 无效或网络不通）" if "401" in str(exc) or "403" in str(exc) else f"WebSocket 连接失败：{exc}"
            raise ASRError(hint) from exc

        try:
            await self._ws.send(self._build_run_task())
            reply = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(reply)
        except asyncio.TimeoutError:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError("run-task 超时，未收到 task-started")
        except Exception as exc:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"run-task 失败：{exc}") from exc

        header = data.get("header", {})
        event = header.get("event", "")
        if event == "task-failed":
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(
                f"任务启动失败：{header.get('error_message', '')}",
                code=header.get("error_code", -1),
            )
        if event != "task-started":
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(f"未预期的启动响应：{event or reply[:120]}")

        logger.info("阿里云任务已启动 task_id=%s", self._task_id)
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
                await self._ws.send(audio)
                self._stat_sent_blocks += 1
                self._stat_sent_bytes += len(audio)
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

                header = data.get("header", {})
                event = header.get("event", "")
                self._stat_events[event or "?"] = self._stat_events.get(event or "?", 0) + 1

                if event == "result-generated":
                    sentence = (data.get("payload", {})
                                    .get("output", {})
                                    .get("sentence", {}))
                    text = sentence.get("text", "")
                    if text:
                        is_end = bool(sentence.get("sentence_end", False))
                        self._notify_result(
                            text,
                            SLICE_FINAL if is_end else SLICE_INTERMEDIATE,
                            int(sentence.get("begin_time", 0)),
                        )
                elif event == "task-finished":
                    logger.info("识别任务结束 task-finished")
                    break
                elif event == "task-failed":
                    err_code = header.get("error_code", "")
                    err_msg = header.get("error_message", "")
                    if err_code == ERROR_NO_VALID_AUDIO:
                        # 未发送任何音频即结束任务（热键快速连点开始→取消）：
                        # 服务端按"无有效音频"拒绝空任务，属预期行为。
                        # 会话统计行（发送 0 块）已记录现场，无需打扰用户。
                        logger.info(
                            "task-failed：未发送音频即结束"
                            "（NO_VALID_AUDIO_ERROR，预期行为）"
                        )
                        break
                    self._notify_error(err_code, err_msg)
                    break
                else:
                    logger.debug("忽略事件：%s", event)
        except Exception:
            logger.exception("接收循环异常")
        finally:
            self._set_state(STATE_DISCONNECTED)

    async def _shutdown(self) -> None:
        """发送 finish-task，等接收循环收完剩余结果后关闭连接。"""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({
                    "header": {
                        "action": "finish-task",
                        "task_id": self._task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {}},
                }))
                logger.info("已发送 finish-task")
            except websockets.ConnectionClosed:
                logger.info("发送 finish-task 时连接已断开")
            except Exception:
                logger.exception("发送 finish-task 失败")

        # 等接收循环自然结束（收到 task-finished 后 break），最多 3 秒
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

        logger.info("阿里云会话统计：发送音频 %d 块 / %.1f 秒，收到事件 %s",
                    self._stat_sent_blocks,
                    self._stat_sent_bytes / (self.send_sample_rate * 2.0),
                    self._stat_events or "无")

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

        self._thread = threading.Thread(target=_run, daemon=True, name="aliyun-asr-loop")
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
        """推送音频块。block 为 int16 numpy 数组（16k 采集）。

        8k 电话场景模型在此降采样（相邻两样本取平均，简易抗混叠），
        其余模型原样发送。
        """
        if not self.is_connected() or self._audio_queue is None or self._loop is None:
            return
        data = np.asarray(block).reshape(-1)
        if self._is_8k and data.size >= 2:
            data = ((data[0::2].astype(np.int32)
                     + data[1::2].astype(np.int32)) >> 1).astype(np.int16)
        payload = np.ascontiguousarray(data).tobytes()
        try:
            asyncio.run_coroutine_threadsafe(self._audio_queue.put(payload), self._loop)
        except RuntimeError:
            logger.warning("事件循环已关闭，丢弃音频块")

    def stop(self) -> None:
        """发送 finish-task 并关闭连接。"""
        if self._loop is None or self._stop_event is None:
            return
        if self._audio_queue and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._audio_queue.put(None), self._loop)
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                logger.exception("关闭超时")
        self._cleanup_loop()
