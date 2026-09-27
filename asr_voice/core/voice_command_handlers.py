"""语音命令处理器：命令注册、路由、待命态、命令热键、AI 命令、开关逻辑。

从 app.py 拆出：这条链路只围绕「语音命令的 App 侧处理」——配置同步、
动作接线、命令路由、待命态管理、命令热键注册/触发、AI 命令链、开关逻辑。
读写 app 状态通过入参 app 访问；app 侧保留同名薄包装方法，信号与调用点不变。

循环依赖：app 顶层 import 本模块，本模块顶层 import app 的常量（纯字符串，
不触发类实例化）；其余 app 方法/状态一律通过 app 参数访问，与 asr_pipeline
一致的延迟导入策略。
"""

from __future__ import annotations

import logging
import time

from ..i18n import t
from .app import APP_STATE_IDLE, APP_STATE_LISTENING

logger = logging.getLogger(__name__)

# 语音命令的默认触发词 / AI 前缀兜底值（同 app.py 的 DEFAULT_CMD_PREFIX）。
# 与 config/defaults.py 的 commands.prefix / ai_prefix 同源 —— 这是「用户要
# 说出的词」而不是界面文案：翻成英文会让兜底值与配置文件里的默认值不一致。
DEFAULT_CMD_PREFIX = "听我说"      # noqa: i18n
DEFAULT_CMD_AI_PREFIX = "帮我"     # noqa: i18n


def sync_config(app) -> None:
    """把 commands 配置节同步进 voice_commands 模块（启动/热加载）。"""
    from . import voice_commands

    cmd_cfg = getattr(app.cfg, "commands", None)
    voice_commands.set_config(
        enabled=bool(getattr(cmd_cfg, "enable", False)),
        prefix=str(getattr(cmd_cfg, "prefix", DEFAULT_CMD_PREFIX)
                   or DEFAULT_CMD_PREFIX),
        ai_prefix=str(getattr(cmd_cfg, "ai_prefix", DEFAULT_CMD_AI_PREFIX)
                      or DEFAULT_CMD_AI_PREFIX),
        phrases_enabled=bool(getattr(cmd_cfg, "phrases_enable", False)),
        ai_thinking=bool(getattr(cmd_cfg, "ai_thinking", True)),
        ai_prompt_template=str(getattr(cmd_cfg, "ai_prompt_template", "") or ""),
    )


def register_handlers(
    app,
    keep_clipboard,
    live_intermediate,
    llm_enable,
    interrupt_delete,
) -> None:
    """本地命令表的动作接线：复用现有槽（与右键菜单同一入口）。

    开关类 handler 以 bound method 传入（签名 handler(bool)），
    保证「关剪贴板」等反向命令能正确传 False。
    """
    from . import voice_commands

    voice_commands.register_handlers({
        "enter": lambda: app.injector.send_key("enter"),
        "delete_last": lambda: command_delete_last(app),
        "select_all": lambda: app.injector.send_key("ctrl+a"),
        "undo": lambda: app.injector.send_key("ctrl+z"),
        "backspace": lambda: app.injector.send_key("backspace"),
        "stop_record": lambda: app._on_toggle(),
        "method_keys": lambda: app._on_injection_method_changed("simulate_keys"),
        "method_clip": lambda: app._on_injection_method_changed("clipboard_paste"),
        "keep_clipboard": keep_clipboard,
        "live_intermediate": live_intermediate,
        "llm_enable": llm_enable,
        "interrupt_delete": interrupt_delete,
    })


def command_delete_last(app) -> None:
    """「删除那句」：退格删掉刚上屏的最后一句 final。"""
    n = len(app._last_spoken_text)
    if n == 0:
        app.bar.signals.transient_hint.emit(t("hint.no_last_sentence"), 2.0)
        return
    # 与「发送」同理：删除会移动光标，取消在途修正的替换防误删
    app._last_final_at = time.time()
    app.injector.replace(n, "")
    app._last_spoken_text = ""


