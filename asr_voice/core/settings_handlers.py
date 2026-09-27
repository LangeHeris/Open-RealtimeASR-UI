"""设置开关处理器：引擎/主题/注入方式/LLM/游戏模式/逐字同步/快捷键等切换逻辑。

从 app.py 拆出：这条链路只围绕「右键菜单 / 设置页触发的开关与切换」——
引擎切换、主题切换、注入方式、AI 修正、逐字同步、语音命令、游戏模式、
长按说话、保留剪贴、暂停播放、自动隐藏、置顶、自启、字体变更。
读写 app 状态通过入参 app 访问；app 侧保留同名薄包装方法，信号与调用点不变。

_on_bar_confirm 的路由壳留在 app.py（它同时分发到非设置类处理器如提权），
实际冲突处理逻辑在本模块的 on_bar_confirm 函数。
"""

from __future__ import annotations

import logging
import sys

from ..config.loader import is_configured
from ..hotkey import probe as _hold_probe
from ..hotkey.listener import HotkeyListener
from ..i18n import t

logger = logging.getLogger(__name__)


# ---- 引擎 ----

def engine_menu_id(cfg) -> str:
    """构建引擎菜单 ID（格式与 presets.get_engines 一致）：funasr:<model> /
    aliyun:<model> / xfyun:std:cn|std:en|lang / volcengine:bigasr|seedasr /
    tencent 用 engine_model_type。"""
    engine = getattr(cfg, "engine", "tencent")
    if engine == "funasr":
        return f"funasr:{cfg.funasr.model}"
    if engine == "aliyun":
        a = getattr(cfg, "aliyun", None)
        return f"aliyun:{getattr(a, 'model', 'paraformer-realtime-v2')}"
    if engine == "xfyun":
        x = getattr(cfg, "xfyun", None)
        if getattr(x, "edition", "llm") == "std":
            lang = str(getattr(x, "std_lang", "cn") or "cn")
            return f"xfyun:std:{lang if lang in ('cn', 'en') else 'cn'}"
        return f"xfyun:{getattr(x, 'lang', 'autodialect')}"
    if engine == "volcengine":
        v = getattr(cfg, "volcengine", None)
        resource_id = getattr(v, "resource_id", "volc.bigasr.sauc.duration")
        return "volcengine:seedasr" if "seedasr" in resource_id else "volcengine:bigasr"
    return cfg.tencent.engine_model_type


def follow_slot_rebinding(app, old_active: str, new_cfg) -> None:
    """分类「调用」绑定被改且当前生效引擎正是旧绑定时，生效模型字段跟随新绑定。

    绑定（menu_model_<slot>）与生效模型字段（tencent.engine_model_type /
    aliyun.model）分开存储：右键菜单点击路径两者同步写，设置页/手改
    yaml 只改绑定，此处补齐另一路径，否则保存后录音仍用旧模型。
    """
    from ..config.loader import update_config_field

    t_old, t_new = getattr(app.cfg, "tencent", None), getattr(new_cfg, "tencent", None)
    if t_old is not None and t_new is not None:
        for slot in ("large_2_0", "large_1_0", "general"):
            old_v = str(getattr(t_old, f"menu_model_{slot}", "") or "")
            new_v = str(getattr(t_new, f"menu_model_{slot}", "") or "")
            if old_v and old_v == old_active and new_v and new_v != old_v:
                logger.info("腾讯 %s 档绑定变更且为当前引擎：%s → %s", slot, old_v, new_v)
                t_new.engine_model_type = new_v
                update_config_field("engine_model_type", new_v, section="tencent")
                break
    a_old, a_new = getattr(app.cfg, "aliyun", None), getattr(new_cfg, "aliyun", None)
    if a_old is not None and a_new is not None:
        for slot in ("llm", "general"):
            old_v = str(getattr(a_old, f"menu_model_{slot}", "") or "")
            new_v = str(getattr(a_new, f"menu_model_{slot}", "") or "")
            if old_v and old_v == old_active and new_v and new_v != old_v:
                model = new_v.split(":", 1)[1]
                logger.info("阿里 %s 档绑定变更且为当前引擎：%s → %s", slot, old_v, new_v)
                a_new.model = model
                update_config_field("model", model, section="aliyun")
                break


