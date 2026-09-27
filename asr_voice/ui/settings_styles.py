"""设置对话框的配色预设与 QSS 生成。

从 settings_dialog.py 拆出：两套主题 palette（SETTINGS_STYLES）、
_settings_qss（palette -> 全控件 QSS）、_theme_palette（按配置选预设）。
与对话框布局逻辑无耦合，只依赖 palette 字典结构。
"""

from __future__ import annotations

from .fonts import get_loaded_family


def _theme_palette(cfg) -> dict:
    """设置弹窗配色：按 `ui.settings_style` 选预设，未知回退默认。

    每套预设是一组 palette 字典，覆盖页面/卡片/输入框/按钮/滑块/下拉/Toggle 等
    所有控件的色值。显示名走 i18n（见 set.style.*），键则是语言无关内部键
    （用户偏好四字名在 UI 层展示），新主题只需在 SETTINGS_STYLES 加一个键，
    _settings_qss 不需改动。
    """
    style_name = ""
    if cfg is not None:
        try:
            style_name = getattr(getattr(cfg, "ui", None), "settings_style", "") or ""
        except AttributeError:
            style_name = ""
    if style_name not in SETTINGS_STYLES:
        style_name = _DEFAULT_STYLE
    return dict(SETTINGS_STYLES[style_name])