def maybe_route_command(app, text: str) -> bool:
    """命令检测，命中返回 True（调用方停止常规上屏/注入）。判定顺序：
    待命态整句即命令 > 句首触发词（有余文即命令，无余文进待命）。

    两个调用点：ASR 线程 raw final（_on_asr_result）；主线程修正版
    文本（_do_llm_replace_job 命令复检——触发词被听错时 raw 未命中、
    整句进了修正管道，修正版拼回触发词后补检）。主线程调用时各信号
    直连当轮执行；涉及注入队列的动作（send_key/replace）与先前提交
    的退格任务按 FIFO 保序（调用方需先入队退格再调本函数）。"""
    if not text:
        return False
    from . import voice_commands

    if not voice_commands.enabled():
        return False
    # 待命态：本句整体就是命令（单说触发词后的下一句）
    if app._command_standby:
        app._command_standby = False
        app.bar.signals.command_standby.emit(False)
        # 命令句中间帧只拦了注入没拦显示，final 被吞后清掉，别残留"发送"等字样
        app.bar.signals.text_updated.emit("", False)
        if not voice_commands.is_valid_command_text(text):
            # 纯标点/噪声：不当命令也不上屏，避免待命句误上屏
            return True
        dispatch_command(app, text)
        return True
    # 仅句首触发词生效，句中忽略防口述内容误触发
    hit, rest = voice_commands.detect_prefix(text)
    if not hit:
        return False
    app.bar.signals.text_updated.emit("", False)
    # 退格清掉触发词句可能的中间帧残留：同音误识别（如"之嘛开们"）
    # 时 may_be_command_start 拦不住、半截字已同步进输入框；不清掉
    # 的话，随后待命态的「发送」回车会把残字一起发出去。
    # 无残留时是 no-op（_last_len=0）
    app.injector.cancel_intermediate()
    if rest:
        dispatch_command(app, rest)
    else:
        app._command_standby = True
        app.bar.signals.command_standby.emit(True)
    return True


def dispatch_command(app, command_text: str) -> None:
    """ASR 线程：路由命令文本，queued signal 切主线程执行。"""
    from . import voice_commands

    kind, payload, arg = voice_commands.route(command_text)
    if kind == "local":
        logger.info("语音命令(本地)：%s -> %s %s", command_text, payload, arg)
        app.bar.signals.command_action.emit(payload, arg)
    elif kind == "phrase":
        logger.info("自定义短语命中：%r -> %d字", command_text, len(payload))
        app.bar.signals.command_phrase.emit(payload)
    elif kind == "ai":
        logger.info("语音命令(AI)：%r", command_text)
        app.bar.signals.command_ai.emit(payload)
    else:
        logger.info("语音命令未识别：%r", command_text)
        app.bar.signals.transient_hint.emit(
            t("hint.command_not_understood", prefix=DEFAULT_CMD_AI_PREFIX), 2.5
        )


def on_command_standby(app, active: bool) -> None:
    """主线程槽：待命态定时器启停（QTimer 只能主线程碰；ASR 线程只发信号不碰定时器）。"""
    if active:
        seconds = int(getattr(getattr(app.cfg, "commands", None),
                              "standby_seconds", 8) or 8)
        seconds = max(2, min(seconds, 60))
        app._standby_timer.start(seconds * 1000)
    else:
        app._standby_timer.stop()


def on_standby_timeout(app) -> None:
    """待命超时（主线程）：复位标志并提示。"""
    if app._command_standby:
        app._command_standby = False
        app.bar.signals.command_standby.emit(False)
        app.bar.signals.transient_hint.emit(
            t("hint.command_standby_cancelled"), 1.5)


def update_cmd_hotkey_listener(app) -> None:
    """按配置注册/更新/注销命令待命热键（启动、热加载、命令开关时调用）。

    启用条件：语音命令开启 且 commands.hotkey 非空。热键变更时先
    注销旧的再注册新的；注册失败只提示不阻断（与主热键不同，
    命令热键是可选入口）。"""
    from ..hotkey.listener import HotkeyListener

    cmd_cfg = getattr(app.cfg, "commands", None)
    want = (bool(getattr(cmd_cfg, "enable", False))
            and str(getattr(cmd_cfg, "hotkey", "") or "").strip())
    if not want:
        if app.cmd_hotkey is not None:
            try:
                app.cmd_hotkey.stop()
            except Exception:
                logger.debug("命令热键注销失败", exc_info=True)
            app.cmd_hotkey = None
        return
    if app.cmd_hotkey is not None:
        if app.cmd_hotkey.hotkey == want:
            return  # 未变化，保持注册
        try:
            app.cmd_hotkey.stop()
        except Exception:
            logger.debug("命令热键注销失败", exc_info=True)
        app.cmd_hotkey = None
    app.cmd_hotkey = HotkeyListener(want, app._on_command_hotkey)
    try:
        app.cmd_hotkey.start()
    except Exception as exc:
        logger.error("命令热键注册失败：%s", exc)
        app.cmd_hotkey = None
        app.bar.signals.transient_hint.emit(
            t("hint.cmd_hotkey_failed", hotkey=want), 2.5)