def on_engine_changed(app, engine: str) -> None:
    """引擎切换：写配置让热加载生效。engine 带 funasr:/aliyun:/xfyun:/volcengine: 前缀，
    其余按腾讯云模型名处理。"""
    from ..config.loader import update_config_field
    from ..ui.presets import engine_display_name

    logger.info("切换引擎：%s", engine)
    app.bar.set_engine(engine)
    name = engine_display_name(engine)
    ok_hint = t("hint.engine_switched", engine=name)
    no_key_hint = t("hint.no_key_switch")
    no_funasr_hint = (
        t("hint.funasr_not_in_package")
        if getattr(sys, "frozen", False) else t("hint.funasr_missing")
    )

    if engine.startswith("funasr:"):
        model = engine.split(":", 1)[1]
        update_config_field("engine", "funasr")
        update_config_field("model", model, section="funasr")
        app.cfg.engine = "funasr"
        app.cfg.funasr.model = model
        if is_configured(app.cfg):
            app._kick_funasr_preheat(model, name)
        else:
            app.bar.signals.error_occurred.emit(no_funasr_hint)
    elif engine.startswith("aliyun:"):
        model = engine.split(":", 1)[1]
        update_config_field("engine", "aliyun")
        update_config_field("model", model, section="aliyun")
        app.cfg.engine = "aliyun"
        if not hasattr(app.cfg, "aliyun"):
            from types import SimpleNamespace
            app.cfg.aliyun = SimpleNamespace()
        app.cfg.aliyun.model = model
        emit_switch_hint(app, ok_hint, no_key_hint)
    elif engine.startswith("xfyun:"):
        option = engine.split(":", 1)[1]
        update_config_field("engine", "xfyun")
        app.cfg.engine = "xfyun"
        if not hasattr(app.cfg, "xfyun"):
            from types import SimpleNamespace
            app.cfg.xfyun = SimpleNamespace()
        if option.startswith("std"):
            lang = option.split(":", 1)[1] if ":" in option else "cn"
            if lang not in ("cn", "en"):
                lang = "cn"
            update_config_field("edition", "std", section="xfyun")
            update_config_field("std_lang", lang, section="xfyun")
            app.cfg.xfyun.edition = "std"
            app.cfg.xfyun.std_lang = lang
        else:
            update_config_field("edition", "llm", section="xfyun")
            update_config_field("lang", option, section="xfyun")
            app.cfg.xfyun.edition = "llm"
            app.cfg.xfyun.lang = option
        emit_switch_hint(app, ok_hint, no_key_hint)
    elif engine.startswith("volcengine:"):
        family = engine.split(":", 1)[1]
        update_config_field("engine", "volcengine")
        if not hasattr(app.cfg, "volcengine"):
            from types import SimpleNamespace
            app.cfg.volcengine = SimpleNamespace()
        billing = str(getattr(app.cfg.volcengine, f"{family}_billing", "") or "duration")
        if billing not in ("duration", "concurrent"):
            billing = "duration"
        resource_id = f"volc.{family}.sauc.{billing}"
        update_config_field("resource_id", resource_id, section="volcengine")
        app.cfg.engine = "volcengine"
        app.cfg.volcengine.resource_id = resource_id
        emit_switch_hint(app, ok_hint, no_key_hint)
    else:
        update_config_field("engine", "tencent")
        update_config_field("engine_model_type", engine, section="tencent")
        app.cfg.engine = "tencent"
        app.cfg.tencent.engine_model_type = engine
        emit_switch_hint(app, ok_hint, no_key_hint)


def emit_switch_hint(app, ok_hint: str, fail_hint: str) -> None:
    """引擎切换结果提示统一出口：配置就绪闪现成功语，否则橙色错误通道。"""
    if is_configured(app.cfg):
        app.bar.signals.transient_hint.emit(ok_hint, 2.0)
    else:
        app.bar.signals.error_occurred.emit(fail_hint)


# ---- 主题 ----

