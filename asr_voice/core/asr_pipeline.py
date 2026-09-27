"""ASR 会话与音频管线：会话启停、引擎创建/预热、采集流管理与音频分发。

从 app.py 拆出：这条链路只围绕「开会话 -> 采集音频 -> 喂 ASR -> 停会话」，
读写的 app 状态（state / asr / capture / vad / _pending_audio 等）通过入参
app 访问；app 侧保留同名薄包装方法，热键与信号调用点不变。
管线专用常量（会话超时/建连重试/底噪校准等）仍定义在 app 模块，函数内
延迟导入避免与 app 的顶层互相引用成环。
"""

from __future__ import annotations

import logging
import threading
import time

from ..config.loader import is_configured
from ..i18n import t
from ..ui.floating_bar import UI_IDLE, UI_LISTENING, UI_RECOGNIZING

logger = logging.getLogger(__name__)


def decide_media_actions(cfg) -> tuple[bool, bool]:
    """按配置决定录音时要不要暂停播放/全局静音，返回 (do_pause, do_mute)。

    「仅游戏模式下静音」开启时：全局静音只在游戏模式也开着时执行，
    避免日常录音误静音音乐/视频；暂停播放不受此限制。
    """
    rec = getattr(cfg, "recording", None)
    do_pause = bool(getattr(rec, "pause_background_audio", False))
    do_mute = bool(getattr(rec, "mute_background_audio", False))
    if do_mute and bool(getattr(rec, "mute_only_game_mode", False)):
        inj = getattr(cfg, "injection", None)
        if not bool(getattr(inj, "game_mode", False)):
            do_mute = False
    return do_pause, do_mute


def _is_transient_connect_error(exc: Exception) -> bool:
    """建连失败是否瞬时网络问题（值得重试）：阿里云带 code="connect_timeout"，其它按超时字样兜底。"""
    if getattr(exc, "code", None) == "connect_timeout":
        return True
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg or "超时" in msg  # noqa: i18n  异常信息匹配目标


def _volc_resource_id(volc_cfg) -> str:
    """合成火山 resource_id：volc.{bigasr|seedasr}.sauc.{billing}。版本由前缀判定，
    计费取对应版本独立字段，切版本自动套用该版本的计费方式。"""
    cur = str(getattr(volc_cfg, "resource_id", "") or "")
    family = "seedasr" if "seedasr" in cur else "bigasr"
    billing = str(getattr(volc_cfg, f"{family}_billing", "") or "duration")
    if billing not in ("duration", "concurrent"):
        billing = "duration"
    return f"volc.{family}.sauc.{billing}"


def _guide_no_key_once(app) -> bool:
    """商店版「未配置密钥首次拒绝录音」的一次性引导，返回是否发了引导。

    只弹一次（标记落 steam_state.json 的 no_key_guided）：反复弹同一句
    说明会变成噪音，用户第一次没去设置，第二次多半也不会。
    开源版不走这条——它的用户是自己 clone 来跑的，本来就该去看 README。
    """
    from ..steam_integration import is_steam_build

    if not is_steam_build():
        return False
    from ..ui.welcome_dialog import (
        NO_KEY_GUIDE_KEY,
        is_guided,
        mark_guided,
    )

    if is_guided(NO_KEY_GUIDE_KEY):
        return False
    mark_guided(NO_KEY_GUIDE_KEY)
    app.bar.signals.error_occurred.emit(t("welcome.no_key_hint"))
    return True


