"""悬浮条右键菜单构建。

从 floating_bar.py 拆出：菜单构建逻辑（~270 行）与悬浮条窗口主体无关，
悬浮条与托盘共用同一份菜单（app._setup_tray 调 bar.build_menu）。
所有对悬浮条状态的读取通过入参 bar（FloatingBar 实例）完成。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QActionGroup,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QWidget

from ..config.loader import dialog_enabled, engine_group_configured
from ..core import history as _history
from ..hotkey.hold import hold_flag_enabled
from ..i18n import t
from ..paths import state_dir
from .fonts import get_loaded_family
from .presets import ENGINE_CATEGORIES, get_engines, provider_menu_items
from .theme_features import get_feature
from .theme_registry import available_themes, theme_display_name

if TYPE_CHECKING:
    from .floating_bar import FloatingBar

logger = logging.getLogger(__name__)

# 分组标题（菜单里的显示文案）：存 i18n 键而非文案本身。
# 导入期不取文案的理由见 i18n 模块 docstring；取用时经 _group_title() 调 t()。
_GROUP_TITLE_KEYS = {
    "tencent": "engine.group.tencent",
    "aliyun": "engine.group.aliyun",
    "xfyun": "engine.group.xfyun",
    "volcengine": "engine.group.volcengine",
    "funasr": "engine.group.funasr",
}


def _group_title(group: str) -> str:
    """分组标题显示名（未知分组回退原键名，确保菜单不出现空行）。

    不加 `default=`：`_GROUP_TITLE_KEYS.get(group, group)` 已保证未知分组
    走"拿 group 本身当键"，`t()` 找不到会返回该键，兜底已经在了；再加一层
    `default` 反而会把"词典漏了新分组的词条"这件事**静默吞掉**，菜单里
    冒出 `engine.group.newvendor` 而测试全绿。留 `t()` 自带的 debug 日志，
    至少还留了条线索。
    """
    return t(_GROUP_TITLE_KEYS.get(group, group))


# 自绘菜单指示图标：QSS 的 image/url() 只认真实文件，首次使用画成缓存 PNG
#（文件名含颜色，换主题色自动换新图）。勾选统一用主题 accent，替换系统黑勾；
# 子菜单箭头自绘 chevron，避免深浅主题下系统箭头对比不足
_ICON_DIR = state_dir() / "menu_icons"


def _draw_check(p: QPainter, color_hex: str) -> None:
    pen = QPen(QColor(color_hex), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(3.2, 8.6)
    path.lineTo(6.7, 12.0)
    path.lineTo(12.8, 4.4)
    p.drawPath(path)


def _draw_chevron(p: QPainter, color_hex: str) -> None:
    pen = QPen(QColor(color_hex), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(6.0, 3.4)
    path.lineTo(10.4, 8.0)
    path.lineTo(6.0, 12.6)
    p.drawPath(path)


def _icon_png(kind: str, color_hex: str, draw) -> str:
    """取（缺则生成）16px 指示图标缓存 PNG 路径；写盘失败返回空串，调用方省略该规则。"""
    path = _ICON_DIR / f"{kind}_{color_hex.lstrip('#')}.png"
    if not path.is_file():
        try:
            _ICON_DIR.mkdir(parents=True, exist_ok=True)
            pix = QPixmap(16, 16)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw(p, color_hex)
            p.end()
            pix.save(str(path))
        except Exception:
            logger.warning("菜单指示图标写入失败，跳过该样式：%s", path, exc_info=True)
            return ""
    return path.as_posix()


def _indicator_qss(bar: "FloatingBar", light: bool) -> str:
    """勾选标记 + 子菜单箭头的 QSS 片段（深浅各自配色，缺失图标时自动省略）。"""
    check = _icon_png("check", bar._accent_color.name(), _draw_check)
    chev_color = "#7a7a84" if light else "#c8c8d2"
    chev = _icon_png("chevron", chev_color, _draw_chevron)
    parts = []
    if check:
        parts.append(
            f"QMenu::indicator {{ width: 15px; height: 15px; }}"
            f"QMenu::indicator:checked {{ image: url({check}); }}"
        )
    if chev:
        parts.append(
            "QMenu::right-arrow { width: 11px; height: 11px; margin-right: 1px; "
            f"image: url({chev}); }}"
        )
    return "\n".join(parts)


def menu_stylesheet(bar: "FloatingBar") -> str:
    """按当前主题生成右键菜单样式；半透明依赖 build_context_menu 里的 WA_TranslucentBackground。

    浅色菜单表面与悬浮条同灰阶（#f7f7f8，见 presets.THEMES），深浅分支共用
    同一套结构与自绘指示图标，保证悬浮条与菜单风格一致。
    """
    light = bar._is_light(bar._bg_color.name())

    # 菜单字号最小 14px：Windows 默认 9pt 菜单字体偏小发虚；
    # 字重 500（Medium）与悬浮条文字一致：Regular 偏细，无 Medium 字面的
    # 字体（如雅黑）自动落回最近字重，无副作用
    # 文字列对齐（2026-09-09 两轮用户报障后探针实测定案）：Qt 对含勾选项的
    # 菜单**整菜单**预留 15px 勾选列（宽=QMenu::indicator 的 width），勾选图标
    # 画在该列（项左缘+2），**不占 padding**。故勾选项与非勾选项共用同一条
    # padding 规则即全文字同列；早先给勾选项另加 28/20px 左 padding 是在预留列
    # 之外又叠一层缩进（「语音模式」比兄弟项右移 16/8px 的根因）。
    # 右侧 28px：菜单宽度由最宽项撑起，收窄后用户报「太窄了」（2026-09-10），
    # 恢复原右留白；子菜单箭头锚在项右缘（margin-right 1px），不受 padding 影响
    menu_px = max(14, int(getattr(bar.cfg, "font_size_bar", 13) or 13))
    # 自定义字体时显式声明 font-family 防被系统字体覆盖，否则用雅黑族
    family = get_loaded_family()
    font_stack = (
        f"font-family: '{family}', 'Microsoft YaHei UI', 'PingFang SC', sans-serif; "
        f"font-size: {menu_px}px; font-weight: 500;"
        if family else
        f"font-family: 'Microsoft YaHei UI', 'PingFang SC', sans-serif; "
        f"font-size: {menu_px}px; font-weight: 500;"
    )
    # 主题专属菜单样式委托特性模块（theme_features）；模块缺失落到深浅默认样式
    if bar._knot_icon_mode:
        feat = get_feature("celtic")
        if feat is not None:
            return feat.menu_qss(bar, font_stack)
    if bar._top_line_color is not None:
        feat = get_feature("cyberpunk")
        if feat is not None:
            return feat.menu_qss(bar, font_stack)
    if getattr(bar, "_pixel_mode", False):
        feat = get_feature("pixel")
        if feat is not None:
            return feat.menu_qss(bar, font_stack)
    indicators = _indicator_qss(bar, light)
    if light:
        a = bar._accent_color
        sel_bg = f"rgba({a.red()},{a.green()},{a.blue()},42)"
        press_bg = f"rgba({a.red()},{a.green()},{a.blue()},72)"
        # 背景直接取悬浮条当前底色（含实际透明度）：配色与透明度跟悬浮条
        # 完全一致（含自定义底色 / bar_opacity 的情况）。半透明表面禁用
        # ClearType，菜单文字与悬浮条同为灰阶抗锯齿，观感统一优先
        bg = bar._bg_color
        menu_bg = f"rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()})"
        return f"""
            QMenu {{
                {font_stack}
                background: {menu_bg}; color: #1d1d1f;
                border: 1px solid rgba(0, 0, 0, 38); border-radius: 8px; padding: 6px;
            }}
            QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {sel_bg}; color: {a.name()}; }}
            QMenu::item:pressed {{ background: {press_bg}; color: {a.name()}; }}
            QMenu::item:disabled {{ color: #8a8a94; font-weight: bold; font-size: 13px; padding: 8px 12px 2px; }}
            QMenu::separator {{ height: 1px; background: rgba(0, 0, 0, 22); margin: 4px 8px; }}
            {indicators}
        """
    # 深色主题：Fluent 磨砂感（深灰半透明）
    return f"""
        QMenu {{
            {font_stack}
            background: rgba(38, 38, 44, 205); color: #e0e0e0;
            border: 1px solid rgba(255, 255, 255, 26); border-radius: 8px; padding: 6px;
        }}
        QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
        QMenu::item:selected {{ background: #3a3a44; }}
        QMenu::item:pressed {{ background: #45454f; }}
        QMenu::item:disabled {{ color: #9a9aa8; font-weight: bold; font-size: 13px; padding: 8px 12px 2px; }}
        QMenu::separator {{ height: 1px; background: #444; margin: 4px 8px; }}
        {indicators}
    """


def build_context_menu(bar: "FloatingBar", parent: Optional[QWidget] = None) -> QMenu:
    """构建统一右键菜单（悬浮条与托盘共用，高频项贴顶）。

    parent 只用于内存归属：QMenu 父对象必须是 QWidget 或 None，
    传 QSystemTrayIcon 这类非 QWidget 时退回用悬浮条自身。
    """
    # 运行态常量延迟导入：避免模块加载期与 floating_bar 循环依赖
    from .floating_bar import UI_ARMED, UI_LISTENING, UI_RECOGNIZING

    menu_parent = parent if isinstance(parent, QWidget) else bar
    menu = QMenu(menu_parent)
    menu.setStyleSheet(menu_stylesheet(bar))

    # 启停录音。三态分流：录音中→「停止录音」；长按准备中（ARMED：键已按下、未越
    # 阈值）→「准备中…」且**置灰不可点**；其余（待机/对话等）→「开始录音」。
    # ARMED 不能沿用「开始录音」：此刻用户正按着键，报"开始录音"是错的，点下去会
    # 开出一段与本次长按无关的录音，而松手时长按侧不认它（_hold_owns_session 未
    # 归属），那一段只能等静音自动停止或 60 秒硬上限收场。置灰比只改文案可靠——
    # 改了文案照样点得动。
    if bar._state == UI_ARMED:
        act_toggle = menu.addAction(t("menu.preparing"))
        act_toggle.setEnabled(False)
    else:
        toggle_label = t("menu.stop_recording") \
            if bar._state in (UI_LISTENING, UI_RECOGNIZING) \
            else t("menu.start_recording")
        act_toggle = menu.addAction(toggle_label)
        act_toggle.triggered.connect(lambda: bar.signals.toggle_requested.emit())

    # 完整配置（bar.cfg 只是 cfg.ui 子节，无密钥与 dialog 节字段）：
    # 对话入口显隐与下方引擎密钥过滤都要用
    full_cfg = bar._full_cfg or bar.cfg

    # 语音模式（spec：右键菜单顶部一项 checkable，给不想记热键的人一个入口）。
    # dialog.enable=False 时整项不添加（功能总开关，同时会注销对话热键与
    # 悬浮条对话入口）；读不到配置时 dialog_enabled 默认启用，不误隐藏
    if dialog_enabled(full_cfg):
        act_dialog = menu.addAction(t("menu.dialog_mode"))
        act_dialog.setCheckable(True)
        act_dialog.setChecked(bar._state.startswith("dialog_"))
        act_dialog.setToolTip(t("menu.dialog_mode_tip"))
        act_dialog.triggered.connect(
            lambda checked: bar.signals.dialog_requested.emit(checked)
        )

    menu.addSeparator()

    # 注入逻辑（子菜单内二选一：入口一行，展开后两个选项）
    inj_menu = menu.addMenu(t("menu.injection"))
    inj_group = QActionGroup(inj_menu)
    inj_group.setExclusive(True)
    act_clipboard = inj_menu.addAction(t("menu.injection.clipboard"))
    act_clipboard.setCheckable(True)
    act_clipboard.setChecked(bar._injection_method != "simulate_keys")
    act_clipboard.setToolTip(t("menu.injection.clipboard_tip"))
    inj_group.addAction(act_clipboard)
    act_simulate = inj_menu.addAction(t("menu.injection.simulate"))
    act_simulate.setCheckable(True)
    act_simulate.setChecked(bar._injection_method == "simulate_keys")
    act_simulate.setToolTip(t("menu.injection.simulate_tip"))
    inj_group.addAction(act_simulate)
    act_clipboard.triggered.connect(
        lambda: bar.signals.injection_method_changed.emit("clipboard_paste")
    )
    act_simulate.triggered.connect(
        lambda: bar.signals.injection_method_changed.emit("simulate_keys")
    )
    # 保留剪贴：开启时结果留在剪贴板可手动 Ctrl+V；关闭时粘贴后恢复原内容
    inj_menu.addSeparator()
    act_keep = inj_menu.addAction(t("menu.keep_clipboard"))
    act_keep.setCheckable(True)
    act_keep.setChecked(bool(getattr(bar, "_keep_clipboard", True)))
    act_keep.setToolTip(t("menu.keep_clipboard_tip"))
    act_keep.triggered.connect(
        lambda checked: bar.signals.keep_clipboard_toggled.emit(checked)
    )
    # 游戏模式：强制剪贴板粘贴注入（不降级逐字、不恢复剪贴板），
    # 用于逐字输入无效的游戏等自绘输入框
    act_game = inj_menu.addAction(t("menu.game_mode"))
    act_game.setCheckable(True)
    act_game.setChecked(bool(getattr(bar, "_game_mode", False)))
    act_game.setToolTip(t("menu.game_mode_tip"))
    act_game.triggered.connect(
        lambda checked: bar.signals.game_mode_toggled.emit(checked)
    )
    # 逐字同步：中间结果实时进输入框，ASR 修正时退格重打；
    # AI 修正或剪贴输入时被逻辑锁强制关闭
    inj_menu.addSeparator()
    act_live = inj_menu.addAction(t("menu.live_sync"))
    act_live.setCheckable(True)
    act_live.setChecked(bar._live_intermediate)
    act_live.setToolTip(t("menu.live_sync_tip"))
    act_live.triggered.connect(
        lambda checked: bar.signals.live_intermediate_toggled.emit(checked)
    )

    # AI 修正：结果先上屏，后台大模型修正错别字/口癖再替换；参数在设置页配置
    try:
        llm_on = bool(
            getattr(getattr(bar._full_cfg or bar.cfg, "llm", None), "enable", False)
        )
    except Exception:
        llm_on = False
    act_llm = inj_menu.addAction(t("menu.llm_correction"))
    act_llm.setCheckable(True)
    act_llm.setChecked(llm_on)
    act_llm.setToolTip(t("menu.llm_correction_tip"))
    act_llm.triggered.connect(
        lambda checked: bar.signals.llm_enable_toggled.emit(checked)
    )

    # 语音命令总开关：句首触发词执行命令不上屏；关闭则触发词当普通文本（commands.enable）
    try:
        cmd_on = bool(
            getattr(getattr(bar._full_cfg or bar.cfg, "commands", None),
                    "enable", False)
        )
    except Exception:
        cmd_on = False
    act_cmd = inj_menu.addAction(t("menu.voice_commands"))
    act_cmd.setCheckable(True)
    act_cmd.setChecked(cmd_on)
    act_cmd.setToolTip(t("menu.voice_commands_tip"))
    act_cmd.triggered.connect(
        lambda checked: bar.signals.commands_toggled.emit(checked)
    )

    # 引擎子菜单，每次构建都重新检测密钥：
    # - 分组标题不能用 addSection（QSS 的 separator 高 1px 会把标题压成线），改用禁用 action
    # - 过滤：engine_show_<provider> 手动隐藏；密钥未配置的组不显示，当前引擎所在组除外。
    #   funasr 不走密钥过滤（无密钥概念）：按依赖可用性过滤会让打包版里
    #   "引擎提供商显隐"开关永远被覆盖，显隐只由设置开关控制；依赖缺失时
    #   点击会走预热/启动错误路径给出明确提示
    engine_menu = menu.addMenu(t("menu.engine"))
    active_group = bar._engine_group_of(bar._current_engine or "tencent")
    # 逐模型显隐：设置页「引擎排序」折叠清单里关闭的模型不在菜单出现
    hidden_ids = {
        e.strip()
        for e in str(getattr(bar.cfg, "engine_hidden_models", "") or "").split(",")
        if e.strip()
    }
    # 先按全部过滤规则收集可见引擎，再按组渲染：
    # 整组都被隐藏（厂商开关关闭 / 所有模型隐藏）时不渲染孤立分组标题
    visible: list[tuple[str, str, str]] = []
    for engine_id, label in get_engines(getattr(bar.cfg, "engine_order", None)):
        group = bar._engine_group_of(engine_id)
        # 该提供商被设置里隐藏：整组跳过（标题与引擎项都不显示）
        if not bar._engine_group_visible(group):
            continue
        # 密钥未配置：隐藏，当前引擎组除外（funasr 无密钥，跳过此过滤）
        if group != "funasr" and group != active_group and not engine_group_configured(full_cfg, group):
            continue
        if engine_id in hidden_ids:
            continue
        visible.append((group, engine_id, label))

    def _add_engine_action(menu: QMenu, engine_id: str, label: str) -> None:
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(bar._current_engine == engine_id)
        action.triggered.connect(
            lambda checked, e=engine_id: bar.signals.engine_changed.emit(e)
        )

    # 云引擎分组渲染：对 ENGINE_CATEGORIES 注册的组（腾讯/阿里/讯飞/火山）
    # 只显示分类名（如 大模型2.0/大模型版/通用引擎/豆包大模型 2.0），点击
    # 即切换到该档配置的模型（设置页「引擎参数」下拉配置，见 presets.
    # provider_menu_items）；funasr 单项引擎保持平铺。组顺序跟随
    # get_engines，任一叶子可见即渲染该组；档配置的模型被隐藏时该分类
    # 项不显示
    rendered_groups: set[str] = set()
    current_group = None
    for group, engine_id, label in visible:
        if group in ENGINE_CATEGORIES:
            if group in rendered_groups:
                continue
            rendered_groups.add(group)
            # 局部名不能叫 `t`：本模块 import 了 i18n 的 t()，此处 `t` 的绑定
            # 会让整个函数里的 t("key") 变成 UnboundLocalError（守卫见
            # test_i18n.NoShadowingOfTranslationHelperTest）。
            items = [(slot, title, model_id)
                     for slot, title, model_id in provider_menu_items(group, full_cfg)
                     if model_id not in hidden_ids]
            if not items:
                continue
            engine_menu.addAction(_group_title(group)).setEnabled(False)
            for slot, title, model_id in items:
                action = engine_menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(bar._current_engine == model_id)
                action.triggered.connect(
                    lambda checked, m=model_id: bar.signals.engine_changed.emit(m)
                )
            continue
        if group != current_group:
            header = engine_menu.addAction(_group_title(group))
            header.setEnabled(False)  # 禁用项 = 分组标题，不可点击
            current_group = group
        _add_engine_action(engine_menu, engine_id, label)

    # 主题子菜单（内置主题 + 已加载主题包）：注册表按内部键索引，显示名单独取
    theme_menu = menu.addMenu(t("menu.theme"))
    for key, theme in available_themes().items():
        action = theme_menu.addAction(theme_display_name(key))
        action.setCheckable(True)
        action.setChecked(bar._current_theme_name == key)
        action.triggered.connect(
            lambda checked, n=key: bar.signals.theme_changed.emit(n)
        )

    menu.addSeparator()

    # 历史对话剪贴板（子菜单：独立窗口 + 开关 + 最近记录点击复制 + 清空）
    hist_menu = menu.addMenu(t("menu.history"))
    act_win = hist_menu.addAction(t("menu.history.open_window"))
    act_win.setToolTip(t("menu.history.open_window_tip"))
    act_win.triggered.connect(
        lambda: bar.signals.history_window_requested.emit()
    )
    act_hist = hist_menu.addAction(t("menu.history.enable"))
    act_hist.setCheckable(True)
    act_hist.setChecked(_history.is_enabled())
    act_hist.setToolTip(t("menu.history.enable_tip"))
    act_hist.triggered.connect(bar._on_history_toggled)
    hist_entries = _history.entries() if _history.is_enabled() else []
    if hist_entries:
        hist_menu.addSeparator()
        for text in hist_entries[:15]:
            display = text.replace("\n", " ")
            if len(display) > 30:
                display = display[:30] + "..."
            act_item = hist_menu.addAction(display)
            act_item.setToolTip(text)
            act_item.triggered.connect(
                lambda checked, text=text: bar._copy_history_item(text)
            )
        hist_menu.addSeparator()
        act_clear = hist_menu.addAction(t("menu.history.clear"))
        act_clear.triggered.connect(bar._on_clear_history)
    else:
        hint = hist_menu.addAction(
            t("menu.history.empty") if _history.is_enabled()
            else t("menu.history.disabled")
        )
        hint.setEnabled(False)

    # 字体子菜单：选择 .ttf/.otf 文件 / 恢复默认
    font_menu = menu.addMenu(t("menu.font"))
    act_choose_font = font_menu.addAction(t("menu.font.choose_file"))
    act_choose_font.triggered.connect(bar._on_choose_font_file)
    # 当前已选字体文件时，给出"恢复默认"快捷项
    from .fonts import _loaded_file_path as _cur_font_path
    if _cur_font_path:
        act_reset_font = font_menu.addAction(t("menu.font.reset"))
        act_reset_font.triggered.connect(
            lambda: bar.signals.font_file_changed.emit("")
        )

    # "设置"不带省略号：点了就直接弹窗，无需 "..." 提示
    act_settings = menu.addAction(t("menu.settings"))
    act_settings.triggered.connect(lambda: bar.signals.settings_requested.emit())

    menu.addSeparator()

    # 程序行为（子菜单：多选，自动隐藏 / 永远置顶 / 开机自启）
    window_menu = menu.addMenu(t("menu.behavior"))
    act_auto_hide = window_menu.addAction(t("menu.behavior.auto_hide"))
    act_auto_hide.setCheckable(True)
    act_auto_hide.setChecked(bar.is_auto_hide_enabled())
    act_auto_hide.setToolTip(t("menu.behavior.auto_hide_tip"))
    act_auto_hide.triggered.connect(
        lambda checked: bar.signals.auto_hide_toggled.emit(checked)
    )
    act_on_top = window_menu.addAction(t("menu.behavior.always_on_top"))
    act_on_top.setCheckable(True)
    act_on_top.setChecked(bar.is_always_on_top())
    act_on_top.setToolTip(t("menu.behavior.always_on_top_tip"))
    act_on_top.toggled.connect(bar._on_always_on_top_toggled)
    act_autostart = window_menu.addAction(t("menu.behavior.autostart"))
    act_autostart.setCheckable(True)
    # 每次构建菜单实时查询注册表（app 侧写/删后下次右键即反映最新状态）
    try:
        from ..core.autostart import is_enabled as _autostart_enabled
        act_autostart.setChecked(_autostart_enabled())
    except Exception:
        act_autostart.setChecked(False)
    act_autostart.setToolTip(t("menu.behavior.autostart_tip"))
    act_autostart.triggered.connect(
        lambda checked: bar.signals.autostart_toggled.emit(checked)
    )
    act_pause_bg = window_menu.addAction(t("menu.behavior.pause_media"))
    act_pause_bg.setCheckable(True)
    act_pause_bg.setChecked(bool(getattr(getattr(full_cfg, "recording", None),
                                         "pause_background_audio", False)))
    act_pause_bg.setToolTip(t("menu.behavior.pause_media_tip"))
    act_pause_bg.triggered.connect(
        lambda checked: bar.signals.pause_bg_audio_toggled.emit(checked)
    )
    # 长按说话：勾选态按当前配置实时给出；写配置、重建热键、以及在
    # 「热键解析不出主键」时明确报错都由 app._on_hold_to_talk_toggled 负责，
    # 故提示里只承诺到那里为止（不写"已开启"，那种情况下它其实不生效）。
    # 勾选态走与运行时**同一个** hold_flag_enabled：这里原先用 bool()，而运行时
    # reject 掉认不出的值（拼错的字符串、YAML 裸整数 1）——两套真值规则下，配置里
    # 手写一个裸 `1` 就是「菜单勾着、功能却是关的」，用户看到的是假象（"meby" 那个
    # 缺陷的镜像）。显示端必须与运行时同源，哪怕因此显示为「关」（fail closed）。
    act_hold = window_menu.addAction(t("menu.behavior.hold_to_talk"))
    act_hold.setCheckable(True)
    act_hold.setChecked(hold_flag_enabled(
        getattr(getattr(full_cfg, "hotkey", None), "hold_to_talk", False)))
    act_hold.setToolTip(t("menu.behavior.hold_to_talk_tip"))
    act_hold.triggered.connect(
        lambda checked: bar.signals.hold_to_talk_toggled.emit(checked)
    )
    # 程序提权：UAC 确认后以管理员身份重启接管；已是管理员则置灰不可点
    act_elevate = window_menu.addAction(t("menu.behavior.elevate"))
    try:
        from ..core.elevation import is_elevated as _is_elevated
        _elevated = _is_elevated()
    except Exception:
        _elevated = False
    if _elevated:
        act_elevate.setEnabled(False)
        act_elevate.setToolTip(t("menu.behavior.elevate.running_as_admin"))
        # 全局菜单 QSS 把 :disabled 项渲染成"分组标题"样式（加粗小字、
        # 左侧内缩 12px），禁用后的「程序提权」会字号变小且文字错位。
        # 「程序行为」子菜单内没有分组标题，这里覆盖一条规则：禁用项
        # 保持正常行的字号/字重/位置，仅文字置灰。置灰色沿用各主题
        # 各自的禁用色（默认浅/深、凯尔特、赛博朋克）
        if bar._knot_icon_mode:
            _dis_color = "#8a8a8a"
        elif bar._top_line_color is not None:
            _dis_color = bar._top_line_color.name()
        else:
            _dis_color = ("#8a8a94" if bar._is_light(bar._bg_color.name())
                          else "#9a9aa8")
        _menu_px = max(14, int(getattr(bar.cfg, "font_size_bar", 13) or 13))
        window_menu.setStyleSheet(
            "QMenu::item:disabled {"
            f" padding: 7px 28px; font-size: {_menu_px}px; font-weight: 500;"
            f" color: {_dis_color}; }}"
        )
    else:
        act_elevate.setToolTip(t("menu.behavior.elevate_tip"))
        act_elevate.triggered.connect(
            lambda: bar.signals.elevate_requested.emit()
        )

    # 打断方式（子菜单：多选，左键打断 / 删除键打断可同时开启）
    interrupt_menu = menu.addMenu(t("menu.interrupt"))
    act_int_click = interrupt_menu.addAction(t("menu.interrupt.on_click"))
    act_int_click.setToolTip(t("menu.interrupt.on_click_tip"))
    act_int_click.setCheckable(True)
    act_int_click.setChecked(bool(getattr(bar.cfg, "interrupt_on_click", False)))
    act_int_click.toggled.connect(bar._on_interrupt_toggled)
    act_int_delete = interrupt_menu.addAction(t("menu.interrupt.on_delete"))
    act_int_delete.setToolTip(t("menu.interrupt.on_delete_tip"))
    act_int_delete.setCheckable(True)
    act_int_delete.setChecked(bool(getattr(bar.cfg, "interrupt_on_delete", False)))
    act_int_delete.toggled.connect(bar._on_interrupt_delete_toggled)

    menu.addSeparator()

    # 退出
    act_exit = menu.addAction(t("menu.exit"))
    act_exit.triggered.connect(lambda: bar.signals.exit_requested.emit())

    # 主菜单和子菜单都要 TranslucentBackground，否则 QSS 的 rgba 背景被
    # 不透明窗底盖住。浅色菜单同样半透明（透明度与悬浮条一致），圆角 Win11 由 DWM 裁切
    menu.setAttribute(Qt.WA_TranslucentBackground)
    for sub in menu.findChildren(QMenu):
        sub.setAttribute(Qt.WA_TranslucentBackground)

    return menu


def prewarm_context_menu(bar: "FloatingBar") -> bool:
    """把「首次弹出菜单」的一次性成本从用户第一次右键挪到启动后。

    实测（Windows / PySide6，本机）：**首次** `menu.exec()` 弹出要
      * 创建 Qt::Popup 原生窗口（含 WA_TranslucentBackground 分层窗口 + DWM 合成）
        —— 连空白无 QSS 的 QMenu 首次都要 ~0.42s；
      * 首次解析菜单 QSS（QStyleSheetStyle 初始化）—— 再 ~0.26s；
    合计 ~0.65~0.76s；**第二次只要 ~0.02s**。用户机器上这套被放大到 3s+，
    表现为「初次启动后第一次右键要等一会儿」。

    做法：在屏幕外 popup 一次同样的菜单并立刻关闭。两条路径都走完，
    窗口类注册/样式引擎/分层窗合成都完成，而用户看不到任何东西。

    必须真的 popup：`WA_DontShowOnScreen` 只做到一半（实测仅 0.65→0.167s）。
    popup 之后**不需要** `processEvents()`——实测 `popup(); close()` 直连同样是
    0.017s，省掉它可避免在事件循环回调里重入。

    只构建/弹出、不触发任何 action，故无副作用。任何异常都吞掉并返回 False：
    预热失败只意味着"第一次右键慢一次"，绝不能让启动失败。
    """
    try:
        menu = build_context_menu(bar, bar)
        menu.popup(QPoint(-4000, -4000))
        menu.close()
        menu.deleteLater()
        logger.debug("右键菜单已预热")
        return True
    except Exception:      # noqa: BLE001  预热失败绝不能打断启动
        logger.debug("右键菜单预热失败（忽略；首次右键会稍慢）", exc_info=True)
        return False