def on_theme_changed(app, theme_name: str) -> None:
    """主题切换：更新颜色 + 写入配置文件。"""
    from ..config.loader import update_config_field
    from ..ui.theme_registry import theme_display_name, available_themes

    theme = available_themes().get(theme_name)
    if not theme:
        return
    logger.info("切换主题：%s", theme_name)
    app.bar.apply_theme_name(theme_name)
    update_config_field("bg_color", theme["bg"], section="ui")
    update_config_field("text_color", theme["text"], section="ui")
    update_config_field("accent_color", theme["accent"], section="ui")
    update_config_field("theme_name", theme_name, section="ui")
    app.bar.signals.transient_hint.emit(
        t("hint.theme_switched", theme=theme_display_name(theme_name)), 1.5)


# ---- 注入方式 ----

def on_injection_method_changed(app, method: str) -> None:
    """注入方式切换（右键菜单）：立即生效并写入配置持久化。"""
    if method not in ("clipboard_paste", "simulate_keys"):
        return
    if method == "simulate_keys" and app.injector.game_mode:
        app.bar.show_llm_wait_confirm(
            t("app.confirm.keys_off_game"),
            kind="conflict_method_game", yes_text=t("common.ok"),
            no_text=t("common.cancel"))
        return
    if method == "clipboard_paste":
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app.bar.show_llm_wait_confirm(
                t("app.confirm.paste_off_live"),
                kind="conflict_paste", yes_text=t("common.ok"),
                no_text=t("common.cancel"))
            return
    apply_injection_method(app, method)


def apply_injection_method(app, method: str) -> None:
    """执行注入方式切换：写配置 + 逐字同步逻辑锁 + 联动确认/提示。"""
    from ..config.loader import update_config_field

    logger.info("切换注入方式：%s", method)
    app.injector.method = method
    try:
        app.cfg.injection.method = method
    except Exception:
        pass
    app.bar.set_injection_method(method)
    update_config_field("method", method, section="injection")
    hint_key = ("hint.method_clip" if method == "clipboard_paste"
                else "hint.method_keys")
    if method == "clipboard_paste":
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app._live_pref_locked_paste = True
            app._set_live_config(False)
            hint_key = "hint.method_clip_live_off"
    elif app._live_pref_locked_paste:
        app._live_pref_locked_paste = None
        app._set_live_config(True)
        hint_key = "hint.method_keys_live_back"
    if method == "simulate_keys":
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if (not live_cfg_on and not app._llm_config_enabled()
                and not app._live_pref_locked_paste
                and not app._commands_config_enabled()):
            app.bar.show_llm_wait_confirm(
                t("app.confirm.enable_live_sync"), kind="live_sync")
    app.bar.set_live_intermediate(app._live_intermediate())
    app.bar.signals.transient_hint.emit(t(hint_key), 1.5)


# ---- 逐字同步 / 冲突源 ----

def llm_config_enabled(app) -> bool:
    """AI 修正是否启用（按 cfg.llm.enable，api_key 由创建修正器处把关）。"""
    llm = getattr(app.cfg, "llm", None)
    return llm is not None and bool(getattr(llm, "enable", False))


def commands_config_enabled(app) -> bool:
    """语音命令是否启用（读模块运行态）。"""
    from . import voice_commands
    return voice_commands.enabled()


def set_live_config(app, enabled: bool) -> None:
    """写入逐字同步配置（持久化 + 内存 cfg + 悬浮条勾选态同步）。"""
    from ..config.loader import update_config_field

    update_config_field("live_intermediate", "true" if enabled else "false",
                        section="recording", value_type="bool")
    try:
        app.cfg.recording.live_intermediate = bool(enabled)
    except AttributeError:
        from types import SimpleNamespace
        app.cfg.recording = SimpleNamespace(**vars(getattr(app.cfg, "recording", SimpleNamespace())))
        setattr(app.cfg.recording, "live_intermediate", bool(enabled))
    app.bar.set_live_intermediate(app._live_intermediate())


def live_conflict_sources(app) -> list:
    """「逐字同步」开启路上的冲突源（llm/paste/commands/game 的子集）。"""
    sources = []
    if llm_config_enabled(app):
        sources.append("llm")
    if app.injector.method == "clipboard_paste":
        sources.append("paste")
    if commands_config_enabled(app):
        sources.append("commands")
    if app.injector.game_mode:
        sources.append("game")
    return sources


