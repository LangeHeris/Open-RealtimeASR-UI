"""LLM 修正替换逻辑：修正器创建、修正回调入队、主线程退格重写与延迟回车。

从 app.py 拆出：这组逻辑只围绕「识别结果上屏后由大模型修正再替换」
一条链路，读写的 app 状态（_pending_llm_replace / _session_seq /
_inject_hwnd / _deferred_enter 等）通过入参 app 访问；app 侧保留同名
薄包装方法，信号连接与调用点不变。
"""

from __future__ import annotations

import logging

from . import history as _history
from ..i18n import t

logger = logging.getLogger(__name__)


def setup_llm_corrector(app) -> None:
    """按 cfg.llm 配置创建 LLM 修正器；未启用或未填 key 则不创建。"""
    llm_cfg = getattr(app.cfg, "llm", None)
    if llm_cfg is None or not getattr(llm_cfg, "enable", False):
        return
    if not getattr(llm_cfg, "api_key", ""):
        logger.warning("LLM 修正已开启但 api_key 为空，跳过")
        app.bar.signals.transient_hint.emit(t("hint.llm_no_key"), 2.0)
        return
    from ..postprocess.llm_corrector import LLMCorrector
    # 历史上下文开关开启时注入 provider（取最近 15 条，与右键菜单展示数
    # 一致）；关闭传 None，corrector 不做历史拼接。_call_llm 在后台线程
    # 调 provider，history.entries 自带锁，线程安全
    history_provider = (
        (lambda: _history.entries()[:15])
        if getattr(llm_cfg, "history_context", False) else None
    )
    app._llm_corrector = LLMCorrector(
        base_url=getattr(llm_cfg, "base_url", "https://api.openai.com/v1"),
        api_key=llm_cfg.api_key,
        model=getattr(llm_cfg, "model", "gpt-4o-mini"),
        timeout_ms=getattr(llm_cfg, "timeout_ms", 3000),
        # 传原始配置值即可：空/None/旧版默认值都由 prompt_for_language
        # 按界面语言归一化（str() 兜住 yaml 给出非字符串的极端情况）
        prompt=str(getattr(llm_cfg, "prompt", "") or ""),
        disable_thinking=bool(getattr(llm_cfg, "disable_thinking", True)),
        batch_sentences=int(getattr(llm_cfg, "batch_sentences", 1) or 1),
        batch_idle_ms=int(getattr(llm_cfg, "batch_idle_ms", 1000) or 1000),
        history_provider=history_provider,
        on_corrected=app._on_llm_corrected,
        on_corrected_multi=app._on_llm_corrected_multi,
    )
    app._wait_for_correction = bool(getattr(llm_cfg, "wait_for_correction", False))
    logger.info("LLM 修正已启用：model=%s base=%s wait=%s",
                app._llm_corrector.model, app._llm_corrector.base_url,
                app._wait_for_correction)


def on_llm_corrected(
    app, original: str, corrected: str, requested_at: float = 0.0,
) -> None:
    """LLM 修正完成回调（后台线程）：单句路径，入队后经信号切主线程替换。

    corrected：与原文不同=有修正要替换；相同=无需替换；None=调用失败保留原文。
    requested_at 是发起时最后一句的上屏时刻，防替换期间又有新句上屏。
    """
    # 取 feed 时存的 wait 模式（key=requested_at）；找不到则用当前值兜底
    is_wait = app._feed_context.pop(requested_at, app._wait_for_correction)
    # 长按整段提交批次（原文从未上屏，feed 时登记了 requested_at）
    hold_batch = requested_at in app._hold_llm_batch_ts
    app._hold_llm_batch_ts.discard(requested_at)
    # 入队防并发覆盖，见 _pending_llm_replace 注释
    app._pending_llm_replace.append({
        "pairs": None,
        "original": original,
        "corrected": corrected,
        "seq": app._session_seq,
        "requested_at": requested_at,
        # 携带 feed 时的 wait 模式：回调前热切换了设置也按 feed 时处理，
        # 避免原文没上屏却走退格分支
        "wait": is_wait,
        # 长按整段提交：会话已切换时不能按普通 wait 直接丢弃（见 do_llm_replace_job）
        "hold_batch": hold_batch,
    })
    app.llm_replace_requested.emit()