def start_session(app) -> None:
    # 管线常量延迟导入：顶层导入会与 app 成环
    from .app import APP_STATE_IDLE, APP_STATE_LISTENING

    # 命令热键启动：会话建立后自动进待命态（免说触发词）。
    # 立即取出并清零，未配置/模型未就绪等早退路径不会把它泄漏给下次手动启动
    standby_start = bool(getattr(app, "_standby_start", False))
    app._standby_start = False

    if not is_configured(app.cfg):
        # 商店版首次被拒时给一次完整引导（说明本地引擎只能中文、云端要自带
        # 密钥），之后仍走下面的单行短提示——两条连着闪会互相顶掉，首次用
        # 长文案（语义已覆盖短提示）比叠两条更清楚。
        if _guide_no_key_once(app):
            return

        # 与引擎切换提示同规则：悬浮条单行右截断，只留「哪家 + 去哪填」，
        # 具体字段与服务页指引由设置界面的字段标签说明
        engine = getattr(app.cfg, "engine", "tencent")
        if engine == "funasr":
            # funasr 无密钥概念：未就绪只能是依赖不可用（打包版/未安装）
            app.bar.signals.error_occurred.emit(
                t("hint.funasr_dep_missing")
            )
        elif engine == "aliyun":
            app.bar.signals.error_occurred.emit(
                t("hint.no_key_aliyun")
            )
        elif engine == "xfyun":
            std = getattr(getattr(app.cfg, "xfyun", None), "edition", "llm") == "std"
            app.bar.signals.error_occurred.emit(
                t("hint.no_key_xfyun_std") if std else t("hint.no_key_xfyun_llm")
            )
        elif engine == "volcengine":
            app.bar.signals.error_occurred.emit(
                t("hint.no_key_volcengine")
            )
        else:
            app.bar.signals.error_occurred.emit(
                t("hint.no_key_tencent")
            )
        return

    logger.info("开始语音会话")

    cfg = app.cfg

    # FunASR：模型未就绪时拒绝录音（不阻塞主线程等待加载），
    # 加载完成后悬浮条会提示"已就绪"，用户再按热键开始
    is_funasr = getattr(cfg, "engine", "tencent") == "funasr"
    if is_funasr:
        inst = app._funasr_instance
        inst_loading = inst is not None and inst.is_loading()
        if inst is None or not inst.is_ready():
            if not inst_loading:
                # 未开始加载或上次加载失败：重新触发预热
                preheat_funasr(app)
            app.bar.signals.transient_hint.emit(
                t("hint.funasr_loading_hint"), 3.0
            )
            logger.info("FunASR 模型未就绪，本次录音请求已拒绝")
            app.state = APP_STATE_IDLE
            # 界面必须一起回待机：长按路径在调用本函数**之前**就发了 UI_LISTENING
            #（越过阈值即显示"聆听中"），这里只改 app.state 的话悬浮条会一直停在
            # 录音外观，而实际什么都没录。点按路径本来就待机，本 emit 是幂等空转
            app.bar.signals.state_changed.emit(UI_IDLE)
            return
    app.state = APP_STATE_LISTENING
    app._discard_results = False  # 新会话复位「停止后不再出字」标记
    app._session_interrupted = False  # 同时复位「真打断」标记（见 app.__init__ 注释）
    app._session_seq += 1         # 新会话序号（LLM 替换防旧会话误删）
    app.bar.signals.state_changed.emit(UI_LISTENING)
    app.bar.signals.level_changed.emit(0.0)
    # 硬超时计时起点 + 启动底噪校准（头 1 秒只采样，不喂 VAD）
    app._session_start_time = time.time()
    app._calib_energies = []

    from ..audio.vad import VAD  # 延迟导入：vad 顶层依赖 numpy
    app.vad = VAD(
        sample_rate=cfg.audio.sample_rate,
        block_size=cfg.audio.block_size,
        energy_threshold=cfg.vad.energy_threshold,
        silence_duration_ms=cfg.vad.silence_duration_ms,
        min_speech_ms=cfg.vad.min_speech_ms,
        pre_speech_buffer_ms=cfg.vad.pre_speech_buffer_ms,
    )

    # 建/取 ASR 客户端：腾讯云新建；FunASR 复用预热实例（create_asr 内绑回调）
    app.asr = create_asr(app, cfg)
    if app.asr is None:
        app.state = APP_STATE_IDLE
        app.bar.signals.state_changed.emit(UI_IDLE)
        return

    # 采集流：预热模式复用启动时打开的流，否则重建，重建期音频进缓冲；
    # 预滚一并回灌，连"按下热键前刚出口的首字"都补得上。建连（握手约 1~2 秒）
    # 放后台线程，就绪后补发缓冲，开头不丢字
    app._pending_audio = app._preroll.copy()
    try:
        ensure_capture(app)
    except Exception as exc:
        logger.error("音频采集启动失败：%s", exc)
        app.bar.signals.error_occurred.emit(t("hint.mic_start_failed", err=exc))
        stop_session(app)
        return

    # 暂停播放/全局静音：所有早退路径之后、采集流建立成功才生效，
    # 拒绝录音的场景不误中断后台音频；做什么由配置开关决定（含「仅游戏模式下静音」）
    do_pause, do_mute = decide_media_actions(cfg)
    if do_pause or do_mute:
        app.media_pauser.engage(do_pause=do_pause, do_mute=do_mute)

    threading.Thread(
        target=connect_asr_worker, args=(app,), daemon=True, name="asr-connect"
    ).start()

    # 超时计时从音频采集启动后开始，避免模型加载期间白白消耗超时额度
    app._last_voice_time = time.time()

    # 命令热键启动的会话：自动进命令待命态（下一句直接当命令，免说触发词）。
    # 放在所有早退路径之后，确保会话真实建立才进待命
    if standby_start:
        app._command_standby = True
        app.bar.signals.command_standby.emit(True)
        app.bar.signals.transient_hint.emit(
            t("hint.command_standby_ready"), 2.5)