def live_conflict_desc(sources: list) -> str:
    """冲突源 -> 确认文案的"将…"部分。"""
    keys = []
    if "llm" in sources:
        keys.append("app.live_conflict.llm")
    if "commands" in sources:
        keys.append("app.live_conflict.commands")
    if "game" in sources:
        keys.append("app.live_conflict.game")
    if "paste" in sources:
        keys.append("app.live_conflict.paste")
    if not keys:
        return ""
    phrases = [t(k) for k in keys]
    if len(phrases) > 1:
        sep = t("common.list_sep")
        and_ = t("common.list_and")
        return sep.join(phrases[:-1]) + and_ + phrases[-1]
    return phrases[0]


def on_live_intermediate_toggled(app, enabled: bool) -> None:
    """右键菜单「逐字同步」：即时生效并持久化。"""
    logger.info("逐字同步开关：%s", "开" if enabled else "关")
    if enabled:
        sources = live_conflict_sources(app)
        if sources:
            app._pending_live_conflicts = sources
            app.bar.show_llm_wait_confirm(
                t("app.confirm.enable_live_conflict",
                  items=live_conflict_desc(sources)),
                kind="conflict_live", yes_text=t("common.ok"),
                no_text=t("common.cancel"))
            return
    set_live_config(app, enabled)
    app.bar.signals.transient_hint.emit(
        t("hint.live_on") if enabled else t("hint.live_off"), 1.5
    )


# ---- AI 修正 ----

def on_llm_enable_toggled(app, enabled: bool) -> None:
    """右键菜单「AI 修正」：即时启停并持久化。未配 api_key 拒绝开启。"""
    if enabled and not getattr(getattr(app.cfg, "llm", None), "api_key", ""):
        app.bar.signals.error_occurred.emit(t("app.err.llm_no_key_for_edit"))
        return
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app.bar.show_llm_wait_confirm(
                t("app.confirm.enable_llm"),
                kind="conflict_llm", yes_text=t("common.ok"),
                no_text=t("common.cancel"))
            return
    apply_llm_toggle(app, enabled)


def apply_llm_toggle(app, enabled: bool) -> None:
    """执行 AI 修正启停：写配置 + 修正器启停 + 逐字同步逻辑锁 + 联动确认。"""
    from ..config.loader import update_config_field

    logger.info("AI 修正开关：%s", "开" if enabled else "关")
    update_config_field("enable", "true" if enabled else "false",
                        section="llm", value_type="bool")
    try:
        app.cfg.llm.enable = bool(enabled)
    except AttributeError:
        from types import SimpleNamespace
        app.cfg.llm = SimpleNamespace(enable=bool(enabled))
    if enabled and app._llm_corrector is None:
        app._setup_llm_corrector()
        if app._llm_corrector is None:
            return
    elif not enabled and app._llm_corrector is not None:
        app._llm_corrector = None
        app._wait_for_correction = False
        update_config_field("wait_for_correction", "false",
                            section="llm", value_type="bool")
        app.cfg.llm.wait_for_correction = False
        logger.info("LLM 修正已关闭")
    if enabled:
        update_config_field("wait_for_correction", "false",
                            section="llm", value_type="bool")
        app.cfg.llm.wait_for_correction = False
        app._wait_for_correction = False
    hint_key = "hint.llm_on" if enabled else "hint.llm_off"
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app._live_pref_locked = True
            set_live_config(app, False)
            hint_key = "hint.llm_on_live_off"
    elif app._live_pref_locked:
        app._live_pref_locked = None
        set_live_config(app, True)
        hint_key = "hint.llm_off_live_back"
    if enabled:
        app.bar.show_llm_wait_confirm(t("app.confirm.wait_correction"))
    else:
        app.bar.hide_llm_wait_confirm()
    app.bar.signals.transient_hint.emit(t(hint_key), 2.0)