def on_llm_corrected_multi(
    app, pairs: list, requested_at: float = 0.0,
) -> None:
    """LLM 修正完成回调：多句路径。pairs = [(原句, 修正句)]，顺序即上屏顺序。"""
    is_wait = app._feed_context.pop(requested_at, app._wait_for_correction)
    # 入队防并发覆盖，见 _pending_llm_replace 注释
    app._pending_llm_replace.append({
        "pairs": pairs,
        "original": None,
        "corrected": None,
        "seq": app._session_seq,
        "requested_at": requested_at,
        "wait": is_wait,  # 见 on_llm_corrected 注释
        "hold_batch": False,  # 多句批次不来自长按整段提交（见 on_llm_corrected）
    })
    app.llm_replace_requested.emit()


def do_llm_replace(app) -> None:
    """主线程槽：逐条执行排队中的修正替换，清空后尝试落延迟回车。"""
    while app._pending_llm_replace:
        job = app._pending_llm_replace.pop(0)
        do_llm_replace_job(app, job)
    maybe_fire_deferred_enter(app)


def do_llm_replace_job(app, job: dict) -> None:
    """处理单个修正任务。job 字段：pairs=[(原句,修正句)] 多句/None 单句；
    original+corrected 单句（None=失败，==original=已正确）；seq 发起时会话序号；
    requested_at 最后一句上屏时刻。
    多句从后往前替换：上屏后游标在末尾，倒着退格每句只删自己的字数。"""
    pairs = job.get("pairs")
    original = job.get("original")
    corrected = job.get("corrected")
    seq = job["seq"]
    last_final_at = job["requested_at"]
    # 用 feed 时的 wait 模式：热切换后 job 仍走正确分支，原文没上屏就不退格
    is_wait = job.get("wait", False)
    hold_batch = job.get("hold_batch", False)

    # 会话序号不符（打断后已开新会话）就取消替换：退格会误删新会话的输入
    if seq != app._session_seq:
        logger.info("会话已切换，取消旧会话的 LLM 修正替换")
        # 长按整段提交例外：原文从未上屏（整段走 wait 语义），照普通 wait 直接
        # 丢弃等于把用户刚说的整段话吃掉 —— 降级为注入原文，内容比"修得干净"重要
        if hold_batch and original:
            try:
                app.injector.inject(
                    original, is_final=True,
                    done_callback=app._record_inject_hwnd,
                )
                _history.add(original)
                app.bar.signals.transient_hint.emit(
                    t("hint.llm_failed_original"), 1.5)
                logger.info("长按整段修正被会话切换取消，降级上屏原文：%d字",
                            len(original))
            except Exception:
                logger.exception("长按整段降级上屏失败")
            return
        # 非 wait 原文已上屏：补记历史；wait 模式原文没上过屏，不记
        if not is_wait:
            history_record_on_screen(app, original, pairs)
        return

    # wait 模式：final 没上屏，直接 inject 修正版。无原文可删，只校验会话序号、
    # 打断标记和焦点（等待期间用户切了窗口就不打）
    if is_wait:
        # 这里只认「真打断」（删除键 / 左键点击），**不认** _discard_results：
        # 后者在手动停止时必然置位（stop_session 对无冲刷能力的引擎硬切断），
        # 而「修正后上屏」的回包必然晚于停止（长按整段提交更是必然落在停止
        # 之后）—— 用它判会把用户刚说完的话整段吃掉：原文从未上屏，历史里
        # 也查不到。打断后已开新会话的情况由上面的会话序号校验兜住。
        if getattr(app, "_session_interrupted", False):
            logger.info("会话被真打断，取消 wait 模式上屏")
            return
        # 焦点校验：识别时刻与现在前台窗口一致才上屏，用户切走了就不打
        #（宁可不注入也不打错窗口）；_inject_hwnd=0（拿不到句柄）时放行
        try:
            cur_hwnd = app.injector.current_window_hwnd()
        except Exception:
            cur_hwnd = 0
        if app._inject_hwnd and cur_hwnd != app._inject_hwnd:
            logger.info("等待修正期间焦点已切换（%s -> %s），放弃上屏",
                        app._inject_hwnd, cur_hwnd)
            # 不上屏不等于内容可以无痕消失：wait 模式原文从未上过屏，这一放弃就
            # 从屏幕到历史都没了痕迹。补记历史（历史开关关着时 add 内部忽略），
            # 用户至少还能从历史里把这段话找回来
            if pairs is not None:
                for orig_i, corr_i in pairs:
                    _history.add(corr_i if corr_i is not None else orig_i)
            elif original:
                _history.add(corrected if corrected is not None else original)
            app.bar.signals.transient_hint.emit(
                t("hint.llm_focus_switched"), 2.0)
            return
        try:
            if pairs is not None:
                # 多句：顺序注入修正版即可，无原文可删。
                # 修正版命令复检（纯探测）：触发词被听错（raw final
                # 未命中）而修正版拼回触发词的句子，作命令执行、
                # 不上屏不进历史。派发与注入同队列 FIFO，顺序=
                # 说话顺序（inject 与 send_key 交错正确）
                from . import voice_commands

                out_len = 0
                for orig_i, corr_i in pairs:
                    out = corr_i if corr_i is not None else orig_i
                    if (voice_commands.enabled()
                            and voice_commands.detect_prefix(out)[0]):
                        app._maybe_route_command(out)
                        continue
                    app.injector.inject(
                        out, is_final=True,
                        done_callback=app._record_inject_hwnd,
                    )
                    # 「删除那句」的退格基准：wait 模式原文从未上屏，基准只能在这里
                    # 补——否则它停在更早那次的长度上，删的是别的句子
                    app._last_spoken_text = out
                    # 历史只记修正后文本，原文不入历史
                    _history.add(out)
                    out_len += len(out)
                logger.info("LLM wait 多句上屏：%d句 %d字",
                            len(pairs), out_len)
                app.bar.signals.transient_hint.emit(
                    t("hint.llm_fixed_pairs", n=len(pairs), length=out_len), 1.5)
                return
            # 单句
            out = corrected if corrected is not None else original
            # 修正版命令复检：ASR 把触发词听错（如"之嘛开们"）时 raw
            # final 未命中、整句进了修正管道；修正版拼回触发词后在此
            # 补检。wait 模式原文从未上屏（选区完好），「帮我」AI 命令
            # 与本地命令均完整可用
            if app._maybe_route_command(out):
                return
            app.injector.inject(
                out, is_final=True,
                done_callback=app._record_inject_hwnd,
            )
            # 「删除那句」的退格基准：wait 模式原文从未上屏（长按整段提交也走这条），
            # 基准只能在这里补——否则它停在更早那次的长度上，删除键会删错对象
            app._last_spoken_text = out
            # 历史记最终上屏文本（修正成功=修正版，失败=原文，都入）
            _history.add(out)
            if corrected is None:
                app.bar.signals.transient_hint.emit(
                    t("hint.llm_failed_original"), 1.5)
            elif corrected == original:
                app.bar.signals.transient_hint.emit(
                    t("hint.llm_already_correct"), 1.0)
            else:
                app.bar.signals.transient_hint.emit(
                    t("hint.llm_fixed_len", length=len(out)), 1.5)
            logger.info("LLM wait 单句上屏：%d字", len(out))
        except Exception:
            logger.exception("wait 模式上屏失败")
        return

    # 新句检测：修正后又上屏了新 final，光标已后移，退格会删错对象，
    # 放弃替换（宁漏修不错乱）
    if app._last_final_at > last_final_at:
        logger.info("修正期间有新句子注入，取消本次替换")
        # 原文已在屏上：补记历史
        history_record_on_screen(app, original, pairs)
        return

    # 焦点校验：上屏后用户切走窗口，退格/粘贴落错窗口，取消替换
    try:
        cur_hwnd = app.injector.current_window_hwnd()
    except Exception:
        cur_hwnd = 0
    if app._inject_hwnd and cur_hwnd != app._inject_hwnd:
        logger.info("上屏后焦点已切换，取消本次替换")
        # 原文已在屏上：补记历史
        history_record_on_screen(app, original, pairs)
        return

    # 同 wait 分支：只认真打断。手动停止也会置 _discard_results，而回包照样可能
    # 落在停止之后 —— 用那个标记判，用户"说完就按停止"的那些句子会永远修不上
    #（原文已在屏上，不丢字，但 AI 修正静默失效）
    if getattr(app, "_session_interrupted", False):
        logger.info("会话被真打断，取消 LLM 修正替换")
        # 原文已在屏上：补记历史
        history_record_on_screen(app, original, pairs)
        return

    try:
        if pairs is not None:
            # 修正版命令复检（纯探测）：触发词被听错而修正版拼回触发词
            # 的句子，退格后作命令执行、不上屏不进历史
            from . import voice_commands

            cmd_flags = [
                voice_commands.enabled()
                and voice_commands.detect_prefix(corr_i)[0]
                for _, corr_i in pairs
            ]
            # 多句：从后往前退格重写，每句只删自己的字数；
            # 命令句只退格（听错版原文已上屏，删掉即可）
            for i in range(len(pairs) - 1, -1, -1):
                orig_i, corr_i = pairs[i]
                if cmd_flags[i]:
                    app.injector.replace(len(orig_i), "")
                else:
                    app.injector.replace(len(orig_i), corr_i)
            # 命令派发在退格入队之后：send_key 等键序才能落在删除之后
            for i, (_, corr_i) in enumerate(pairs):
                if cmd_flags[i]:
                    app._maybe_route_command(corr_i)
            in_len = sum(len(o) for o, _ in pairs)
            out_len = sum(len(c) for _, c in pairs)
            logger.info("LLM 多句修正已替换：%d句 %d字 -> %d字",
                        len(pairs), in_len, out_len)
            app.bar.signals.transient_hint.emit(
                t("hint.llm_fixed_pairs", n=len(pairs), length=out_len), 1.5
            )
            # 历史逐句记修正版（上屏顺序）；命令句不入历史
            for i, (orig_i, corr_i) in enumerate(pairs):
                if not cmd_flags[i]:
                    _history.add(corr_i if corr_i is not None else orig_i)
            return

        # ---- 单句路径 ----
        if corrected is None:
            logger.info("LLM 修正失败，保留原文")
            app.bar.signals.transient_hint.emit(t("hint.llm_failed_keep"), 1.5)
            # 失败：原文已在屏上就是最终文本，入历史
            _history.add(original)
            return
        if corrected == original:
            logger.debug("LLM 无修正（原文已正确）")
            app.bar.signals.transient_hint.emit(t("hint.llm_already_correct"), 1.0)
            # 结果==原文即 LLM 判定无需改动，照样入历史
            _history.add(corrected)
            return
        # 修正版命令复检：触发词被听错（raw final 未命中）而修正版拼回
        # 触发词时，删掉已上屏的听错文本后执行命令。先退格入队再派发：
        # send_key 等键序才能落在删除之后（注入队列 FIFO 保序）。非
        # wait 模式选区已被听错文本顶掉：AI 命令会提示"请先选中要处理
        # 的文字"，本地命令（发送/开关等）不受影响
        from . import voice_commands

        if (voice_commands.enabled()
                and voice_commands.detect_prefix(corrected)[0]):
            app.injector.replace(len(original), "")
            app._maybe_route_command(corrected)
            return
        # 退格删原文+重写修正版（不重置 _last_len，不影响后续中间结果逻辑）
        app.injector.replace(len(original), corrected)
        logger.info("LLM 修正已替换：%d字 -> %d字", len(original), len(corrected))
        app.bar.signals.transient_hint.emit(
            t("hint.llm_fixed_range", a=len(original), b=len(corrected)), 1.5
        )
        # 历史只记修正后文本（AI 修正前的原文不入历史）
        _history.add(corrected)
    except Exception:
        logger.exception("LLM 修正替换失败")


def maybe_fire_deferred_enter(app) -> None:
    """延迟回车落地：wait 模式「发送」等修正全部上屏后再回车。corrector 忙时
    等下一回调再试；回车排注入队列尾，FIFO 保证先文本后回车。"""
    if not app._deferred_enter:
        return
    if app._pending_llm_replace:
        return
    corrector = app._llm_corrector
    if corrector is not None and corrector.busy():
        return
    app._deferred_enter = False
    app._deferred_enter_timer.stop()
    app.injector.send_key("enter")
    logger.info("延迟回车已执行（wait 模式修正已上屏）")


def on_deferred_enter_timeout(app) -> None:
    """保险兜底：修正迟迟不回（网络挂起/回调丢失），超时仍执行回车。"""
    if app._deferred_enter:
        app._deferred_enter = False
        app.injector.send_key("enter")
        logger.info("延迟回车超时兜底执行")


def history_record_on_screen(app, original: str, pairs) -> None:
    """补记已上屏的原文：修正被取消时原文就是最终文本，防历史大量缺句。"""
    if pairs is not None:
        # 多句批：逐句记原文（上屏顺序）
        for orig_i, _ in pairs:
            _history.add(orig_i)
    elif original:
        _history.add(original)