def warmup_capture(app) -> None:
    """后台预热采集流：失败仅记日志，首次录音时会重试。"""
    try:
        ensure_capture(app)
    except Exception:
        logger.warning("麦克风预热失败（首次录音时会重试）", exc_info=True)


def stop_capture(app) -> None:
    """立即停采集流，切断音频供给。asr.stop() 对部分引擎是异步的，不停流的话
    停止后引擎还会收到残留语音回吐文字，像"点了停止还在录音"。"""
    with app._capture_lock:
        cap = app.capture
        app.capture = None
    if cap is not None:
        try:
            cap.stop()
        except Exception:
            logger.warning("停止采集流失败", exc_info=True)


def ensure_capture(app) -> None:
    """确保采集流可用：健康则复用，死亡则重建。健康 = 流在运行且（启动 3s 内或
    最近 2s 持续出块）；设备拔出时流对象还在但回调停摆，按死亡重建。"""
    from ..audio.capture import AudioCapture  # 延迟导入：sounddevice+numpy 较重

    with app._capture_lock:
        now = time.time()
        if app.capture is not None and app.capture.is_running():
            priming = now - app._capture_started_at <= 3.0
            delivering = (
                app._last_block_time > 0
                and now - app._last_block_time <= 2.0
            )
            if priming or delivering:
                return
            try:
                app.capture.stop()
            except Exception:
                logger.warning("关闭失效的采集流时出错", exc_info=True)
        cfg = app.cfg
        app.capture = AudioCapture(
            sample_rate=cfg.audio.sample_rate,
            channels=cfg.audio.channels,
            block_size=cfg.audio.block_size,
            dtype=cfg.audio.dtype,
            on_block=app._on_audio_block,
            device=getattr(cfg.audio, "input_device", "") or None,
        )
        app.capture.start()
        app._capture_started_at = now
        app._last_block_time = 0.0


def connect_asr_worker(app) -> None:
    """后台建连线程，握手期间录音已在进行、音频进缓冲。

    不在本线程补发音频：send_audio 统一由音频回调线程调用（连上后先补缓冲
    再发当前块），保证各引擎客户端无并发调用。瞬时超时自动重试
    （最多 3 次、间隔 2s），配置类错误不重试，直接报错停会话。
    """
    from .app import (
        APP_STATE_LISTENING,
        ASR_CONNECT_RETRY_DELAY,
        ASR_CONNECT_RETRY_MAX,
    )

    asr = app.asr
    if asr is None:
        return
    for attempt in range(1, ASR_CONNECT_RETRY_MAX + 1):
        # 快速连点时旧实例会延迟十几秒才抛错，校验 app.asr is asr，
        # 防旧实例失败误伤新会话
        if app.asr is not asr or app.state != APP_STATE_LISTENING:
            return
        try:
            asr.start()
            if attempt > 1:
                logger.info("ASR 连接恢复（第 %d 次尝试成功）", attempt)
            break
        except Exception as exc:
            transient = _is_transient_connect_error(exc)
            if transient and attempt < ASR_CONNECT_RETRY_MAX:
                logger.warning(
                    "ASR 建连瞬时超时（第 %d 次尝试），"
                    "%.1f 秒后自动重试：%s",
                    attempt, ASR_CONNECT_RETRY_DELAY, exc,
                )
                time.sleep(ASR_CONNECT_RETRY_DELAY)
                continue
            logger.error(
                "ASR 启动失败%s：%s",
                f"（已自动重试 {attempt - 1} 次）"
                if transient and attempt > 1 else "",
                exc,
            )
            if app.asr is asr and app.state == APP_STATE_LISTENING:
                dialog_msg = t("hint.connect_failed", err=exc)
                if transient and attempt > 1:
                    dialog_msg += t("hint.retry_suffix", n=attempt - 1)
                app.bar.signals.error_occurred.emit(dialog_msg)
                stop_session(app)
            return
    # 握手期间会话已取消：关掉刚建好的连接防泄漏（讯飞按并发限流，泄漏会被拒连）
    if app.asr is not asr or app.state != APP_STATE_LISTENING:
        logger.info("会话已取消，关闭刚建立的 ASR 连接")
        try:
            asr.stop()
        except Exception:
            logger.exception("关闭已取消会话的 ASR 连接失败")
        return
    logger.info(
        "ASR 连接就绪（预缓冲 %d 块，约 %.1f 秒）",
        len(app._pending_audio), len(app._pending_audio) * 0.1,
    )