def on_llm_wait_confirm(app, enable_wait: bool) -> None:
    """悬浮条内嵌确认的回答：「修正后上屏」开 / 不开。"""
    app.bar.hide_llm_wait_confirm()
    if not enable_wait:
        app.bar.signals.transient_hint.emit(
            t("hint.no_wait_correction"), 2.5)
        return
    from ..config.loader import update_config_field
    update_config_field("wait_for_correction", "true",
                        section="llm", value_type="bool")
    try:
        app.cfg.llm.wait_for_correction = True
    except AttributeError:
        from types import SimpleNamespace
        app.cfg.llm = SimpleNamespace(
            **vars(getattr(app.cfg, "llm", SimpleNamespace())))
        setattr(app.cfg.llm, "wait_for_correction", True)
    app._wait_for_correction = True
    app.bar.signals.transient_hint.emit(t("hint.wait_correction_on"), 1.5)


# ---- 确认路由 ----

def on_live_sync_confirm(app, enabled: bool) -> None:
    """「逐字同步」确认回答：开启即生效；暂不则保持关闭。"""
    app.bar.hide_llm_wait_confirm()
    if enabled:
        set_live_config(app, True)
        app.bar.signals.transient_hint.emit(t("hint.live_on"), 1.5)
    else:
        app.bar.signals.transient_hint.emit(
            t("hint.live_stays_off"), 2.0)


def on_bar_confirm(app, kind: str, enabled: bool) -> None:
    """悬浮条内嵌确认统一入口：按询问类型分发。"""
    if kind == "llm_wait":
        on_llm_wait_confirm(app, enabled)
    elif kind == "live_sync":
        on_live_sync_confirm(app, enabled)
    elif kind == "conflict_llm":
        if enabled:
            apply_llm_toggle(app, True)
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(t("hint.cancel_keep_method"), 2.0)
    elif kind == "conflict_paste":
        if enabled:
            apply_injection_method(app, "clipboard_paste")
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(t("hint.cancel_keep_keys"), 2.0)
    elif kind == "conflict_live":
        if enabled:
            sources = app._pending_live_conflicts or []
            app._pending_live_conflicts = None
            if "llm" in sources:
                apply_llm_toggle(app, False)
            if "commands" in sources:
                app._apply_commands_toggle(False)
            if "game" in sources:
                apply_game_mode_toggle(app, False)
            set_live_config(app, True)
            if "paste" in sources:
                apply_injection_method(app, "simulate_keys")
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(
                t("hint.cancel_live_stays_off"), 2.0)
    elif kind == "conflict_commands":
        if enabled:
            app._apply_commands_toggle(True)
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(
                t("hint.cancel_commands_off"), 2.0)
    elif kind == "conflict_game_mode":
        if enabled:
            apply_game_mode_toggle(app, True)
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(
                t("hint.cancel_game_off"), 2.0)
    elif kind == "conflict_method_game":
        if enabled:
            apply_game_mode_toggle(app, False)
            if app.injector.method != "simulate_keys":
                apply_injection_method(app, "simulate_keys")
        else:
            app.bar.hide_llm_wait_confirm()
            app.bar.signals.transient_hint.emit(
                t("hint.cancel_force_clip"), 2.0)
    elif kind == "game_mode_elevate":
        app.bar.hide_llm_wait_confirm()
        if enabled:
            app._on_elevate()
        else:
            app.bar.signals.transient_hint.emit(
                t("hint.elevate_later"), 2.5)


# ---- 保留剪贴 / 暂停播放 ----

def on_keep_clipboard_toggled(app, enabled: bool) -> None:
    """保留剪贴开关（右键菜单切换）：立即生效并写入配置持久化。"""
    from ..config.loader import update_config_field

    logger.info("保留剪贴：%s", "开" if enabled else "关")
    app.injector.keep_clipboard = bool(enabled)
    try:
        app.cfg.injection.keep_clipboard = bool(enabled)
    except Exception:
        pass
    app.bar.set_keep_clipboard(enabled)
    update_config_field("keep_clipboard",
                        "true" if enabled else "false",
                        section="injection", value_type="bool")
    app.bar.signals.transient_hint.emit(
        t("hint.keep_clip_on") if enabled else t("hint.keep_clip_off"),
        1.5,
    )