def on_command_hotkey(app) -> None:
    """命令待命热键触发（键盘线程回调）：进入命令待命态，下一句直接当命令。

    - 已在录音：立即进待命态（免说触发词）
    - 空闲：置 _standby_start 后走常规启动，会话建立后由
      asr_pipeline.start_session 自动补进待命态
    """
    from . import voice_commands

    if not voice_commands.enabled():
        return
    if app.state == APP_STATE_LISTENING:
        app._command_standby = True
        app.bar.signals.command_standby.emit(True)
        app.bar.signals.transient_hint.emit(
            t("hint.command_standby_next"), 2.5)
        return
    if app.state != APP_STATE_IDLE:
        return
    app._standby_start = True
    app._on_toggle()


def on_command_action(app, action: str, arg: str) -> None:
    """主线程槽：执行本地命令表命中的动作。"""
    from . import voice_commands

    handler = voice_commands.get_handler(action)
    if handler is None:
        logger.warning("语音命令动作未注册：%s", action)
        return
    try:
        if action == "enter":
            # 「发送」与 AI 修正冲突：回车后新内容已进框，修正回调再退格会删错，
            # bump _last_final_at 让"新句检测"取消替换。wait 模式立即回车会发
            # 空消息，先冲刷修正，仍有在途则延迟到上屏后再回车
            app._last_final_at = time.time()
            # 长按 + AI 修正：整段仍压在待修正缓冲里（原文从未上屏）。先说送
            # 一轮再判回车——否则回车当场执行，发出去的是空消息，整段文字随后
            # 才上屏（用户说「发送」等于把自己刚说的话丢掉）
            sent_hold_batch = False
            if (getattr(app, "_hold_llm_texts", None)
                    and app._llm_corrector is not None):
                app._hold_llm_flush()
                sent_hold_batch = True
            if app._llm_corrector is not None and (
                    app._wait_for_correction or sent_hold_batch):
                app._llm_corrector.flush_pending()
                if app._llm_corrector.busy() or app._pending_llm_replace:
                    app._deferred_enter = True
                    llm_tmo = int(
                        getattr(getattr(app.cfg, "llm", None),
                                "timeout_ms", 3000) or 3000)
                    app._deferred_enter_timer.start(max(llm_tmo * 2, 10000))
                    app.bar.signals.transient_hint.emit(
                        t("hint.waiting_correction_send"), 2.0)
                    return
        if action in voice_commands.toggle_actions():
            handler(arg != "off")
        else:
            handler()
    except Exception:
        logger.exception("语音命令执行失败：%s", action)


def on_command_phrase(app, content: str) -> None:
    """主线程槽：自定义短语命中，整段注入。行为对齐普通 FINAL 上屏：
    更新「删除那句」基准、记历史、取消在途修正替换。"""
    content = (content or "").strip()
    if not content:
        return
    # 注入会移动光标，与「发送」同理：取消在途修正的替换防误删
    app._last_final_at = time.time()
    app.bar.signals.text_updated.emit(content, True)
    app.bar.signals.transient_hint.emit(t("hint.phrase_inserted"), 2.0)
    app.injector.inject(
        content, is_final=True, done_callback=app._record_inject_hwnd,
    )
    app._last_spoken_text = content
    from . import history as _history
    _history.add(content)