# 主题键采用语言无关内部键（沿用 A4 ui.theme_name 的约定）：
# 键要写进 config.yaml、又要过 i18n，不能存中文显示名——英文用户的配置文件里
# 躺着"极简暗色"既看不懂也不可脚本化处理。显示名走 set.style.* 词条。
#   极简暗色 minimal_dark（默认）：参考图同款中性灰调——页面/面板纯灰阶，选中态白描边
#   流畅浅色 fluent_light：Win11 浅色，实底不透明观感
# 旧配置里的中文取值由 config/loader.py 一次性迁移（_migrate_legacy_settings_style）。
# accent 系列（主按钮/滑块已填段/开关开/焦点描边）统一中性灰而非蓝：
#   用户反馈蓝色太突兀，交互强调改为亮灰（深色主题）/深灰（浅色主题）
SETTINGS_STYLES: dict[str, dict] = {
    "minimal_dark": {
        "accent":          "#d6d6dc",
        "on_accent":       "#16161a",
        "text":            "#ececef",
        # 次级文字提亮：说明文字字号小、灰阶抗锯齿发软，提亮补对比
        "secondary":       "#a5a5ac",
        "page":            "#1c1c1f",
        # 纯色实底：无渐变无反光
        "panel":           "#242428",
        "input_bg":        "#2b2b31",
        # 输入框常态描边：比通用 border 亮一档——填充底色与卡片底几乎同色，
        # 无边框时输入框整个"消失"在背景里
        "input_border":    "#494952",
        "btn_bg":          "#2b2b31",
        "btn_hover":       "#3a3a41",
        "btn_pressed":     "#232327",
        "border":          "#3a3a41",
        # 发丝分割线：半透明白叠加，比实灰更细弱（参考图同款）
        "sep":             "rgba(255, 255, 255, 0.06)",
        # 区块分割线：比行间发丝线更醒目，划分扁平分区边界
        "group_sep":       "rgba(255, 255, 255, 0.16)",
        "slider_groove":   "rgba(255, 255, 255, 0.12)",
        "nav_sel":         "#2e2e35",
        "nav_text":        "#9d9da4",
        "card_bg":         "#26262b",
        "card_border":     "#38383f",
        "track_filled":    "#d6d6dc",
        "danger":          "#c23a3a",
        "danger_down":     "#9e2e2e",
        "combo_bg":        "#2b2b31",
        "combo_sel":       "#3c3c44",
        "combo_sel_text":  "#ffffff",
        "slider_handle":   "#ffffff",
        "slider_handle_border": "#1c1c1f",
        "save_bg":         "#e2e2e7",
        "save_text":       "#16161a",
        "save_hover":      "#f2f2f5",
        "save_hover_text": "#000000",
        "tooltip_bg":      "#2b2b31",
        "nav_hover":       "rgba(255, 255, 255, 0.05)",
        "selection_bg":    "rgba(255, 255, 255, 0.28)",
        "focus_border":    "#c9c9d1",
        "toggle_track_off": "#3d3d44",
        "toggle_track_on":  "#d6d6dc",
        "toggle_knob_off":  "#ffffff",
        "toggle_knob_on":   "#16161a",
        "theme_card_sel_border": "#e6e6ea",
        "root_border":     "rgba(255, 255, 255, 0.10)",
        # 「永久为管理员启动」全宽按钮：未开启蓝 / 已开启绿（功能态色，
        # 独立于中性灰 accent）；按钮上文字用深色保对比
        "admin_blue":        "#4cc2ff",
        "admin_blue_hover":  "#6fcdff",
        "admin_blue_down":   "#39a0dc",
        "admin_green":       "#6ccb5f",
        "admin_green_hover": "#84d47a",
        "admin_green_down":  "#57ab4e",
        "admin_text":        "#16161a",
        "radius": 8,
    },
    "fluent_light": {
        "accent":          "#4a4a52",
        "on_accent":       "#ffffff",
        "text":            "#1b1b1f",
        # 次级文字加深：原 #616161 在说明字号下偏浅
        "secondary":       "#55555c",
        "page":            "#f4f4f6",
        "panel":           "#ffffff",
        # 填充比白卡略深一档 + 可见描边：原 #fbfbfd 贴在白卡上等于隐形
        "input_bg":        "#f5f5f8",
        "input_border":    "#c6c6cf",
        "btn_bg":          "#f0f0f3",
        "btn_hover":       "#e6e6eb",
        "btn_pressed":     "#dcdce1",
        "border":          "#dcdce3",
        "track_filled":    "#4a4a52",
        "danger":          "#c42b1c",
        "danger_down":     "#a4262c",
        "combo_bg":        "#ffffff",
        "combo_sel":       "#e8e8ec",
        "combo_sel_text":  "#1b1b1f",
        "slider_handle":   "#ffffff",
        "slider_handle_border": "#8a8a94",
        "save_bg":         "#3a3a42",
        "save_text":       "#ffffff",
        "save_hover":      "#2f2f36",
        "save_hover_text": "#ffffff",
        "tooltip_bg":      "#ffffff",
        "nav_hover":       "rgba(0, 0, 0, 0.05)",
        "selection_bg":    "rgba(0, 0, 0, 0.18)",
        "focus_border":    "#4a4a52",
        "toggle_track_off": "#d4d4dc",
        "toggle_track_on":  "#4a4a52",
        "toggle_knob_off":  "#ffffff",
        "toggle_knob_on":   "#ffffff",
        "theme_card_sel_border": "#3a3a42",
        "sep":             "rgba(0, 0, 0, 0.07)",
        # 区块分割线：比行间发丝线更醒目，划分扁平分区边界
        "group_sep":       "rgba(0, 0, 0, 0.16)",
        "slider_groove":   "rgba(0, 0, 0, 0.10)",
        "nav_sel":         "#e9e9ee",
        "nav_text":        "#5d5d65",
        "card_bg":         "#ffffff",
        "card_border":     "#e3e3e9",
        "root_border":     "rgba(0, 0, 0, 0.14)",
        # 「永久为管理员启动」全宽按钮：未开启蓝 / 已开启绿（功能态色，
        # 独立于中性灰 accent）；按钮上文字用白色保对比
        "admin_blue":        "#0067c0",
        "admin_blue_hover":  "#1975c5",
        "admin_blue_down":   "#005fb8",
        "admin_green":       "#0f7b0f",
        "admin_green_hover": "#248a24",
        "admin_green_down":  "#0d650d",
        "admin_text":        "#ffffff",
        "radius": 8,
    },
}
_DEFAULT_STYLE = "minimal_dark"


