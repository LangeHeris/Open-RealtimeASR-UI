"""组合型对话客户端：本地 VAD + ASR 引擎 + HermesClient。

对外接口与 DoubaoDialogClient 完全一致（start / send_audio / stop + 四个
回调），所以 DialogController 只需在 _create_client 里多一个分派分支。

**为什么需要组合**：豆包/Qwen 是端到端 S2S，一条 WS 包办断句+识别+合成；
Hermes 的 API server 三者都不提供（capabilities 实测 audio_api=false /
realtime_voice=false）。三个子组件都在本类内部编排，控制器不知道它们存在
—— 这正是 dialog_events.py 开头那句「新增第三个提供商只需写一个新客户端 +
一张映射表」的兑现方式，只不过 Hermes 是第一个「组合型」。

**半双工为什么在这里**：控制器既有的 half_duplex 闸门语义是「AI 播报时暂停
上行」，而这里要暂停的是「思考中」（实测 5~8 秒），两者不是一回事。改控制器
会牵动另外两个 provider，所以闸门下沉到本类内部。
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from ..audio.vad import EVENT_SPEECH_END, VAD
from .dialog_events import (
    AI_TURN_END, USER_TRANSCRIPT, USER_TURN_END, DialogErrorEvent,
)
from .hermes_client import CODE_CANCEL_FAILED, HermesClient

logger = logging.getLogger(__name__)

# 与各引擎模块一致的分片约定（tencent/aliyun/qwen3 三处取值相同）。
# 本模块独立定义一份，避免依赖某一个具体引擎模块。
SLICE_START = 0
SLICE_INTERMEDIATE = 1
SLICE_FINAL = 2

_SAMPLE_RATE = 16000
_BLOCK_SIZE = 320          # 20ms/块，与对话链路既有的上行包长一致


class HermesDialogClient:
    """组合型对话客户端。"""

    def __init__(self, *, base_url: str, api_key: str,
                 asr_factory, hermes_factory=None,
                 vad_cfg=None, model: str = "hermes-agent",
                 session_title: str = "ORI 语音对话", system_hint: str = "",
                 connect_timeout_ms: int = 5000,
                 turn_timeout_ms: int = 60000,
                 on_state=None, on_event=None, on_audio=None, on_error=None):
        self.on_state = on_state
        self.on_event = on_event
        self.on_audio = on_audio
        self.on_error = on_error

        self._asr_factory = asr_factory
        self._asr = None
        self._turn_in_flight = False
        self._stopped = False              # stop() 后的终态：不再向上抛任何回调
        self._uplink_open = True           # 长按模式下由控制器关闭；默认 True=既有行为
        # 松手那一次成句的放行位（见 hold_end_user_turn）：只放行一条 final
        self._release_final_pass = False
        self._vad = VAD(sample_rate=_SAMPLE_RATE, block_size=_BLOCK_SIZE,
                        **dict(vad_cfg or {}))
        # VAD 自己没有锁：reset() 会从热键/Hermes 线程进来，而 feed() 在采集
        # 线程里跑（判停分支要 concatenate 内部缓冲）—— 见 send_audio。
        self._vad_lock = threading.Lock()

        factory = hermes_factory or HermesClient
        self._client = factory(
            base_url=base_url, api_key=api_key, model=model,
            session_title=session_title, system_hint=system_hint,
            connect_timeout_ms=connect_timeout_ms,
            turn_timeout_ms=turn_timeout_ms,
            on_state=self._on_client_state,
            on_event=self._on_client_event,
            on_audio=self._on_client_audio,
            on_error=self._on_client_error)

    # ---- 对外接口（与 DoubaoDialogClient 同形）----

    @property
    def state(self) -> str:
        return str(getattr(self._client, "state", "disconnected"))

    def is_connected(self) -> bool:
        fn = getattr(self._client, "is_connected", None)
        return bool(fn()) if callable(fn) else False

    def start(self) -> None:
        """先起 ASR 引擎，再建 Hermes 会话。

        顺序不能反：控制器在 client.start() 之前就开了采集流，若会话先就绪
        进入 LISTENING 而 ASR 还没起来，最初几帧会被静默丢掉。
        """
        self._asr = self._asr_factory(
            on_result=self._on_asr_result, on_error=self._on_asr_error)
        try:
            self._asr.start()
            self._client.start()
        except Exception:
            # 启动期任一步失败都要自己收尾：控制器拿到异常后只回滚采集/播放并丢掉
            # 本客户端引用，**不会**再调 stop()。ASR 引擎可能已经起了自有线程/WS
            # （它的 start() 失败时未必清干净 —— engine 可选 aliyun/qwen3），
            # Hermes 会话也可能已经建好；留着就会继续把结果喂给已回 IDLE 的控制器，
            # 甚至对着空 session_id 起轮。异常原样上抛，控制器照旧走它的失败路径。
            self._stop_asr()
            raise

    def set_uplink_open(self, open_: bool) -> None:
        """长按闸门。注意与豆包/Qwen 的区别：这里**不拦音频进 ASR**。

        本地 ASR 收不到音频就什么都识别不出来，所以闸门只拦「final 发给
        agent」这一步（_on_asr_result）。麦克风照常按 20ms 节奏喂帧——闸门关闭
        时由 send_audio 换成静音：既保住云引擎的 15 秒保活（H58），又不会把
        等待期的环境声识别成用户的话。逐字上屏照常显示。
        """
        self._uplink_open = bool(open_)
        if self._uplink_open:
            # 又按住了 = 新的一段话：上一句松手时留下的那次放行机会作废。
            # 不清的话它会在下一次「闸门关着时冒出来的 final」上被误用。
            self._release_final_pass = False

    def hold_end_user_turn(self) -> None:
        """松手：让 ASR 引擎立刻收束当前句（不支持 end_segment 的引擎由静音
        判停兜底，最多慢几百毫秒，不会丢话）。

        为什么还要留一次放行（_release_final_pass）：用户几乎都是说完最后一个
        字就松手，此刻本地 VAD 的静音窗（默认 600ms）远没走完，而控制器那边
        闸门已经合上——这一次 end_segment()（或云引擎自己按静音收束）产出的
        final 若被闸门一并拦掉，「按住说话」在 Hermes 上就等于什么都没说
        （文字上了屏、agent 永远收不到）。只放行**一条**，不重开闸门。
        """
        self._release_final_pass = True
        engine = self._asr
        end = getattr(engine, "end_segment", None)
        # 入口就打一行 INFO（而不是等 final 到了才打）：真机上「按住说话后 agent
        # 没反应」时，先靠它区分「松手通知根本没到客户端」与「引擎没产出 final」，
        # 顺带记下这次成句是靠引擎收束还是靠静音判停。
        logger.info("长按松手：通知客户端收束本轮（end_segment=%s）",
                    "有" if callable(end) else "无")
        if callable(end):
            try:
                end()
            except Exception:
                logger.warning("强制成句失败，回退本地静音判停", exc_info=True)
        with self._vad_lock:
            self._vad.reset()

    def send_audio(self, pcm: bytes) -> None:
        """上行一帧 16k/20ms PCM。半双工：请求在途期间**只补静音**，不丢帧。

        为什么不能像以前那样直接丢（H58，真机 2026-09-17 20:56 复现）：腾讯服务端
        15 秒收不到音频就断连（4008 客户端超过15秒未发送音频数据），而 Hermes 一轮
        可能跑工具、远超 15 秒（turn_timeout_ms 默认 60 秒）—— 丢帧会在思考中把 ASR
        打死，回到聆听后整段对话失聪（错误非可重连，控制器只弹一条横幅）。
        补静音既保活、又不会把等待期的话当成指令：喂真音频会让这一轮的 final 撞上
        「上一轮尚未结束」被丢掉。
        闸门期**不喂 VAD**：它只在真正聆听时有意义，而三条开闸路径
        （_on_client_event 的 AI_TURN_END / cancel_current_turn / _on_client_error）
        都会 _vad.reset()，残留状态必被清掉。
        """
        if self._asr is None:
            # ⚠️ 必须先判 None 再判闸门：stop() 会把 _asr 置 None（_stop_asr），而采集
            # 线程此后仍可能送帧。合并成 or 判定的话，闸门分支会拿 None 去调 send_audio。
            return
        if not self._uplink_open:
            # 长按闸门关闭：仍要喂音频，否则 15 秒静默会被云引擎断连（H58）。
            # 补静音而非真音频：等待期的环境声不该被识别成用户的话。
            self._asr.send_audio(
                np.zeros_like(np.frombuffer(pcm, dtype=np.int16)))
            return
        if self._turn_in_flight:
            self._asr.send_audio(
                np.zeros_like(np.frombuffer(pcm, dtype=np.int16)))
            return
        block = np.frombuffer(pcm, dtype=np.int16)
        # VAD 无锁：reset 若撞进 feed 的判停分支（长度判定与 concatenate 之间），
        # 异常会从采集线程抛出去（控制器那条路径没有 try）。
        with self._vad_lock:
            events = self._vad.feed(block)
        for kind, _value in events:
            if kind == EVENT_SPEECH_END:
                # 本地判停 → 让 ASR 引擎收束当前句、产出 final。
                # 不是所有引擎都提供 end_segment（用 getattr 探测）。
                end = getattr(self._asr, "end_segment", None)
                if callable(end):
                    end()
        self._asr.send_audio(block)

    def cancel_current_turn(self) -> None:
        """取消在途轮次：转发 /stop，并立刻解锁麦克风。

        先解锁再打 /stop：用户按取消的意图就是「我要重说」，让他立刻能开口
        比等一个网络往返重要；服务端的 run 由 /stop 异步收尾。
        """
        self._turn_in_flight = False
        with self._vad_lock:
            self._vad.reset()
        self._client.cancel()

    def stop(self) -> None:
        # 先挂牌再收场：引擎线程与 Hermes worker 都可能与 stop() 擦肩而过，收场后
        # 一律不再上抛（控制器的契约是「stop() 返回即已收场」，残留事件会把上一轮
        # 内容喂进已拆掉或新开的会话 —— 与 HermesClient._stopped 同一条理由）。
        self._stopped = True
        try:
            self._client.stop()
        finally:
            self._stop_asr()

    def _stop_asr(self) -> None:
        """停掉并丢弃 ASR 引擎（幂等）。stop() 与 start() 的失败回滚共用。

        M3：先原子摘牌再 stop —— 老写法「先 stop 后置 None」在重入/并发下会拿同一个
        引擎停两次（stop 若抛异常还会把 _asr 永远留住）。
        """
        engine, self._asr = self._asr, None
        if engine is None:
            return
        try:
            engine.stop()
        except Exception:
            logger.debug("ASR 引擎停止失败", exc_info=True)

    # ---- 子组件回调 ----

    def _on_asr_result(self, text: str, slice_type: int, index: int) -> None:
        # 收场后引擎线程仍可能漏来一帧：既不向上抛，更不能拿它去 start_turn ——
        # 对已收场的 HermesClient 起轮仍会 spawn worker 去 POST 一个正在关闭的会话。
        if self._stopped:
            return
        if not text:
            return
        is_final = slice_type == SLICE_FINAL
        self._notify_event(USER_TRANSCRIPT, {"text": text, "is_final": is_final})
        if not is_final:
            return
        if not self._uplink_open and not self._release_final_pass:
            # 闸门关闭：识别结果照常上屏（USER_TRANSCRIPT 已在上面发出），
            # 但不把这一句当成用户的指令发给 agent。
            logger.debug("长按闸门关闭，暂不提交本轮文本")
            return
        if self._release_final_pass:
            # 可观测（提到 INFO，生产日志级别就是 INFO）：真机上出现「按住说话后
            # agent 完全没反应」时，靠这行判断松手那一句到底有没有被提交。
            # 只打长度不打正文：用户语音内容不进日志。
            logger.info("长按松手：放行收束出的一句（len=%d）", len(text))
        # 放行位是一次性的：它专为「松手那一次成句」而设（见 hold_end_user_turn），
        # 用掉即恢复拦截，否则后续任何一条迟到 final 都会再消费它一次。
        self._release_final_pass = False
        # 「用户说完」在这里发出（spec §4.2 把 USER_TURN_END 归给本地 VAD 判停），
        # 但发在**起轮时**而不是 VAD 判停时：判停后 ASR 可能因垃圾音频不产出
        # final，控制器若据此进 THINKING 却没有任何在途轮次，就再也等不到出口。
        # 发在这里才能保证它与一次真实的 Hermes 轮次配对（控制器唯一的 THINKING
        # 入口就是它，不发则整个 5~8s 思考期界面停在「请说话」）。
        self._notify_event(USER_TURN_END, {})
        self._turn_in_flight = True        # 半双工闸门：此后丢帧
        self._client.start_turn(text)

    def _on_asr_error(self, code, message) -> None:
        if self._stopped:
            return
        self._notify_error(DialogErrorEvent(
            code, f"识别失败：{message}", "识别失败"))

    def _on_client_state(self, state: str) -> None:
        if self.on_state is not None:
            self.on_state(state)

    def _on_client_event(self, neutral: str, payload: dict) -> None:
        if self._stopped:
            return
        # AI 一轮结束 = 半双工闸门的下沿
        if neutral == AI_TURN_END:
            self._turn_in_flight = False
            with self._vad_lock:
                self._vad.reset()
        self._notify_event(neutral, payload)

    def _on_client_audio(self, pcm: bytes) -> None:
        """TTS 预留位：本次恒不触发（Hermes 不产音频，audio_api=false）。

        将来接 TTS 时，这里原样转发给控制器即可 —— 控制器的播放器、
        音频闸门、排空判定都是现成的，一行都不用改。
        """
        if self._stopped:
            return
        if self.on_audio is not None:
            self.on_audio(pcm)

    def _on_client_error(self, err) -> None:
        if self._stopped:
            return
        # 错误出口也必须开闸：HermesClient 有三条「只报错、永不来 AI_TURN_END」的
        # 出口 —— 起轮被拒（上一轮 worker 还活着）、初始 POST 失败、流中断/超时。
        # 闸门只由 AI_TURN_END 与 cancel_current_turn 释放，不在这里补一刀，
        # _turn_in_flight 就永远是真，send_audio 此后丢掉每一帧：一次网络抖动
        # 换来的是整场对话失聪，只能退出重进。
        # 唯一的例外是「/stop 失败」（code=CODE_CANCEL_FAILED）：它的 POST 最长要
        # connect_timeout_ms 才落地，完全可能在用户已经重新开口、新一轮已在途之后
        # 才回来 —— 那时开闸会让思考期的音频灌进 ASR，下一个 final 又撞上「上一轮
        # 尚未结束」被丢弃，白白丢一句话。其余错误都意味着本轮不会再有 AI_TURN_END，
        # 必须开闸（不开就是整场对话失聪）。错误照旧一律转发。
        if (self._turn_in_flight
                and getattr(err, "code", None) != CODE_CANCEL_FAILED):
            self._turn_in_flight = False
            with self._vad_lock:
                self._vad.reset()
        self._notify_error(err)

    # ---- 小工具 ----

    def _notify_event(self, neutral: str, payload: dict) -> None:
        if self.on_event is not None:
            self.on_event(neutral, payload)

    def _notify_error(self, err) -> None:
        if self.on_error is not None:
            self.on_error(err)