def session_stop_from_audio(app) -> None:
    """音频回调线程的停止入口。回调线程内 stream.stop() 会卡 1~2 秒，期间音频
    继续喂 ASR 回吐文字；放独立线程，回调立刻返回、停止立即生效。"""
    from .app import APP_STATE_LISTENING

    with app._lock:
        if app.state == APP_STATE_LISTENING:
            stop_session(app)


def stop_session(app) -> None:
    from .app import APP_STATE_IDLE, APP_STATE_STOPPING

    logger.info("停止语音会话")
    # 打断路径（"删除那句"/点击打断）在调用前已预置 _discard_results=True，
    # 正常停止（热键再按 / 静音自动停 / 超时）进来时仍是 False
    interrupted = app._discard_results
    # 自动停止（静音超时/时长上限）放行冲刷 final：停止时在途的尾句 final
    #（各引擎停止冲刷的残留句）放行上屏。手动打断维持硬切断（防"点了停止还在出字"）
    auto_stop = getattr(app, "_auto_stop_pending", False)
    app._auto_stop_pending = False
    # 本地引擎（FunASR）没有服务端断句，stop() 同步冲刷的 FINAL 就是用户
    # 最后说的那句话——正常停止必须放行上屏（"停止即上屏"），否则表现
    # "悬浮条有字但输入框没进去"。云端引擎与打断停止维持硬切断：
    # 停止后引擎冲刷的中间帧/尾句 FINAL/排队注入一律不上屏，
    # 否则表现"点了停止还在出字"
    # 腾讯 preview 分支现在也有 end_segment（客户端切句），判别改用显式标记
    is_funasr = app.asr is not None and bool(getattr(app.asr, "sync_stop_flush", False))
    # 腾讯 Hy-ASR preview 分支：服务端无断句，end 冲刷的 final 才携带最后一
    # 句文本——短句（如两个字）整句都在尾部，正常停止不放行的话整句永远
    # 进不了输入框（"识别了但一直不发送"），按 FunASR 同语义放行"停止即上屏"
    preview_flush = app.asr is not None and bool(
        getattr(app.asr, "flush_tail_on_stop", False)
    )
    flush_on_stop = is_funasr or preview_flush or auto_stop
    if not (flush_on_stop and not interrupted):
        app._discard_results = True
    app.state = APP_STATE_STOPPING
    # 停录即取消命令待命态（残留待命会让下一会话首句被误当命令）
    if app._command_standby:
        app._command_standby = False
        app.bar.signals.command_standby.emit(False)
    # 停录即取消延迟回车：未上屏修正已被丢弃标记拦下，再回车只发空消息
    if app._deferred_enter:
        app._deferred_enter = False
        app._deferred_enter_timer.stop()

    # 先置 IDLE 再做可能阻塞的 stop：阻塞期间 UI 的 flash 过期会把
    # "正在聆听..."画出来，造成闪烁
    app.bar.signals.state_changed.emit(UI_IDLE)
    app.bar.signals.level_changed.emit(0.0)
    # 清空悬浮条上的识别文字残留，回到待机提示
    app.bar.signals.text_updated.emit("", False)

    # 立即停采集流：asr.stop() 异步期间没有新音频可回吐，杜绝"停止后继续录音"
    stop_capture(app)
    if app.asr:
        try:
            # connecting 时强行 stop() 会 kill 事件循环、建连线程卡满 15s 超时
            #（快速连点的异常根源）；未连上就跳过，交给建连线程兜底关连接
            if getattr(app.asr, "state", "") != "connecting":
                app.asr.stop()
            else:
                logger.info("ASR 连接尚未建立，交由建连线程收尾")
        except Exception:
            logger.exception("ASR 停止异常")
        app.asr = None

    # 停止完成后再清一次悬浮条：停止期间放行的冲刷 final（自动停止尾句、
    # FunASR 同步冲刷、腾讯 preview end 冲刷）会把文字写回顶部已清过的
    # 悬浮条，不补清会残留"按钮已回待机、文字停在最后一句"。仅放行冲刷
    # 的路径需要补清：其他引擎手动停止走硬切断（冲刷帧被丢弃），不触碰
    if not interrupted and flush_on_stop:
        app.bar.signals.text_updated.emit("", False)

    # 放行冲刷的路径：冲刷 FINAL 的 text_updated 会把悬浮条状态改回
    # "识别中"（状态点/声纹图标停在录音外观），冲刷完成后必须再发一次
    # IDLE 恢复待机态（云端/打断路径此前已是待机，无需重复）
    if flush_on_stop and not interrupted:
        app.bar.signals.state_changed.emit(UI_IDLE)

    # 退格掉未断句的中间文本：仅当「有 final 会来替换它」（冲刷上屏）。
    # 打断路径（左键/删除键）保留已上屏文字——用户看到的即所得，
    # 与「丢弃未上屏文字」日志承诺一致（cancel_pending 已丢弃队列中
    # 未执行的任务）。FunASR 等引擎中间结果是会话累积全文，打断时
    # 退格 _last_len 会删掉整个会话已上屏的字（2026-09-09 用户报障）。
    # 无冲刷能力的引擎正常停止时同样保留——没有 final 会来补，
    # 退格删掉就等于整句凭空消失
    if flush_on_stop and not interrupted:
        try:
            app.injector.cancel_intermediate()
        except Exception:
            logger.exception("取消中间结果失败")
    app.injector.reset()
    # 会话结束立即冲刷 LLM 修正队列（无新 FINAL，替换安全）
    if app._llm_corrector is not None:
        try:
            app._llm_corrector.flush_pending()
        except Exception:
            logger.exception("会话结束冲刷 LLM 修正队列失败")
    app._pending_audio = []
    app.vad = None
    app.state = APP_STATE_IDLE

    # 预热模式：会话结束后后台重建采集流，下次录音 0ms 就绪
    if getattr(getattr(app.cfg, "audio", None), "warmup_capture", True):
        threading.Thread(
            target=warmup_capture, args=(app,), daemon=True, name="capture-rewarm"
        ).start()

    # 暂停播放/全局静音：无条件恢复本会话动过的（内部无状态时为空操作）
    app.media_pauser.resume()