def _settings_qss(p: dict) -> str:
    """生成设置弹窗 QSS：所有硬编码色改读 palette，支持两套预设换肤。

    设计要点：
    - 窗口不透明（透明表面会禁用 ClearType 致文字发糊），根背景用 page 实底；
      圆角 8px 与 Win11 DWM 自动裁切对齐，Win10 无系统圆角、呈方角。
    - **字号一律用 pt 不用 px**：QSS 的 px 字号是物理像素，不随 Windows
      显示缩放（125%/150%）放大，缩放屏上会小到看不清；pt（1/72 英寸）
      随系统 DPI 等比放大，与其他软件观感一致。几何尺寸（padding/border）
      仍用 px。基准：正文/标签 10.5pt，说明/副标题 10pt，页标题 15pt。
    - **设置界面字体栈**：普惠体优先（栈首；多字重已随 fonts/ 全量注册，
      400/500/700 各命中 55 Regular / 65 Medium / 85 Bold），与悬浮条/菜单统一；
      普惠体文件缺失（构建未投放 fonts/）时 Qt 自动落栈内系统回退位，不崩不空白。
    - **字重层级**：正文/描述/表单标签 400；分组标题、导航项、卡片标题 500；
      对话框大标题、主按钮 700。
    """
    r = p["radius"]
    # 控件内圆角：参考图（DSH 设置）输入框/按钮/卡片统一 ~8-10px 圆角
    ri = 8
    page = p["page"]
    root_bg = page
    root_border = p.get("root_border") or p["border"]
    root_top = root_border
    # 普惠体优先 + 系统回退：生效 family 为普惠体（内置默认/用户所选，
    # fonts/ 多字重已全量注册）时用它真实注册的 family 名置于栈首，
    # 400/500/700 各命中 55 Regular / 65 Medium / 85 Bold，与应用其他部分统一；
    # 普惠体文件缺失时 Qt 自动落栈内系统回退位，不崩不空白。
    # bold_stack 同栈，靠 font-weight 700 取 Bold 字重。
    # 字重层级：正文/描述/表单标签 400；分组标题/导航项/卡片标题 500；
    # 对话框大标题/主按钮 700（普惠体命中真实字面，系统回退位走各自近似匹配）
    fam = get_loaded_family()
    puhuiti_head = f"'{fam}', " if "puhuiti" in fam.lower() else "'Alibaba PuHuiTi 3.0', "
    font_stack = (puhuiti_head
                  + "'Segoe UI Variable Text', 'Segoe UI', "
                  + "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
    bold_stack = font_stack
    return f"""
    QDialog {{ background: {page}; font-family: {font_stack}; }}
    /* 无边框主题弹窗：平铺底色兜住四角，圆角面板内层呈现 */
    QDialog#promptDlg {{ background: {p['panel']}; }}
    QWidget#promptPanel {{
        background: {p['panel']}; border: 1px solid {p['border']};
        border-radius: {r}px;
    }}
    QWidget#dialogRoot {{
        background: {root_bg}; border: 1px solid {root_border};
        border-top: 1px solid {root_top};
        border-radius: {r}px;
    }}
    QWidget#titleBar {{ background: transparent; border: none; }}
    QLabel#dialogTitle {{
        background: transparent; color: {p['text']}; font-size: 12pt;
        font-family: {bold_stack}; font-weight: 700;
    }}
    /* 顶栏按钮（打开配置文件）：参考图同款灰底全圆角胶囊，无边框 */
    QPushButton#btnGhost {{
        background: {p['btn_bg']}; border: none; border-radius: 14px;
        padding: 6px 15px; color: {p['text']}; font-size: 9.5pt;
    }}
    QPushButton#btnGhost:hover {{ background: {p['btn_hover']}; }}
    QPushButton#btnGhost:pressed {{ background: {p['btn_pressed']}; }}
    /* 「永久为管理员启动」全宽动作按钮（Wallpaper Engine 式）：
       未开启蓝（btnWide）/ 已开启绿（btnWideGreen），objectName 切换后需 re-polish */
    QPushButton#btnWide {{
        background: {p['admin_blue']}; border: none; border-radius: 8px;
        min-height: 38px; padding: 0px 14px;
        color: {p['admin_text']}; font-size: 10pt;
    }}
    QPushButton#btnWide:hover {{ background: {p['admin_blue_hover']}; }}
    QPushButton#btnWide:pressed {{ background: {p['admin_blue_down']}; }}
    QPushButton#btnWideGreen {{
        background: {p['admin_green']}; border: none; border-radius: 8px;
        min-height: 38px; padding: 0px 14px;
        color: {p['admin_text']}; font-size: 10pt;
    }}
    QPushButton#btnWideGreen:hover {{ background: {p['admin_green_hover']}; }}
    QPushButton#btnWideGreen:pressed {{ background: {p['admin_green_down']}; }}
    QPushButton#btnClose {{
        background: transparent; border: none; border-radius: 6px;
        color: {p['secondary']}; font-size: 11pt; padding: 0px;
    }}
    QPushButton#btnClose:hover {{ background: {p['nav_hover']}; color: {p['text']}; }}
    QPushButton#btnClose:pressed {{ background: {p['btn_pressed']}; color: {p['text']}; }}
    QLabel {{ color: {p['text']}; background: transparent; }}
    /* 语音命令页「命令参考」折叠切换：左侧对齐的轻量文字按钮 */
    QPushButton#cmdRefToggle {{
        background: transparent; border: none; text-align: left;
        color: {p['secondary']}; font-size: 9.5pt; padding: 4px 2px;
    }}
    QPushButton#cmdRefToggle:hover {{ color: {p['text']}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    /* 左侧导航：图标+文字，选中项灰底圆角胶囊高亮（参考图样式）；
       常规项弱灰、悬停/选中转亮 */
    QListWidget#navList {{ background: transparent; border: none; outline: none; }}
    QListWidget#navList::item {{
        padding: 9px 12px; margin: 2px 12px; border-radius: 8px;
        color: {p['nav_text']}; background: transparent;
        font-family: {font_stack}; font-weight: 500; font-size: 12pt;
    }}
    QListWidget#navList::item:hover {{ background: {p['nav_hover']}; color: {p['text']}; }}
    QListWidget#navList::item:selected {{
        background: {p['nav_sel']}; color: {p['text']};
        font-family: {bold_stack}; font-weight: 700;
    }}
    QListWidget#navList::item:selected:hover {{
        background: {p['nav_sel']}; color: {p['text']};
    }}
    /* 可展开卡片（复刻参考图「插件配置」）：标题+说明+右箭头，选中展开；
       玻璃态用垂直渐变模拟磨砂玻璃厚度 */
    QWidget#accordionCard {{
        background: {p.get('card_gradient', p['card_bg'])};
        border: 1px solid {p['card_border']};
        border-radius: 10px;
    }}
    QLabel#accordionTitle {{
        color: {p['text']}; font-size: 11pt;
        font-family: {bold_stack}; font-weight: 500;
        background: transparent;
    }}
    QLabel#accordionDesc {{
        color: {p['secondary']}; font-size: 10pt; background: transparent;
    }}
    QLabel#accordionChevron {{
        color: {p['secondary']}; font-size: 13pt; background: transparent;
    }}
    /* 卡片标题/说明置灰（引擎排序：厂商总开关关闭时）——
       QSS 指定 color 后 disabled 调色板不再生效，必须显式给规则 */
    QLabel#accordionTitle:disabled, QLabel#accordionDesc:disabled {{
        color: {p['secondary']};
    }}
    /* —— 引擎排序专属：苹果「分组列表」风（inset grouped list）——
       整组一个大圆角容器，行间只用 inset 发丝分隔线；控件无框克制 */
    QWidget#engineOrderGroup {{
        background: {p['card_bg']};
        border: 1px solid {p['card_border']};
        border-radius: 10px;
    }}
    QWidget#eoVendorHeader, QWidget#eoVendorBlock, QWidget#eoModels {{
        background: transparent;
    }}
    /* 两行式头行：主标题 500，副标题灰色小字（iOS 设置的副标题语言） */
    QLabel#eoVendorName {{
        color: {p['text']}; font-size: 10.5pt;
        font-family: {bold_stack}; font-weight: 500; background: transparent;
    }}
    QLabel#eoVendorMeta {{
        color: {p['secondary']}; font-size: 8.5pt; background: transparent;
    }}
    QLabel#eoVendorName:disabled, QLabel#eoVendorMeta:disabled {{
        color: {p['secondary']};
    }}
    /* 当前引擎徽标：细描边小胶囊，克制点缀 */
    QLabel#eoBadge {{
        color: {p['secondary']}; font-size: 8pt; background: transparent;
        border: 1px solid {p['card_border']}; border-radius: 4px;
        padding: 0px 5px;
    }}
    QLabel#eoBadge:disabled {{ color: {p['secondary']}; }}
    /* inset 发丝分隔线：左缩进 36 对齐文本、右到边（iOS 标志性细节）。
       用 root_border（半透明 0.10/0.14）而非 sep（0.06/0.07）——
       组内分隔线需要比"点缀发丝"明显一档才读得出列表结构 */
    QFrame#eoSep {{
        background: {p['root_border']}; border: none; margin-left: 36px;
    }}
    /* 模型行名称：略小于厂商主标题，层级靠缩进+字号而非底色面板 */
    QLabel#eoModelName {{
        color: {p['text']}; font-size: 10pt; background: transparent;
    }}
    QLabel#eoModelName:disabled {{ color: {p['secondary']}; }}
    /* 排序步进器：iOS UIStepper 式实底胶囊分段——填充底+细描边把
       两个方向装成一个控件，箭头用正文色（显眼、可点感强）；
       悬停半区 btn_hover 加深、按压 selection_bg 强反馈。
       首/尾方向仅禁点击、无差异配色（QSS color 对 disabled 同样生效，
       不写 :disabled 规则即两端与其余箭头同色） */
    QFrame#eoStepper {{
        background: {p['input_bg']};
        border: 1px solid {p['input_border']};
        border-radius: 7px;
    }}
    QFrame#eoStepperSep {{
        background: {p['root_border']}; border: none;
    }}
    QPushButton#eoStepBtn {{
        background: transparent; color: {p['text']};
        border: none; border-radius: 5px; font-size: 9pt; padding: 0;
    }}
    QPushButton#eoStepBtn:hover {{
        background: {p['btn_hover']};
    }}
    QPushButton#eoStepBtn:pressed {{
        background: {p['selection_bg']};
    }}
    /* 组下脚注：iOS 设置 section footer 的灰色小字 */
    QLabel#eoFooter {{
        color: {p['secondary']}; font-size: 8.5pt; background: transparent;
    }}
    /* 扁平分区标题（参考图「外观」式纯文字标题，无卡片） */
    QLabel#sectionTitle {{
        color: {p['text']}; font-size: 11.5pt;
        font-family: {bold_stack}; font-weight: 500;
        background: transparent; padding: 2px 2px 8px;
    }}
    /* 行间发丝分割线（半透明叠加，弱于实色边框） */
    QFrame#rowSep {{ background: {p['sep']}; border: none; }}
    /* 扁平分区之间的区块分割线：比行间发丝线更醒目 */
    QFrame#groupSep {{ background: {p['group_sep']}; border: none; }}
    /* 分区内小节标题（大模型版/标准版、豆包 1.0/2.0 等） */
    QLabel#cardSection {{
        color: {p['secondary']}; font-size: 9.5pt;
        font-family: {bold_stack}; font-weight: 500;
        letter-spacing: 1px; background: transparent; margin-top: 12px;
    }}
    QLabel#fieldSection {{
        color: {p['text']}; font-size: 11pt;
        font-family: {bold_stack}; font-weight: 500;
        margin-top: 2px; margin-bottom: 2px; background: transparent;
    }}
    /* 识别引擎卡片：关闭显示时厂商名置灰 */
    QLabel#fieldSection:disabled {{ color: {p['secondary']}; }}
    /* 字段标签与行内说明：层级靠字号（11pt/10pt）区分；标签保持 400——
       普惠体命中 55 Regular 正文观感，系统回退位雅黑无 600 面也不会跳粗 */
    QLabel#fieldLabel {{ color: {p['text']}; font-size: 11pt; font-weight: 400; background: transparent; }}
    QLabel#fieldDesc {{
        color: {p['secondary']}; font-size: 10pt; background: transparent;
    }}
    /* 主题分段卡片（参考图「外观」通栏大卡）：选中亮描边 */
    QToolButton#themeCard {{
        background: {p.get('card_gradient', p['card_bg'])};
        border: 1px solid {p['card_border']};
        border-radius: 10px; padding: 14px 8px 12px; color: {p['text']};
        font-size: 9.5pt;
    }}
    QToolButton#themeCard:hover {{ border: 1px solid {p['secondary']}; }}
    QToolButton#themeCard:checked {{
        background: {p['nav_sel']}; border: 1px solid {p['theme_card_sel_border']};
    }}
    /* 输入类控件：填充式 + 常态可见描边（填充与卡底接近同色，没描边会隐形）；
       悬停微亮、聚焦换强调描边 */
    QLineEdit, QSpinBox, QComboBox {{
        background: {p['input_bg']}; border: 1px solid {p['input_border']};
        border-radius: {ri}px; padding: 6px 12px; color: {p['text']};
        selection-background-color: {p['selection_bg']};
    }}
    QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ background: {p['btn_hover']}; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
        background: {p['input_bg']}; border: 1px solid {p['focus_border']};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
        color: {p['secondary']}; border-color: {p['border']};
    }}
    QTextEdit {{
        background: {p['input_bg']}; border: 1px solid {p['input_border']};
        border-radius: {ri}px; padding: 6px 8px; color: {p['text']};
        selection-background-color: {p['selection_bg']};
        font-size: 10.5pt;
    }}
    QTextEdit:hover {{ background: {p['btn_hover']}; }}
    QTextEdit:focus {{ background: {p['input_bg']}; border: 1px solid {p['focus_border']}; }}
    QTextEdit:disabled {{ color: {p['secondary']}; border-color: {p['border']}; }}
    QTextEdit::placeholder {{ color: {p['secondary']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{
        width: 0; height: 0; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {p['secondary']};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background: {p['combo_bg']}; color: {p['text']};
        border: 1px solid {p['border']}; border-radius: {ri}px;
        selection-background-color: {p['combo_sel']};
        selection-color: {p['combo_sel_text']}; outline: none; padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{ padding: 6px 10px; border-radius: 4px; }}
    QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {p.get('slider_groove', p['border'])}; }}
    QSlider::sub-page:horizontal {{ background: {p['track_filled']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
        background: {p['slider_handle']}; border: 2px solid {p['slider_handle_border']};
    }}
    QSlider::handle:horizontal:hover {{ background: {p['slider_handle']}; border: 2px solid {p['secondary']}; }}
    /* 按钮：无边框灰底圆角块（参考图风格），主按钮用亮色区分 */
    QPushButton {{
        padding: 7px 18px; border-radius: {ri}px;
        background: {p['btn_bg']}; color: {p['text']}; border: none;
    }}
    QPushButton:hover {{ background: {p['btn_hover']}; }}
    QPushButton:pressed {{ background: {p['btn_pressed']}; }}
    QPushButton:disabled {{ color: {p['secondary']}; background: {p['btn_bg']}; }}
    /* 小图标按钮（↑↓ 排序等）：灰底圆角小方块 + 亮箭头，一眼可见；
       悬停亮箭头提示可点。注意：QSS 里不能再写 min-width/min-height——
       样式表的尺寸属性会覆盖代码里的 setFixedSize，按钮会被布局压成
       sizeHint 大小 */
    QPushButton#iconBtn {{
        background: {p['btn_bg']}; border: none;
        border-radius: 8px; color: {p['text']};
        padding: 0px; font-size: 10pt;
    }}
    QPushButton#iconBtn:hover {{ background: {p['btn_hover']}; color: {p['accent']}; }}
    QPushButton#iconBtn:pressed {{ background: {p['btn_pressed']}; }}
    QPushButton#btnSave {{
        background: {p['save_bg']}; color: {p['save_text']}; border: none;
        font-family: {bold_stack}; font-weight: 700; padding: 8px 24px;
    }}
    QPushButton#btnSave:hover {{ background: {p['save_hover']}; color: {p['save_hover_text']}; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {p['border']}; border-radius: 4px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p['secondary']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    /* QToolTip 是独立顶级窗口，不继承 QDialog 的字体栈，必须显式声明
       字体与字号，否则回落到应用默认字体（可能是单字重的用户字体） */
    QToolTip {{
        background: {p['tooltip_bg']}; color: {p['text']};
        border: 1px solid {p['border']}; padding: 6px 10px; border-radius: 6px;
        font-family: {font_stack}; font-size: 10pt;
    }}
    """


def _welcome_qss(p: dict) -> str:
    """首启欢迎框 QSS：与 _settings_qss 同 palette、同字号/字重约定。

    结构对应 welcome_dialog.WelcomeDialog 的 objectName：
    welcomeRoot（圆角根面板）/ titleBar+btnClose（自绘标题栏）/
    heroTitle+heroSub（主副标题）/ card+tile+tileKey+cardTitle+cardDesc
    （三张引导卡，tileKey 是热键 keycap）/ privacyLink（次级色纯文本按钮）/
    btnGhost+btnPrimary（次/主按钮，主按钮沿用设置页保存钮的 save_* 色）。

    字号一律 pt（随系统 DPI 缩放），几何尺寸用 px——约定同 _settings_qss。
    """
    r = p["radius"]
    fam = get_loaded_family()
    puhuiti_head = f"'{fam}', " if "puhuiti" in fam.lower() else "'Alibaba PuHuiTi 3.0', "
    font_stack = (puhuiti_head
                  + "'Segoe UI Variable Text', 'Segoe UI', "
                  + "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
    return f"""
    QDialog {{ background: {p['page']}; font-family: {font_stack}; }}
    /* 无边框圆角根面板：不透明实底（透明表面禁 ClearType 致文字发糊），
       Win11 圆角由 DWM 裁切，Win10 呈方角——与设置弹窗一致 */
    QWidget#welcomeRoot {{
        background: {p['page']}; border: 1px solid {p['root_border']};
        border-radius: {r}px;
    }}
    QWidget#titleBar {{ background: transparent; border: none; }}
    QPushButton#btnClose {{ background: transparent; border: none; border-radius: 6px; }}
    QPushButton#btnClose:hover {{ background: {p['nav_hover']}; }}
    QLabel#heroTitle {{
        background: transparent; color: {p['text']}; font-size: 15pt;
        font-family: {font_stack}; font-weight: 700;
    }}
    QLabel#heroSub {{
        background: transparent; color: {p['secondary']}; font-size: 10pt;
    }}
    QFrame#card {{
        background: {p['card_bg']}; border: 1px solid {p['card_border']};
        border-radius: 10px;
    }}
    QLabel#tile {{
        background: {p['input_bg']}; border: 1px solid {p['border']};
        border-radius: 9px;
    }}
    /* 热键 keycap：比通用 tile 亮一档描边，键帽感 */
    QLabel#tileKey {{
        background: {p['input_bg']}; border: 1px solid {p['input_border']};
        border-radius: 9px; color: {p['text']}; font-size: 9.5pt; font-weight: 700;
    }}
    QLabel#cardTitle {{
        background: transparent; color: {p['text']};
        font-size: 10.5pt; font-weight: 500;
    }}
    QLabel#cardDesc {{
        background: transparent; color: {p['secondary']}; font-size: 10pt;
    }}
    /* 隐私政策：次级色纯文本按钮（富文本链接的蓝色 QSS 管不住，改按钮） */
    QPushButton#privacyLink {{
        background: transparent; border: none; padding: 0;
        color: {p['secondary']}; font-size: 9.5pt; text-align: left;
    }}
    QPushButton#privacyLink:hover {{ color: {p['text']}; }}
    QPushButton#btnGhost {{
        background: {p['btn_bg']}; border: none; border-radius: 8px;
        padding: 7px 16px; color: {p['text']}; font-size: 10pt;
    }}
    QPushButton#btnGhost:hover {{ background: {p['btn_hover']}; }}
    QPushButton#btnGhost:pressed {{ background: {p['btn_pressed']}; }}
    QPushButton#btnPrimary {{
        background: {p['save_bg']}; border: none; border-radius: 8px;
        padding: 7px 22px; color: {p['save_text']}; font-size: 10pt; font-weight: 700;
    }}
    QPushButton#btnPrimary:hover {{
        background: {p['save_hover']}; color: {p['save_hover_text']};
    }}
    QPushButton#btnPrimary:pressed {{ background: {p['btn_pressed']}; }}
    """


def _error_qss(p: dict) -> str:
    """启动期致命错误弹窗 QSS：与设置弹窗同源的无边框圆角卡片。

    主按钮复用设置界面的 save_* 主按钮色族（中性灰强调），保持全应用
    交互强调一致；正文左缩进 52px 与标题对齐（图标 38 + 间距 14）。
    """
    return f"""
    QWidget#errDlg {{ background: transparent; }}
    QWidget#errPanel {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QLabel#errTitle {{
        color: {p['text']};
        font-size: 13pt;
        font-weight: 600;
    }}
    QLabel#errBody {{
        color: {p['secondary']};
        font-size: 10pt;
        padding: 2px 0 0 52px;
    }}
    QPushButton#errOk {{
        background: {p['save_bg']};
        color: {p['save_text']};
        border: none;
        border-radius: 6px;
        font-size: 10pt;
        font-weight: 600;
    }}
    QPushButton#errOk:hover {{ background: {p['save_hover']}; color: {p['save_hover_text']}; }}
    QPushButton#errOk:pressed {{ background: {p['btn_pressed']}; }}
    """