def on_command_ai(app, instruction: str) -> None:
    """主线程槽：「帮我」AI 命令链：读选中 -> LLM -> 粘贴。回调内继续推进，
    全部在注入线程串行，不冻结主线程。"""
    llm = getattr(app.cfg, "llm", None)
    cmd_cfg = getattr(app.cfg, "commands", None)
    if not getattr(llm, "api_key", ""):
        app.bar.signals.transient_hint.emit(
            t("hint.ai_need_key"), 2.5)
        return
    # AI 命令的粘贴会移动光标/选区，与「发送」同理 bump 取消待定修正防误删
    app._last_final_at = time.time()
    base_url = str(getattr(llm, "base_url", "https://api.openai.com/v1"))
    model = str(getattr(llm, "model", "gpt-4o-mini"))
    # 「帮我」AI 命令的思考模式独立于 AI 修正：默认开（commands.ai_thinking）
    use_thinking = bool(getattr(cmd_cfg, "ai_thinking", True))
    disable_thinking = not use_thinking
    # 一次性调用；思考模式响应慢，超时下限抬到 60s（关思考 20s）
    timeout_ms = max(int(getattr(llm, "timeout_ms", 3000)),
                     60000 if use_thinking else 20000)
    hwnd_before = app.injector.current_window_hwnd()
    app.bar.signals.transient_hint.emit(t("hint.ai_processing"), 4.0)

    def _after_read(selection):
        from . import voice_commands

        if not selection:
            app.bar.signals.transient_hint.emit(
                t("hint.ai_need_selection"), 2.5)
            return
        try:
            out = voice_commands.ai_call(
                base_url, llm.api_key, model, timeout_ms,
                selection, instruction, disable_thinking,
            )
        except Exception:
            logger.warning("AI 命令处理失败", exc_info=True)
            app.bar.signals.transient_hint.emit(
                t("hint.ai_failed"), 2.5)
            return
        if not out.strip():
            app.bar.signals.transient_hint.emit(t("hint.ai_empty"), 2.0)
            return
        # 粘贴前校验前台窗口没变：变了就只放剪贴板，不硬打
        if app.injector.current_window_hwnd() != hwnd_before:
            try:
                import pyperclip
                pyperclip.copy(out)
            except Exception:
                pass
            app.bar.signals.transient_hint.emit(
                t("hint.ai_focus_moved"), 3.0)
            return
        try:
            import keyboard
            import pyperclip
            pyperclip.copy(out)
            time.sleep(0.05)
            keyboard.send("ctrl+v")
            # keep_clipboard=False：粘贴落定后恢复原剪贴板（与注入器一致；慢程序读取留缓冲）
            prev_clip = getattr(app.injector, "selection_prev_clip", None)
            if not app.injector.keep_clipboard and prev_clip is not None:
                time.sleep(0.25)
                try:
                    pyperclip.copy(prev_clip)
                except Exception:
                    logger.debug("AI 命令后恢复剪贴板失败", exc_info=True)
            app.injector.selection_prev_clip = None
            app.bar.signals.transient_hint.emit(
                t("hint.ai_replaced", count=len(out)), 2.0)
        except Exception:
            logger.exception("AI 结果替换失败")
            app.bar.signals.transient_hint.emit(
                t("hint.ai_replace_failed"), 2.5)

    app.injector.read_selection(done_callback=_after_read)


def on_commands_toggled(app, enabled: bool) -> None:
    """右键菜单「语音命令」开关：冲突确认后执行。

    冲突确认：开启会强制关闭「逐字同步」——若逐字同步开着，先弹
    内嵌确认（确认才执行开启，取消维持原状）。
    """
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app.bar.show_llm_wait_confirm(
                t("app.confirm.enable_commands"),
                kind="conflict_commands", yes_text=t("common.ok"),
                no_text=t("common.cancel"))
            return
    apply_commands_toggle(app, enabled)


def apply_commands_toggle(app, enabled: bool) -> None:
    """执行语音命令启停：写配置 + 模块同步 + 待命态清理 + 逐字同步逻辑锁。"""
    from ..config.loader import update_config_field

    logger.info("语音命令开关：%s", "开" if enabled else "关")
    update_config_field("enable", "true" if enabled else "false",
                        section="commands", value_type="bool")
    try:
        app.cfg.commands.enable = bool(enabled)
    except AttributeError:
        from types import SimpleNamespace
        app.cfg.commands = SimpleNamespace(enable=bool(enabled))
    # 同步模块状态（_sync 读完整 commands 节，含触发词/前缀）。
    # 必须在锁联动前：_live_intermediate() 读模块运行态
    sync_config(app)
    # 命令热键随语音命令开关启停（关闭时注销，开启时按配置注册）
    update_cmd_hotkey_listener(app)
    if not enabled and app._command_standby:
        app._command_standby = False
        app.bar.signals.command_standby.emit(False)
    # ---- 逐字同步逻辑锁（语音命令）：命令句的中间帧可能因同音误识别
    # 残留在输入框（顶掉选中文本等），两功能互斥——开启强制关闭并记
    # 偏好，关闭自动恢复
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app._live_pref_locked_commands = True
            app._set_live_config(False)
            # 联动后缀与基础句合并成整句：中英语序不同，拼接会让英文读着别扭
            hint_key = "hint.commands_on_live_off"
        else:
            hint_key = "hint.commands_on"
    elif app._live_pref_locked_commands:
        app._live_pref_locked_commands = None
        app._set_live_config(True)
        hint_key = "hint.commands_off_live_back"
    else:
        hint_key = "hint.commands_off"
    app.bar.signals.transient_hint.emit(
        t(hint_key, prefix=DEFAULT_CMD_PREFIX), 2.0)