# ---- 引擎创建 / 预热 ----


def create_asr(app, cfg):
    """按 engine 创建 ASR 客户端，失败返回 None 并提示。引擎类延迟导入。"""
    engine = getattr(cfg, "engine", "tencent")
    if engine == "funasr":
        from ..asr.funasr_realtime import FunASRRealtime
        # 复用预热的实例（含加载锁），避免重复加载
        if app._funasr_instance is not None:
            asr = app._funasr_instance
            # 更新为会话回调（预热时只设了 progress/error）
            asr.on_result = app._on_asr_result
            asr.on_error = app._on_asr_error
            asr.on_state = app._on_asr_state
            asr.on_progress = app._on_funasr_progress
            if asr.is_ready():
                logger.info("复用已预热的 FunASR 模型")
            else:
                logger.info("预热未完成，会话将等待加载完成")
            return asr
        # 无预热实例：首次录音时同步创建并加载（存入 _funasr_instance 防重复加载）
        asr = FunASRRealtime(
            model=cfg.funasr.model,
            device=cfg.funasr.device,
            hotword=cfg.funasr.hotword,
            chunk_preset=getattr(cfg.funasr, "chunk_preset", "默认"),  # noqa: i18n  chunk_preset 持久化默认值
            local_dir=getattr(cfg.funasr, "local_dir", ""),
            model_hub=getattr(cfg.funasr, "model_hub", "ms"),
            model_revision=getattr(cfg.funasr, "model_revision", ""),
            on_result=app._on_asr_result,
            on_error=app._on_asr_error,
            on_state=app._on_asr_state,
            on_progress=app._on_funasr_progress,
        )
        app._funasr_instance = asr
        app.bar.signals.error_occurred.emit(t("hint.funasr_loading"))
        return asr

    if engine == "aliyun":
        from ..asr.aliyun_realtime import AliyunRealtimeASR
        a = getattr(cfg, "aliyun", None)
        return AliyunRealtimeASR(
            api_key=getattr(a, "api_key", ""),
            model=getattr(a, "model", "paraformer-realtime-v2"),
            sample_rate=cfg.audio.sample_rate,
            disfluency_removal=bool(getattr(a, "disfluency_removal", False)),
            max_sentence_silence=int(getattr(a, "max_sentence_silence", 800) or 800),
            vocabulary_id=getattr(a, "vocabulary_id", ""),
            language_hints=getattr(a, "language_hints", ""),
            on_result=app._on_asr_result,
            on_error=app._on_asr_error,
            on_state=app._on_asr_state,
        )

    if engine == "xfyun":
        from ..asr.xfyun_realtime import XfyunRealtimeASR, XfyunStdASR
        x = getattr(cfg, "xfyun", None)
        if getattr(x, "edition", "llm") == "std":
            # 标准版（rtasr v1）：lang/pd/punc 为标准版专用参数
            return XfyunStdASR(
                app_id=getattr(x, "std_app_id", ""),
                api_key=getattr(x, "std_api_key", ""),
                lang=getattr(x, "std_lang", "cn"),
                pd=getattr(x, "pd", ""),
                punc=bool(getattr(x, "std_punc", False)),
                filter_modal=bool(getattr(x, "std_filter_modal", False)),
                on_result=app._on_asr_result,
                on_error=app._on_asr_error,
                on_state=app._on_asr_state,
            )
        # 大模型版（rtasr_llm）：无 pd/punc 参数，仅 lang + 语气词过滤
        return XfyunRealtimeASR(
            app_id=getattr(x, "app_id", ""),
            api_key=getattr(x, "api_key", ""),
            api_secret=getattr(x, "api_secret", ""),
            lang=getattr(x, "lang", "autodialect"),
            filter_modal=bool(getattr(x, "filter_modal", False)),
            on_result=app._on_asr_result,
            on_error=app._on_asr_error,
            on_state=app._on_asr_state,
        )

    if engine == "volcengine":
        from ..asr.volcengine_realtime import VolcengineRealtimeASR
        v = getattr(cfg, "volcengine", None)
        # 按当前版本及其独立计费字段合成
        return VolcengineRealtimeASR(
            api_key=getattr(v, "api_key", ""),
            resource_id=_volc_resource_id(v),
            app_id=getattr(v, "app_id", ""),
            access_token=getattr(v, "access_token", ""),
            enable_ddc=bool(getattr(v, "enable_ddc", False)),
            enable_nonstream=bool(getattr(v, "enable_nonstream", True)),
            on_result=app._on_asr_result,
            on_error=app._on_asr_error,
            on_state=app._on_asr_state,
        )

    # 默认腾讯云
    from ..asr.tencent_realtime import TencentRealtimeASR
    app.asr = TencentRealtimeASR(
        app_id=cfg.tencent.app_id,
        secret_id=cfg.tencent.secret_id,
        secret_key=cfg.tencent.secret_key,
        engine_model_type=cfg.tencent.engine_model_type,
        filter_punc=cfg.tencent.filter_punc,
        convert_num_mode=cfg.tencent.convert_num_mode,
        filter_dirty=cfg.tencent.filter_dirty,
        filter_modal=cfg.tencent.filter_modal,
        hotword_id=cfg.tencent.hotword_id,
        customization_id=cfg.tencent.customization_id,
        on_result=app._on_asr_result,
        on_error=app._on_asr_error,
        on_state=app._on_asr_state,
    )
    return app.asr


