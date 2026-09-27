"""腾讯云实时语音识别（WebSocket）封装。

协议要点：
- URL: wss://asr.cloud.tencent.com/asr/v2/{appid}?{params}
- 签名: HMAC-SHA1(secret_key, 签名原文) → base64 → urlencode
- 签名原文: asr.cloud.tencent.com/asr/v2/{appid}?{参数按字典序排序，value 不 encode}
- 音频: binary message，建议 1:1 实时率发送（16k → 200ms ≈ 6400 字节）
- 结束: 发送 {"type": "end"}
- 结果: result.slice_type 0=开始 1=中间(非稳态) 2=结束(稳态最终)；final=1 全部结束
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
import threading
import time
import uuid
import urllib.parse
from typing import Callable, Optional

import numpy as np
import websockets

logger = logging.getLogger(__name__)

# slice_type 语义
SLICE_START = 0         # 一段话开始
SLICE_INTERMEDIATE = 1  # 一段话中间（非稳态，可能变化）
SLICE_FINAL = 2         # 一段话结束（稳态最终结果）

# 状态
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"

# voice_format 编码
VOICE_FORMAT_PCM = 1

# preview 客户端切句参数：切句请求后尾部稳定此时长即提交一句；
# 尾部持续变化（停顿后又开口）则到上限强制提交
CUT_STABLE_MS = 500
CUT_MAX_WAIT_MS = 2000

# 引擎协议分支（返回 JSON 结构与握手参数集不同）：
# - v1：result.slice_type 0/1/2 + voice_text_str（16k_zh、16k_zh_en 等经典引擎）
# - v2：sentences{sentence, sentence_type 0/1, sentence_id, speaker_id}
#       （16k_zh_en_2.0 / 16k_zh_en_speaker_2.0，大模型2.0 正式版）
# - preview：Hy-ASR-3.0-preview 内测版，不支持 VAD/热词/过滤等参数，
#   握手只发基础参数，final 仅在 end 冲刷时返回
V2_ENGINES = {"16k_zh_en_2.0", "16k_zh_en_speaker_2.0"}
PREVIEW_ENGINES = {"Hy-ASR-3.0-preview"}


class ASRError(Exception):
    """ASR 调用异常。"""

    def __init__(self, message: str, code: int = -1):
        super().__init__(message)
        self.code = code


ResultCallback = Callable[[str, int, int], None]
ErrorCallback = Callable[[int, str], None]
StateCallback = Callable[[str], None]


class TencentRealtimeASR:
    """腾讯云实时语音识别客户端。

    在独立线程运行 asyncio 事件循环，对外提供同步接口：
    - start() 建立连接并完成握手
    - send_audio(block) 推送音频块（numpy int16 数组）
    - stop() 发送结束信号并关闭连接

    通过回调返回结果：
    - on_result(text, slice_type, index)
    - on_error(code, message)
    - on_state(state)
    """

    WS_HOST = "asr.cloud.tencent.com"
    WS_PATH = "/asr/v2"

    def __init__(
        self,
        app_id: str,
        secret_id: str,
        secret_key: str,
        engine_model_type: str = "16k_zh-PY",
        filter_punc: int = 0,
        convert_num_mode: int = 1,
        filter_dirty: int = 0,
        filter_modal: int = 0,
        hotword_id: str = "",
        customization_id: str = "",
        needvad: int = 1,
        on_result: Optional[ResultCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_state: Optional[StateCallback] = None,
    ):
        self.app_id = app_id
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.engine_model_type = engine_model_type
        self.filter_punc = filter_punc
        self.convert_num_mode = convert_num_mode
        self.filter_dirty = filter_dirty
        self.filter_modal = filter_modal
        self.hotword_id = hotword_id
        self.customization_id = customization_id
        self.needvad = needvad

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

        # preview 客户端切句状态（其他分支无意义，恒为初始值）
        self._committed_text = ""       # 已作为 FINAL 发出的累积前缀（游标）
        self._last_full = ""            # 最近一帧的累积全文
        self._sentence_seq = 0          # 已提交句序号（从 0 递增）
        self._cut_requested = False     # 是否有待处理的切句请求
        self._cut_deadline = 0.0        # 切句强制提交时刻（loop.time 基准）
        self._last_tail = None          # 切句等待中的尾部（None=请求后尚无帧）
        self._last_change_at = 0.0      # 尾部最近变化时刻（loop.time 基准）
        self._cut_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> str:
        return self._state

    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    @property
    def flush_tail_on_stop(self) -> bool:
        """停止时是否把未提交的尾部文字冲刷为上屏文本。

        仅 preview 分支为 True：服务端无断句，final 只在 end 冲刷时返回，
        最后一句（短句可能整句都在尾部）全靠停止冲刷交付；停止会话应
        参照 FunASR 的 sync_stop_flush 语义放行该 final 上屏（"停止即上屏"）。
        其余分支服务端会中断句，停止冲刷只是重复帧，无需放行。
        """
        return self._engine_branch() == "preview"

    def _engine_branch(self) -> str:
        """当前引擎所属协议分支：v1 | v2 | preview。"""
        if self.engine_model_type in V2_ENGINES:
            return "v2"
        if self.engine_model_type in PREVIEW_ENGINES:
            return "preview"
        return "v1"

    def _build_request(self) -> tuple[str, str]:
        """构建 WebSocket URL 和 voice_id。"""
        voice_id = uuid.uuid4().hex
        timestamp = int(time.time())
        expired = timestamp + 86400  # 1 天有效
        nonce = random.randint(1, 9999999999)

        # 三分支参数集按各自接口文档设计（发不支持的参数可能被拒或忽略）：
        # - v1：完整参数（filter_punc / vad_silence_time 系引擎仅 v1 文档列出）
        # - v2：无 filter_punc（V2 文档未列），其余过滤/热词参数同 v1
        # - preview：仅基础参数（官方声明不支持 vad/热词/词汇替换/噪音阈值）
        branch = self._engine_branch()
        params = {
            "secretid": self.secret_id,
            "timestamp": timestamp,
            "expired": expired,
            "nonce": nonce,
            "engine_model_type": self.engine_model_type,
            "voice_id": voice_id,
            "voice_format": VOICE_FORMAT_PCM,
        }
        if branch == "v1":
            params.update(
                {
                    "needvad": self.needvad,
                    "filter_punc": self.filter_punc,
                    "convert_num_mode": self.convert_num_mode,
                    "filter_dirty": self.filter_dirty,
                    "filter_modal": self.filter_modal,
                }
            )
        elif branch == "v2":
            params.update(
                {
                    "needvad": self.needvad,
                    "convert_num_mode": self.convert_num_mode,
                    "filter_dirty": self.filter_dirty,
                    "filter_modal": self.filter_modal,
                }
            )
        # preview 分支：只带基础参数，不做任何追加

        if branch != "preview":
            if self.hotword_id:
                params["hotword_id"] = self.hotword_id
            if self.customization_id:
                params["customization_id"] = self.customization_id

        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)

        sign_str = f"{self.WS_HOST}{self.WS_PATH}/{self.app_id}?{query}"
        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        signature_encoded = urllib.parse.quote(signature, safe="")

        url = (
            f"wss://{self.WS_HOST}{self.WS_PATH}/{self.app_id}"
            f"?{query}&signature={signature_encoded}"
        )
        return url, voice_id

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

    def _notify_error(self, code: int, message: str) -> None:
        logger.error("ASR 错误 code=%s message=%s", code, message)
        if self.on_error:
            try:
                self.on_error(code, message)
            except Exception:
                logger.exception("on_error 回调异常")

    # ---- Hy-ASR preview 客户端切句 ----
    # preview 分支服务端无断句：会中只吐累积全文中间帧，final 仅在 end 冲刷。
    # 这里按本地 VAD 停顿（end_segment）把累积全文切成逐句 FINAL，
    # 对齐其他引擎的逐句行为。游标跟踪累积全文，游标之后的尾部即"当前句"。

    def end_segment(self) -> None:
        """切句请求：仅 preview 分支生效（客户端切句），其他分支 no-op。

        由音频回调线程在本地 VAD 检测到停顿时调用（与 FunASR 的
        end_segment 同一触发源），经 call_soon_threadsafe 登记到事件循环线程。
        """
        if self._engine_branch() != "preview":
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(self._request_cut)
        except RuntimeError:
            logger.warning("事件循环已关闭，忽略切句请求")

    def _request_cut(self) -> None:
        """登记切句请求（事件循环线程内执行）。已有等待中的请求则不重复登记。"""
        if self._cut_task is not None and not self._cut_task.done():
            return
        self._cut_requested = True
        tail = self._preview_tail(self._last_full)
        self._last_tail = tail if tail else None
        self._last_change_at = self._loop.time()
        self._cut_deadline = self._loop.time() + CUT_MAX_WAIT_MS / 1000.0
        self._cut_task = asyncio.create_task(self._cut_watch())

    def _preview_tail(self, full: str) -> str:
        """累积全文 → 游标之后的尾部（当前句）。

        服务端（大模型）在静音期会回头重写已提交内容（实测频繁），前缀
        关系随之破坏。定位策略（按优先级）：
        1. 前缀完好：尾部 = 游标之后的新增文本；
        2. 已提交区的长后缀仍保留在 full 中（重写通常只改句首/中段）：
           以该后缀最后一次出现为锚点重定位游标，尾部 = 锚点之后的新内容；
        3. 兜底：退回到共同前缀，游标随之回退/前移——关键是不吞后续
           句子（旧实现按长度硬切，服务端文本变短时尾部恒空，"卡住"）。
        """
        c = self._committed_text
        if full.startswith(c):
            return full[len(c):]
        lcp = 0
        limit = min(len(full), len(c))
        while lcp < limit and full[lcp] == c[lcp]:
            lcp += 1
        for n in range(min(len(c), 24), 3, -1):
            pos = full.rfind(c[-n:], lcp)
            if pos >= lcp:
                logger.warning(
                    "preview 服务端重写已提交内容：后缀锚点重定位（%d 字后缀）", n
                )
                self._committed_text = full[:pos + n]
                return full[pos + n:]
        logger.warning(
            "preview 服务端重写已提交内容：退回到共同前缀（%d/%d 字）",
            lcp, len(c),
        )
        self._committed_text = full[:lcp]
        return full[lcp:]

    def _commit_cut(self, reason: str) -> None:
        """把当前尾部提交为 FINAL 并前移游标；尾部为空只清切句状态。"""
        self._cut_requested = False
        self._last_tail = None
        self._last_change_at = 0.0
        tail = self._preview_tail(self._last_full)
        if not tail:
            return
        self._committed_text = self._last_full
        idx = self._sentence_seq
        self._sentence_seq += 1
        logger.info("preview 客户端切句(%s)：第 %d 句 %d 字", reason, idx, len(tail))
        self._notify_result(tail, SLICE_FINAL, idx)

    async def _cut_watch(self) -> None:
        """切句请求后等尾部稳定：CUT_STABLE_MS 无变化提交；一直变则到 deadline 强制。"""
        while True:
            now = self._loop.time()
            if now >= self._cut_deadline:
                self._commit_cut("forced")
                return
            if self._last_tail is not None:
                if (now - self._last_change_at) * 1000.0 >= CUT_STABLE_MS:
                    self._commit_cut("stable")
                    return
            await asyncio.sleep(0.05)

    def _emit_preview(self, text: str, slice_type: int) -> None:
        """preview 分支结果帧统一转换：累积全文 → 逐句语义。

        中间帧只吐尾部（当前句），与其他引擎逐句中间帧语义一致；
        end 冲刷 FINAL 同样只吐尾部，避免与已切出的句子重复；空尾部不吐。
        """
        full = str(text or "")
        self._last_full = full
        if slice_type == SLICE_FINAL:
            # 冲刷已到，切句等待失去意义
            if self._cut_task is not None and not self._cut_task.done():
                self._cut_task.cancel()
            self._cut_task = None
            self._cut_requested = False
            tail = self._preview_tail(full)
            if not tail:
                return
            self._committed_text = full
            idx = self._sentence_seq
            self._sentence_seq += 1
            self._notify_result(tail, SLICE_FINAL, idx)
            return
        tail = self._preview_tail(full)
        if not tail:
            return
        if self._cut_requested and tail != self._last_tail:
            self._last_tail = tail
            self._last_change_at = (
                self._loop.time() if self._loop is not None else 0.0
            )
        self._notify_result(tail, SLICE_INTERMEDIATE, self._sentence_seq)

    async def _connect(self) -> None:
        url, voice_id = self._build_request()
        logger.info(
            "建立 ASR 连接 voice_id=%s engine=%s", voice_id, self.engine_model_type
        )
        self._set_state(STATE_CONNECTING)

        try:
            self._ws = await websockets.connect(url, max_size=None)
        except Exception as exc:
            self._set_state(STATE_ERROR)
            raise ASRError(f"WebSocket 连接失败：{exc}") from exc

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

        if data.get("code") != 0:
            await self._ws.close()
            self._set_state(STATE_ERROR)
            raise ASRError(
                f"握手失败：{data.get('message')}", code=data.get("code", -1)
            )

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
                    logger.warning("无法解析消息：%s", msg[:200])
                    continue

                if data.get("final") == 1:
                    # preview 分支：final=1 帧携带 end 冲刷的最终结果
                    # （会话中只有中间帧，短句可能整句都等在这帧）。
                    # 先按 SLICE_FINAL 冲刷尾部再退出，保证"停止即上屏"；
                    # 服务端已单独投递过 final 帧时，游标使本次冲刷为空
                    # no-op，不会重复。其余分支该帧只是已投递 final 的
                    # 重复，直接退出。
                    if self._engine_branch() == "preview":
                        result = data.get("result")
                        text = ""
                        if isinstance(result, dict):
                            text = str(result.get("voice_text_str", "") or "")
                        self._emit_preview(text or self._last_full, SLICE_FINAL)
                    logger.info("识别全部结束 final=1")
                    break

                code = data.get("code", 0)
                if code != 0:
                    self._notify_error(code, data.get("message", ""))
                    break

                # V1 引擎：result.slice_type 0/1/2 + voice_text_str；
                # preview 分支经游标转换（_emit_preview），不直通原始帧
                result = data.get("result")
                if result:
                    if self._engine_branch() == "preview":
                        self._emit_preview(
                            result.get("voice_text_str", ""),
                            result.get("slice_type", 0),
                        )
                    else:
                        self._notify_result(
                            result.get("voice_text_str", ""),
                            result.get("slice_type", 0),
                            result.get("index", 0),
                        )

                # V2 引擎（大模型2.0）：sentences 结构，sentence_type
                # 0=非稳态 1=稳态，映射到统一的 slice_type 语义；
                # sentence_id 从 0 逐稳态句递增，直接作 index
                sentences = data.get("sentences")
                if sentences:
                    # 文档结构是单句对象；兼容服务端按列表分句返回的情况
                    items = sentences if isinstance(sentences, list) else [sentences]
                    for sen in items:
                        if not isinstance(sen, dict):
                            continue
                        text = str(sen.get("sentence", "") or "")
                        if not text:
                            continue
                        stable = sen.get("sentence_type", 0)
                        if self._engine_branch() == "preview":
                            self._emit_preview(
                                text,
                                SLICE_FINAL if stable == 1 else SLICE_INTERMEDIATE,
                            )
                        else:
                            self._notify_result(
                                text,
                                SLICE_FINAL if stable == 1 else SLICE_INTERMEDIATE,
                                int(sen.get("sentence_id", 0)),
                            )
        except Exception:
            logger.exception("接收循环异常")
        finally:
            self._set_state(STATE_DISCONNECTED)

    async def _shutdown(self) -> None:
        """发送 end 信号，等接收循环收完剩余结果后关闭连接。

        不直接调 ws.recv()（会和 _recv_loop 冲突导致 ConcurrencyError），
        而是等 _recv_task 自然结束（收到 final=1 后 break）。
        """
        # 1. 发送结束信号
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "end"}))
                logger.info("已发送 end 信号")
            except websockets.ConnectionClosed:
                logger.info("发送 end 时连接已断开")
            except Exception:
                logger.exception("发送 end 失败")

        # 1.5 取消在途切句等待（会话即将结束，剩余文本由冲刷帧提交）
        if self._cut_task is not None and not self._cut_task.done():
            self._cut_task.cancel()

        # 2. 等待接收循环自然结束（收到 final=1 后 break），最多 3 秒
        if self._recv_task and not self._recv_task.done():
            try:
                await asyncio.wait_for(self._recv_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.info("等待最终结果超时，取消接收循环")
                self._recv_task.cancel()
            except Exception:
                logger.debug("等待接收循环结束异常，忽略", exc_info=True)

        # 2.5 preview 兜底冲刷：服务端未在超时前回 final（最终结果帧未到），
        # 用最后累积全文冲刷尾部。尾句文字会中已在悬浮条显示过，不会凭空
        # 多出内容；服务端已冲刷过时游标使本次为空 no-op。保证短句
        # （如两个字）停止后不会"等到超时还不发送"
        if self._engine_branch() == "preview":
            try:
                self._emit_preview(self._last_full, SLICE_FINAL)
            except Exception:
                logger.exception("preview 停止兜底冲刷失败")

        # 3. 停止发送循环
        if self._stop_event:
            self._stop_event.set()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()

        # 4. 关闭连接（状态由 _recv_loop 的 finally 统一设置，不重复）
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("关闭 WebSocket 异常（停止路径尽力而为）", exc_info=True)

    def start(self) -> None:
        """建立连接（在新线程的 asyncio loop 中执行）。握手失败抛出 ASRError。"""
        if self._thread is not None:
            raise ASRError("ASR 已启动，请先 stop")

        self._loop = asyncio.new_event_loop()
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()

        self._committed_text = ""
        self._last_full = ""
        self._sentence_seq = 0
        self._cut_requested = False
        self._last_tail = None
        self._last_change_at = 0.0
        self._cut_task = None

        def _run():
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_forever()
            finally:
                # run_forever 停止时可能仍有 pending 任务（如连接卡住时
                # _connect 挂在 websockets.connect 上）：必须取消并等其
                # 收尾，否则 loop 销毁时报 "Task was destroyed but it is
                # pending"。在 run_until_complete 内部才有 running loop，
                # asyncio.all_tasks() 无参版可用且无废弃警告
                async def _cancel_all():
                    tasks = [
                        t for t in asyncio.all_tasks()
                        if t is not asyncio.current_task() and not t.done()
                    ]
                    for t in tasks:
                        t.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                try:
                    self._loop.run_until_complete(_cancel_all())
                except Exception:
                    logger.debug("清理残留 asyncio 任务失败", exc_info=True)
                self._loop.close()

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