def on_pause_bg_audio_toggled(app, enabled: bool) -> None:
    """右键菜单「暂停播放」开关：接口可用才落盘；下次录音生效。"""
    from ..config.loader import update_config_field

    if enabled and not app.media_pauser.pause_available():
        app.bar.signals.error_occurred.emit(t("app.err.media_unavailable"))
        return
    logger.info("暂停播放开关：%s", "开" if enabled else "关")
    update_config_field("pause_background_audio",
                        "true" if enabled else "false",
                        section="recording", value_type="bool")
    rec = getattr(app.cfg, "recording", None)
    if rec is not None:
        rec.pause_background_audio = bool(enabled)
    app.bar.signals.transient_hint.emit(
        t("hint.pause_bg_on") if enabled else t("hint.pause_bg_off"),
        2.0)


# ---- 长按说话 ----

def on_hold_to_talk_toggled(app, enabled: bool) -> None:
    """右键菜单切换长按录音：写配置 + 重建热键（on_press 有无随之切换）+ 提示。"""
    from ..config.loader import update_config_field
    update_config_field("hold_to_talk", "true" if enabled else "false",
                        section="hotkey", value_type="bool")
    try:
        app.cfg.hotkey.hold_to_talk = enabled
    except Exception:
        logger.debug("同步内存配置 hold_to_talk 失败，忽略", exc_info=True)
    if app.hotkey is not None:
        app.hotkey.stop()
        app.hotkey = HotkeyListener(
            app.cfg.hotkey.toggle, app._on_toggle,
            on_press=app._request_hold_press if app._hold_to_talk_enabled() else None)
        try:
            app.hotkey.start()
        except Exception as exc:
            logger.error("热键重新注册失败：%s", exc)
            app.bar.signals.error_occurred.emit(
                t("app.err.hotkey_register_short", hotkey=app.cfg.hotkey.toggle))
    if not enabled:
        app._hold_tracker = None
        app._stop_hold_watch()
        app._hold_active = False
        app.bar.signals.transient_hint.emit(t("hint.hold_off"), 1.8)
        return
    if app._hold_to_talk_enabled():
        app.bar.signals.transient_hint.emit(t("hint.hold_on"), 1.8)
        return
    if not bool(_hold_probe.available()):
        app.bar.signals.error_occurred.emit(t("app.err.hold_unsupported"))
    else:
        app.bar.signals.error_occurred.emit(
            t("app.err.hold_hotkey_incompatible",
              hotkey=app.cfg.hotkey.toggle))


# ---- 游戏模式 ----

def on_game_mode_toggled(app, enabled: bool) -> None:
    """右键菜单「游戏模式」开关：冲突确认后执行。"""
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app.bar.show_llm_wait_confirm(
                t("app.confirm.enable_game_mode"),
                kind="conflict_game_mode", yes_text=t("common.ok"),
                no_text=t("common.cancel"))
            return
    apply_game_mode_toggle(app, enabled)


def apply_game_mode_toggle(app, enabled: bool) -> None:
    """执行游戏模式启停：写配置 + 注入器/悬浮条同步 + 注入方式/逐字同步逻辑锁。"""
    from ..config.loader import update_config_field

    logger.info("游戏模式开关：%s", "开" if enabled else "关")
    update_config_field("game_mode", "true" if enabled else "false",
                        section="injection", value_type="bool")
    try:
        app.cfg.injection.game_mode = bool(enabled)
    except AttributeError:
        from types import SimpleNamespace
        app.cfg.injection = SimpleNamespace(
            **vars(getattr(app.cfg, "injection", SimpleNamespace())))
        setattr(app.cfg.injection, "game_mode", bool(enabled))
    app.injector.game_mode = bool(enabled)
    app.bar.set_game_mode(bool(enabled))
    hint = (t("hint.game_on") if enabled else t("hint.game_off"))
    if enabled:
        if app.injector.method != "clipboard_paste":
            app._method_pref_locked_game = app.injector.method
            apply_method_lock(app, "clipboard_paste")
            hint += t("hint.suffix_method_clip")
    elif app._method_pref_locked_game:
        prev = app._method_pref_locked_game
        app._method_pref_locked_game = None
        apply_method_lock(app, prev)
        hint += t(
            "hint.suffix_method_restored_keys"
            if prev == "simulate_keys" else "hint.suffix_method_restored_clip")
    if enabled:
        live_cfg_on = bool(getattr(getattr(app.cfg, "recording", None),
                                   "live_intermediate", True))
        if live_cfg_on:
            app._live_pref_locked_game = True
            set_live_config(app, False)
            hint += t("hint.suffix_live_off")
    elif app._live_pref_locked_game:
        app._live_pref_locked_game = None
        set_live_config(app, True)
        hint += t("hint.suffix_live_back")
    app.bar.signals.transient_hint.emit(hint, 2.0)
    from .elevation import should_prompt_elevation
    if should_prompt_elevation(enabled):
        app.bar.show_llm_wait_confirm(
            t("app.confirm.game_mode_elevate"),
            kind="game_mode_elevate", yes_text=t("common.elevate_now"),
            no_text=t("common.not_now"))