def preheat_funasr(app) -> None:
    """后台预热 FunASR 模型，避免首次录音卡顿；加载完置 _funasr_instance，用户切走后结果丢弃。"""
    try:
        from ..asr.funasr_realtime import FunASRRealtime
    except Exception:
        logger.debug("FunASR 模块不可用，跳过预热")
        return

    engine = getattr(app.cfg, "engine", "tencent")
    if engine != "funasr":
        return

    preheat = getattr(app.cfg.funasr, "preheat", True)
    if not preheat:
        return

    # 预热代号：参数变更/引擎切换会弃旧起新，旧线程完成时发现代号过期
    # 即静默退出，避免扰动新加载的 spinner/就绪等 UI 状态
    app._funasr_gen = getattr(app, "_funasr_gen", 0) + 1
    gen = app._funasr_gen

    # 悬浮条进入"模型加载中"状态（完成/失败后恢复默认提示）
    app.bar.signals.model_loading_changed.emit(True)

    # 看门狗：加载线程卡死（网络挂起/模型损坏）不能让人等，超时强制退出加载态
    loaded = threading.Event()

    def _load():
        try:
            tmp = FunASRRealtime(
                model=app.cfg.funasr.model,
                device=app.cfg.funasr.device,
                hotword=app.cfg.funasr.hotword,
                chunk_preset=getattr(app.cfg.funasr, "chunk_preset", "默认"),  # noqa: i18n  chunk_preset 持久化默认值
                local_dir=getattr(app.cfg.funasr, "local_dir", ""),
                model_hub=getattr(app.cfg.funasr, "model_hub", "ms"),
                model_revision=getattr(app.cfg.funasr, "model_revision", ""),
                on_progress=app._on_funasr_progress,
                on_error=app._on_asr_error,
            )
            # 存实例引用供 create_asr 复用（含加载锁），避免预热未完成又开新实例
            app._funasr_instance = tmp
            if tmp.load_model():
                if getattr(app, "_funasr_gen", 0) != gen:
                    logger.debug("FunASR 预热已被更新的预热取代，静默丢弃结果")
                    return
                logger.info("FunASR 模型预热完成")
                app.bar.signals.model_loading_changed.emit(False)
                # 加载期间可能已切走：就绪提示只对 FunASR 引擎显示
                still_funasr = getattr(app.cfg, "engine", "tencent") == "funasr"
                app.bar.signals.model_ready_changed.emit(still_funasr)
            else:
                if getattr(app, "_funasr_gen", 0) != gen:
                    return
                app.bar.signals.model_loading_changed.emit(False)
                app.bar.signals.model_ready_changed.emit(False)
                # 仅 FunASR 引擎时报失败，切走后与用户无关
                if getattr(app.cfg, "engine", "tencent") == "funasr":
                    app.bar.signals.error_occurred.emit(t("hint.funasr_load_failed"))
        except Exception as exc:
            # funasr 未安装/构造即抛等异常路径：必须退出加载态，否则 spinner 永久空转
            logger.exception("FunASR 预热异常")
            if getattr(app, "_funasr_gen", 0) != gen:
                return
            app.bar.signals.model_loading_changed.emit(False)
            app.bar.signals.model_ready_changed.emit(False)
            if getattr(app.cfg, "engine", "tencent") == "funasr":
                app.bar.signals.error_occurred.emit(t("hint.funasr_preheat_failed", err=exc))
        finally:
            loaded.set()

    def _watchdog():
        if not loaded.wait(180):
            logger.error("FunASR 模型加载超时（180 秒无结果）")
            if getattr(app, "_funasr_gen", 0) != gen:
                return
            app.bar.signals.model_loading_changed.emit(False)
            app.bar.signals.model_ready_changed.emit(False)
            if getattr(app.cfg, "engine", "tencent") == "funasr":
                app.bar.signals.error_occurred.emit(
                    t("hint.funasr_load_timeout")
                )

    threading.Thread(target=_load, daemon=True, name="funasr-preheat").start()
    threading.Thread(target=_watchdog, daemon=True, name="funasr-watchdog").start()


# ---- 音频处理 ----


def on_audio_block(app, block) -> None:
    from .app import (
        APP_STATE_LISTENING,
        LEVEL_NORMALIZE,
        MAX_SESSION_SECONDS,
        NOISE_CALIB_BLOCKS,
    )

    # 任何状态都更新预滚缓冲和流健康标记（后者供 ensure_capture 判流死活）
    app._preroll.append(block)
    app._last_block_time = time.time()
    if app.state != APP_STATE_LISTENING:
        return

    now = time.time()

    # 硬超时兜底强制停止（时长可配，0=不限时）。回调线程里 stream.stop() 会卡 1~2 秒，停止放独立线程
    max_secs = int(getattr(app.cfg.recording, "max_session_seconds",
                           MAX_SESSION_SECONDS) or 0)
    if max_secs > 0 and now - app._session_start_time > max_secs:
        logger.info("录音达到 %d 秒上限，强制停止", max_secs)
        app.bar.signals.transient_hint.emit(t("hint.max_reached"), 1.5)
        app._auto_stop_pending = True  # 自动停止放行冲刷 final（见 stop_session）
        threading.Thread(
            target=session_stop_from_audio, args=(app,), daemon=True, name="audio-stop"
        ).start()
        return

    # 校准期只采样不喂 VAD（防底噪触发 SPEECH_START 卡说话状态），音频照发
    if app._calib_energies is not None:
        rms = app.vad.compute_rms(block) if app.vad else 0.0
        app._calib_energies.append(rms)
        app.bar.signals.level_changed.emit(min(1.0, rms / LEVEL_NORMALIZE))
        if len(app._calib_energies) >= NOISE_CALIB_BLOCKS:
            finish_noise_calibration(app)
        send_audio_with_buffer(app, block)
        return

    has_voice_activity = False
    speech_ended = False

    if app.vad:
        from ..audio.vad import (  # 延迟导入（录音开始后必然已缓存，仅字典查找）
            EVENT_LEVEL,
            EVENT_SPEECH_END,
            EVENT_SPEECH_START,
            EVENT_SPEECH_TOO_SHORT,
        )
        for event_type, value in app.vad.feed(block):
            if event_type == EVENT_LEVEL:
                app.bar.signals.level_changed.emit(
                    min(1.0, float(value) / LEVEL_NORMALIZE)
                )
            elif event_type == EVENT_SPEECH_START:
                has_voice_activity = True
                app.bar.signals.state_changed.emit(UI_RECOGNIZING)
            elif event_type == EVENT_SPEECH_END:
                has_voice_activity = True
                speech_ended = True
                if app.state == APP_STATE_LISTENING:
                    app.bar.signals.state_changed.emit(UI_LISTENING)
            elif event_type == EVENT_SPEECH_TOO_SHORT:
                has_voice_activity = True

    # 超时计时只认语音事件和 ASR 吐字两类硬证据。VAD 的 SPEAKING 状态不算：
    # 底噪贴阈值时"连续静音 600ms"永远凑不齐，VAD 会永久卡在 SPEAKING 永不超时
    if has_voice_activity:
        app._last_voice_time = now

    # 自动停止检测（停止同样放独立线程，理由同硬超时）。
    # 长按模式下豁免：用户一直按着 = 他明确在说，中间的停顿不该被打断
    # （spec §3.2）；60 秒硬上限不豁免，仍在上面生效，防跑飞。
    if (
        app.auto_stop_seconds > 0
        and not bool(getattr(app, "_hold_active", False))
        and app.state == APP_STATE_LISTENING
        and now - app._last_voice_time > app.auto_stop_seconds
    ):
        logger.info("无语音 %.0f 秒，自动停止", now - app._last_voice_time)
        app.bar.signals.transient_hint.emit(t("hint.silence_timeout"), 1.0)
        app._auto_stop_pending = True  # 自动停止放行冲刷 final（见 stop_session）
        threading.Thread(
            target=session_stop_from_audio, args=(app,), daemon=True, name="audio-stop"
        ).start()
        return

    send_audio_with_buffer(app, block)

    # 无服务端断句的引擎（FunASR、腾讯 preview 分支）靠本地 VAD 停顿切句；
    # 发送完本块再切，避免切断音频。其余云端引擎无 end_segment，走服务端分句
    if speech_ended and app.asr is not None and hasattr(app.asr, "end_segment"):
        try:
            app.asr.end_segment()
        except Exception:
            logger.exception("FunASR 切句失败")