def apply_method_lock(app, method: str) -> None:
    """游戏模式注入方式锁的强制切换/恢复：四处同步注入方式。"""
    from ..config.loader import update_config_field

    app.injector.method = method
    try:
        app.cfg.injection.method = method
    except Exception:
        pass
    app.bar.set_injection_method(method)
    update_config_field("method", method, section="injection")


# ---- 杂项开关 ----

def on_interrupt_delete_toggled(app, enabled: bool) -> None:
    """删除键打断开关（右键菜单切换）：即时启停监听，配置已由 bar 写入。"""
    app._update_delete_listener(enabled)


def on_auto_hide_toggled(app, enabled: bool) -> None:
    """自动隐藏开关：立即生效并写入配置持久化。"""
    from ..config.loader import update_config_field

    seconds = 5 if enabled else 0
    app.bar.set_auto_hide(enabled, seconds)
    update_config_field("auto_hide_seconds", str(seconds),
                        section="ui", value_type="int")
    app.bar.signals.transient_hint.emit(
        t("hint.auto_hide_on") if enabled else t("hint.auto_hide_off"), 1.5
    )


def on_always_on_top_toggled(app, enabled: bool) -> None:
    """永远置顶开关：窗口标志已由 bar 切换，这里写配置持久化。"""
    from ..config.loader import update_config_field

    update_config_field("always_on_top", "true" if enabled else "false",
                        section="ui", value_type="bool")


def on_autostart_toggled(app, enabled: bool) -> None:
    """开机自启开关：写/删 HKCU Run 注册表键（用户级，无需管理员）。"""
    from .autostart import set_enabled

    ok, msg_key = set_enabled(enabled)
    if ok:
        app.bar.signals.transient_hint.emit(t(msg_key), 2.0)
    else:
        app.bar.signals.error_occurred.emit(t(msg_key))


def on_font_file_changed(app, font_path: str) -> None:
    """用户通过右键菜单选了字体文件：复制到程序目录、加载、写入配置。"""
    from ..config.loader import update_config_field
    from ..ui.fonts import apply_default_font, get_loaded_family, fonts_dir
    from pathlib import Path
    import shutil

    if font_path:
        src = Path(font_path)
        dest_dir = fonts_dir()
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        if src.resolve() != dest.resolve():
            try:
                shutil.copy2(str(src), str(dest))
                font_path = str(dest)
                logger.info("字体文件已复制到：%s", font_path)
            except Exception as exc:
                logger.warning("字体文件复制失败，使用原路径：%s", exc)
    config_path = font_path
    if font_path:
        try:
            rel = Path(font_path).resolve().relative_to(fonts_dir().parent.resolve())
            config_path = rel.as_posix()
        except ValueError:
            config_path = font_path
    apply_default_font(
        app._app,
        family=getattr(app.cfg.ui, "font_family", "") or "",
        font_file=config_path,
        app_point_size=int(getattr(app.cfg.ui, "font_size_app", 9) or 9),
    )
    update_config_field("font_file", config_path, section="ui")
    family = get_loaded_family() or t("hint.font_qt_default")
    if font_path:
        app.bar.signals.transient_hint.emit(
            t("hint.font_current", family=family), 2.0)
    else:
        app.bar.signals.transient_hint.emit(t("hint.font_restored"), 2.0)
    app.bar.apply_theme_name(app.bar._current_theme_name or "dark")
    app.bar.update()