def send_audio_with_buffer(app, block) -> None:
    """发音频块：未连上先入缓冲，连上后先补发缓冲再发当前块。只在音频回调线程
    调用，对引擎无并发；缓冲封顶约 20 秒防建连挂起吃内存。"""
    from .app import PENDING_AUDIO_MAX_BLOCKS

    if app.asr and app.asr.is_connected():
        if app._pending_audio:
            for buffered in app._pending_audio:
                app.asr.send_audio(buffered)
            app._pending_audio = []
        app.asr.send_audio(block)
    elif len(app._pending_audio) < PENDING_AUDIO_MAX_BLOCKS:
        app._pending_audio.append(block)


def finish_noise_calibration(app) -> None:
    """底噪校准收尾：阈值 = 中位数×1.8，封顶配置阈值×3，只抬不降。底噪贴近/
    超过阈值时 VAD 会永远判"说话中"导致永不超时，抬阈值让静音可判；封顶防
    校准期用户开口把阈值抬过头。"""
    from .app import NOISE_CALIB_FACTOR, NOISE_CALIB_MAX_RATIO

    energies = app._calib_energies or []
    app._calib_energies = None
    if not energies or app.vad is None:
        return
    energies.sort()
    noise_floor = energies[len(energies) // 2]  # 中位数，抗偶发说话干扰
    cfg_threshold = app.vad.energy_threshold
    target = min(
        noise_floor * NOISE_CALIB_FACTOR,
        cfg_threshold * NOISE_CALIB_MAX_RATIO,
    )
    if target > cfg_threshold:
        app.vad.energy_threshold = float(target)
        logger.info(
            "底噪校准：环境底噪=%.0f，VAD 阈值 %.0f -> %.0f",
            noise_floor, cfg_threshold, target,
        )
    else:
        logger.info(
            "底噪校准：环境底噪=%.0f，阈值 %.0f 无需调整",
            noise_floor, cfg_threshold,
        )
