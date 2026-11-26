"""设置对话框的配色预设与 QSS 生成。

从 settings_dialog.py 拆出：两套主题 palette（SETTINGS_STYLES）、
_settings_qss（palette -> 全控件 QSS）、_theme_palette（按配置选预设）。
与对话框布局逻辑无耦合，只依赖 palette 字典结构。
"""

from __future__ import annotations

def _theme_palette(cfg) -> dict:
    """设置弹窗配色：按 `ui.settings_style` 选预设，未知回退默认。
    每套预设覆盖全部控件的色值，显示名走 i18n（set.style.*）、键为语言无关内部键；
    新主题只需在 SETTINGS_STYLES 加键，_settings_qss 不需改动。
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


# 主题键用语言无关内部键（键要进 config.yaml 又要过 i18n，不能存中文显示名；显示名走 set.style.*）。
# minimal_dark（默认，中性灰调）/ fluent_light（Win11 浅色）；
# 旧配置的中文取值由 loader 一次性迁移。
# accent 系列（主按钮/滑块已填段/开关开/焦点描边）统一中性灰而非蓝——蓝色太突兀，
# 交互强调用亮灰（深色）/深灰（浅色）。
SETTINGS_STYLES: dict[str, dict] = {
    "minimal_dark": {
        "accent":          "#d6d6dc",
        "on_accent":       "#16161a",
        "text":            "#ececef",
        # 次级文字提亮：说明文字字号小、灰阶抗锯齿发软，提亮补对比
        "secondary":       "#a5a5ac",
        # 行内说明单列一档：小字号灰阶抗锯齿边缘发虚，比次级文字再亮一档
        "desc":            "#c2c2ca",
        "page":            "#1c1c1f",
        # 纯色实底：无渐变无反光
        "panel":           "#242428",
        "input_bg":        "#2b2b31",
        # 输入框常态描边：比通用 border 亮一档——填充底色与卡片底几乎同色
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
        # 「永久为管理员启动」全宽按钮：未开启蓝 / 已开启绿（功能态色
        # 独立于中性灰 accent）；按钮上文字用深色保对比
        "admin_blue":        "#4cc2ff",
        "admin_blue_hover":  "#6fcdff",
        "admin_blue_down":   "#39a0dc",
        "admin_green":       "#6ccb5f",
        "admin_green_hover": "#84d47a",
        "admin_green_down":  "#57ab4e",
        "admin_text":        "#16161a",
        # Steam 页连接状态条：绿=已连接 / 琥珀=未连接（半透明底 + 同色文字）
        "banner_ok_bg":      "rgba(108, 203, 95, 0.12)",
        "banner_ok_text":    "#8fdc84",
        "banner_ok_dot":     "#6ccb5f",
        "banner_warn_bg":    "rgba(255, 185, 70, 0.12)",
        "banner_warn_text":  "#f0b455",
        "banner_warn_dot":   "#e0a03a",
        "radius": 8,
    },
    "fluent_light": {
        "accent":          "#4a4a52",
        "on_accent":       "#ffffff",
        "text":            "#1b1b1f",
        # 次级文字加深：原 #616161 在说明字号下偏浅
        "secondary":       "#55555c",
        # 行内说明单列一档：小字号灰阶抗锯齿边缘发虚，比次级文字再深一档
        "desc":            "#45454d",
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
        # 「永久为管理员启动」全宽按钮：未开启蓝 / 已开启绿（功能态色
        # 独立于中性灰 accent）；按钮上文字用白色保对比
        "admin_blue":        "#0067c0",
        "admin_blue_hover":  "#1975c5",
        "admin_blue_down":   "#005fb8",
        "admin_green":       "#0f7b0f",
        "admin_green_hover": "#248a24",
        "admin_green_down":  "#0d650d",
        "admin_text":        "#ffffff",
        # Steam 页连接状态条：绿=已连接 / 琥珀=未连接（半透明底 + 同色文字）
        "banner_ok_bg":      "rgba(15, 123, 15, 0.10)",
        "banner_ok_text":    "#0f7b0f",
        "banner_ok_dot":     "#0f7b0f",
        "banner_warn_bg":    "rgba(157, 93, 0, 0.10)",
        "banner_warn_text":  "#8a5a00",
        "banner_warn_dot":   "#b37700",
        "radius": 8,
    },
}
_DEFAULT_STYLE = "minimal_dark"


def _settings_qss(p: dict) -> str:
    """生成设置弹窗 QSS：所有硬编码色改读 palette，支持两套预设换肤。

    窗口不透明（透明表面禁 ClearType 致文字发糊），根背景用 page 实底、圆角 8px。**字号一律用 pt 不用 px**
    （px 不随 DPI 缩放放大、缩放屏看不清），几何尺寸仍用 px；字体栈普惠体优先（缺失时 Qt 落系统回退位），字重层级 400/500/700。
    """
    r = p["radius"]
    # 控件内圆角：参考图（DSH 设置）输入框/按钮/卡片统一 ~8-10px 圆角
    ri = 8
    page = p["page"]
    root_bg = page
    root_border = p.get("root_border") or p["border"]
    root_top = root_border
    # 与交互原型保持一致：原型浏览器实际命中的是普惠体，所以普惠体置顶；
    # 系统 UI 字体只做回退。bold_stack 同栈，靠 font-weight 700 取 Bold 字重。
    # 字重层级：正文/描述/表单标签 400，分组标题/导航项/卡片标题 500，
    # 对话框大标题/主按钮 700。
    font_stack = ("'Alibaba PuHuiTi 3.0', "
                  "'Segoe UI Variable Text', 'Segoe UI', "
                  "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
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
        border-radius: 12px;
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
    /* 页面头部：标题 + 一句说明（方案 A 的页头语言） */
    QLabel#pageTitle {{
        color: {p['text']}; font-size: 12pt;
        font-family: {bold_stack}; font-weight: 600;
        background: transparent;
    }}
    QLabel#pageDesc {{
        color: {p['secondary']}; font-size: 9.5pt;
        background: transparent; padding-top: 2px;
    }}
    /* 分组标题：卡片之外的小号灰字，和卡片留白形成层级 */
    QLabel#groupTitle {{
        color: {p['secondary']}; font-size: 9pt;
        font-family: {bold_stack}; font-weight: 500;
        letter-spacing: 1px; background: transparent;
        padding: 0px 4px 6px;
    }}
    /* 分组卡片：一组的行都住在一个圆角容器里，行间只用发丝线 */
    QWidget#groupCard {{
        background: {p['card_bg']}; border: 1px solid {p['card_border']};
        border-radius: 12px;
    }}
    QFrame#groupRowSep {{ background: {p['sep']}; border: none; }}
    /* 命令参考展开区：和分组卡同一套外观，避免展开后变成裸文字 */
    QWidget#cmdRefBody {{
        background: {p['card_bg']}; border: 1px solid {p['card_border']};
        border-radius: 12px;
    }}
    /* 自定义短语词表：表头/行/选中态全部走设置窗调色板 */
    QWidget#phraseCard {{
        background: {p['card_bg']}; border: 1px solid {p['card_border']};
        border-radius: 12px;
    }}
    QTableWidget#phraseTable {{
        background: transparent; border: none; color: {p['text']};
        selection-background-color: {p['nav_sel']};
        selection-color: {p['text']};
    }}
    QTableWidget#phraseTable::item {{
        padding: 6px 8px; border-bottom: 1px solid {p['sep']};
    }}
    /* 词表单元格编辑器：内边距收窄，行高 34px 内文字完整可见 */
    QTableWidget#phraseTable QLineEdit {{
        background: {p['input_bg']}; border: 1px solid {p['focus_border']};
        border-radius: 6px; padding: 1px 8px; color: {p['text']};
        selection-background-color: {p['selection_bg']};
    }}
    QTableWidget#phraseTable QHeaderView::section {{
        background: {p['input_bg']}; color: {p['secondary']};
        border: none; padding: 6px 8px;
        font-family: {font_stack}; font-size: 9pt; font-weight: 500;
    }}
    QTableCornerButton::section {{
        background: {p['input_bg']}; border: none;
    }}
    /* 搜索命中后的短暂高亮：用一档明显但不刺眼的按钮悬停色 */
    QWidget#settingRow[highlight="true"] {{
        background: {p['btn_hover']}; border-radius: 8px;
    }}
    /* 侧边栏搜索框：比表单输入框更紧凑，和导航项同宽 */
    QLineEdit#searchEdit {{
        background: {p['input_bg']}; border: 1px solid {p['input_border']};
        border-radius: 8px; padding: 5px 10px; color: {p['text']};
        font-size: 9.5pt; selection-background-color: {p['selection_bg']};
    }}
    QLineEdit#searchEdit:hover {{ background: {p['btn_hover']}; }}
    QLineEdit#searchEdit:focus {{
        background: {p['input_bg']}; border: 1px solid {p['focus_border']};
    }}
    /* 搜索结果：整行左对齐的轻量按钮，页面名跟在标题后 */
    QPushButton#searchResult {{
        background: transparent; border: none; border-radius: 8px;
        color: {p['text']}; text-align: left; padding: 8px 10px;
        font-size: 10.5pt;
    }}
    QPushButton#searchResult:hover {{ background: {p['nav_hover']}; }}
    QLabel#searchGroup {{
        color: {p['secondary']}; font-size: 9.5pt;
        font-family: {bold_stack}; font-weight: 500;
        background: transparent; padding: 12px 4px 4px;
    }}
    QLabel#searchEmpty {{
        color: {p['secondary']}; font-size: 10pt;
        background: transparent; padding: 18px 4px;
    }}
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
        font-family: {font_stack}; font-weight: 500; font-size: 10.5pt;
    }}
    QListWidget#navList::item:hover {{ background: {p['nav_hover']}; color: {p['text']}; }}
    QListWidget#navList::item:selected {{
        background: {p['nav_sel']}; color: {p['text']};
        font-family: {bold_stack}; font-weight: 500;
    }}
    QListWidget#navList::item:selected:hover {{
        background: {p['nav_sel']}; color: {p['text']};
    }}
    /* 可展开卡片（复刻参考图「插件配置」）：标题+说明+右箭头，选中展开；
       玻璃态用垂直渐变模拟磨砂玻璃厚度 */
    QWidget#accordionCard {{
        background: {p.get('card_gradient', p['card_bg'])};
        border: 1px solid {p['card_border']};
        border-radius: 12px;
    }}
    QLabel#accordionTitle {{
        color: {p['text']}; font-size: 10.5pt;
        font-family: {bold_stack}; font-weight: 500;
        background: transparent;
    }}
    QLabel#accordionDesc {{
        color: {p['secondary']}; font-size: 9.5pt; background: transparent;
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
        border-radius: 12px;
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
    /* inset 发丝分隔线：左缩进 48 对齐文本、右到边（iOS 标志性细节）。
       用 root_border（半透明 0.10/0.14）而非 sep（0.06/0.07）——
       组内分隔线需要比"点缀发丝"明显一档才读得出列表结构 */
    QFrame#eoSep {{
        background: {p['root_border']}; border: none; margin-left: 48px;
    }}
    /* 行首序号：拖动重排后由代码刷新，弱灰小字不与厂商名抢层级 */
    QLabel#eoIndex {{
        color: {p['secondary']}; font-size: 8.5pt; background: transparent;
    }}
    /* 模型行名称：略小于厂商主标题，层级靠缩进+字号而非底色面板 */
    QLabel#eoModelName {{
        color: {p['text']}; font-size: 10pt; background: transparent;
    }}
    QLabel#eoModelName:disabled {{ color: {p['secondary']}; }}
    /* 组下脚注：iOS 设置 section footer 的灰色小字 */
    QLabel#eoFooter {{
        color: {p['secondary']}; font-size: 8.5pt; background: transparent;
    }}
    /* 扁平分区标题（参考图「外观」式纯文字标题，无卡片） */
    QLabel#sectionTitle {{
        color: {p['text']}; font-size: 10.5pt;
        font-family: {bold_stack}; font-weight: 500;
        background: transparent; padding: 2px 2px 8px;
    }}
    /* 行间发丝分割线（半透明叠加，弱于实色边框） */
    QFrame#rowSep {{ background: {p['sep']}; border: none; }}
    /* 扁平分区之间的区块分割线：比行间发丝线更醒目 */
    QFrame#groupSep {{ background: {p['group_sep']}; border: none; }}
    /* 分区内小节标题（大模型版/标准版、豆包 1.0/2.0 等） */
    QLabel#cardSection {{
        color: {p['secondary']}; font-size: 9pt;
        font-family: {bold_stack}; font-weight: 500;
        letter-spacing: 1px; background: transparent; margin-top: 12px;
    }}
    QLabel#fieldSection {{
        color: {p['text']}; font-size: 10.5pt;
        font-family: {bold_stack}; font-weight: 500;
        margin-top: 2px; margin-bottom: 2px; background: transparent;
    }}
    /* 识别引擎卡片：关闭显示时厂商名置灰 */
    QLabel#fieldSection:disabled {{ color: {p['secondary']}; }}
    /* 字段标签与行内说明：层级靠字号（10.5pt/9.5pt）区分；标签保持 400——
       系统雅黑与普惠体都没有 600 面时也不会跳粗 */
    QLabel#fieldLabel {{ color: {p['text']}; font-size: 10.5pt; font-weight: 400; background: transparent; }}
    QLabel#fieldDesc {{
        color: {p.get('desc', p['secondary'])}; font-size: 9.5pt; background: transparent;
    }}
    /* 状态/操作反馈行：比说明更小，左侧细线区分「这是一条状态」而不是正文 */
    QLabel#fieldState {{
        color: {p['secondary']}; font-size: 9pt;
        padding: 2px 0 2px 9px;
        border-left: 2px solid {p['accent']};
        background: transparent;
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
    /* 热键显示用等宽字体（与原型一致）：组合键字符宽度稳定，不随字号跳动 */
    QLineEdit#hotkeyEdit {{
        font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
        font-size: 9.5pt;
    }}
    /* 滑块右侧数值：原型是纯文本，这里保留可编辑但去掉常态边框 */
    QSpinBox#sliderSpin {{
        background: transparent; border: 1px solid transparent;
        border-radius: 6px; padding: 2px 4px; color: {p['text']};
        font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
        font-size: 9.5pt;
    }}
    QSpinBox#sliderSpin:hover {{ background: {p['btn_hover']}; }}
    QSpinBox#sliderSpin:focus {{
        background: {p['input_bg']}; border: 1px solid {p['focus_border']};
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
    /* 多行文本框：悬停不换背景（大面积底色跳变很扎眼），只微亮边框；
       聚焦边框再用 focus_border 强化层级 */
    QTextEdit:hover {{ border: 1px solid {p['btn_hover']}; }}
    QTextEdit:focus {{ background: {p['input_bg']}; border: 1px solid {p['focus_border']}; }}
    QTextEdit:disabled {{ color: {p['secondary']}; border-color: {p['border']}; }}
    QTextEdit::placeholder {{ color: {p['secondary']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
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
    /* ---- 创意工坊卡片 ----
       选择器一律带 workshop 前缀，**只作用于这一张卡**：改共享选择器会
       连带改坏其他页。*/
    QListWidget#workshopList {{
        background: {p['input_bg']};
        border: 1px solid {p['card_border']};
        border-radius: 10px;
        padding: 3px;
        outline: none;
    }}
    /* 行控件自己撑高度（item 的 sizeHint 见 _fill_workshop_list_*），
       这里只管行间留白与圆角高亮 */
    QListWidget#workshopList::item {{
        border-radius: 8px;
        margin: 2px 3px;
    }}
    QListWidget#workshopList::item:hover {{ background: {p['nav_hover']}; }}
    QListWidget#workshopList::item:selected {{ background: {p['nav_sel']}; }}
    QLabel#workshopRowTitle {{
        color: {p['text']}; font-size: 10pt; background: transparent;
    }}
    QLabel#workshopRowSub {{
        color: {p['secondary']}; font-size: 9pt; background: transparent;
    }}
    /* 状态徽标：四态只在"填充/描边/字色"上区分，都取自中性灰调色板
       —— 这套界面整体不引入色相（见 _theme_palette 的说明）。
       选择器**必须一个一行**：Qt 的 QSS 解析器不接受跨行的逗号选择器列表，
       写成多行会把从这里往后的整段样式一起丢掉，且不报任何错。 */
    QLabel#workshopChipDone {{ font-size: 9pt; border-radius: 9px; padding: 2px 9px;
        background: {p['accent']}; color: {p['on_accent']}; border: none; }}
    QLabel#workshopChipBusy {{ font-size: 9pt; border-radius: 9px; padding: 2px 9px;
        background: transparent; color: {p['accent']}; border: 1px solid {p['accent']}; }}
    QLabel#workshopChipTodo {{ font-size: 9pt; border-radius: 9px; padding: 2px 9px;
        background: transparent; color: {p['text']}; border: 1px solid {p['border']}; }}
    QLabel#workshopChipIdle {{ font-size: 9pt; border-radius: 9px; padding: 2px 9px;
        background: transparent; color: {p['secondary']}; border: 1px solid {p['border']}; }}
    QProgressBar#workshopProgress {{
        background: {p['input_border']}; border: none; border-radius: 2px;
    }}
    QProgressBar#workshopProgress::chunk {{
        background: {p['accent']}; border-radius: 2px;
    }}
    /* 本卡的主按钮：几何与 btnGhost 完全一致，只换成主按钮色族，
       让「浏览社区创意工坊」在三个按钮里有唯一的视觉重心 */
    QPushButton#workshopPrimary {{
        background: {p['save_bg']}; color: {p['save_text']}; border: none;
        border-radius: 14px; padding: 6px 15px; font-size: 9.5pt;
        font-weight: 600;
    }}
    QPushButton#workshopPrimary:hover {{
        background: {p['save_hover']}; color: {p['save_hover_text']};
    }}
    QPushButton#workshopPrimary:pressed {{ background: {p['btn_pressed']}; }}
    /* Steam 页连接状态条：绿=客户端已连接 / 琥珀=未连接 */
    QWidget#steamStatusBanner {{
        background: {p['banner_ok_bg']}; border: none; border-radius: 8px;
    }}
    QWidget#steamStatusBanner[state="warn"] {{ background: {p['banner_warn_bg']}; }}
    QWidget#steamStatusDot {{
        background: {p['banner_ok_dot']}; border: none; border-radius: 3px;
    }}
    QWidget#steamStatusBanner[state="warn"] QWidget#steamStatusDot {{
        background: {p['banner_warn_dot']};
    }}
    QLabel#steamStatusText {{
        color: {p['banner_ok_text']}; background: transparent;
        font-size: 9.5pt; font-weight: 500;
    }}
    QWidget#steamStatusBanner[state="warn"] QLabel#steamStatusText {{
        color: {p['banner_warn_text']};
    }}
    /* 蓝色主按钮：仅用于「安装扩展包」这类一次性行动入口，其余按钮保持中性 */
    QPushButton#btnAccent {{
        background: {p['admin_blue']}; color: {p['admin_text']}; border: none;
        border-radius: 14px; padding: 6px 15px; font-size: 9.5pt;
        font-weight: 600;
    }}
    QPushButton#btnAccent:hover {{ background: {p['admin_blue_hover']}; }}
    QPushButton#btnAccent:pressed {{ background: {p['admin_blue_down']}; }}
    QPushButton#btnAccent:disabled {{
        background: {p['btn_bg']}; color: {p['secondary']};
    }}
    """


def _welcome_qss(p: dict) -> str:
    """首启欢迎框 QSS：与 _settings_qss 同 palette、同字号（pt）/字重约定。
    结构对应 welcome_dialog.WelcomeDialog 的 objectName 清单：
    welcomeRoot / titleBar+btnClose / heroTitle+heroSub / card+tile+tileKey+cardTitle+cardDesc /
    privacyLink / btnGhost+btnPrimary（tileKey 是热键 keycap）。
    """
    r = p["radius"]
    font_stack = ("'Alibaba PuHuiTi 3.0', "
                  "'Segoe UI Variable Text', 'Segoe UI', "
                  "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
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

    字体栈、标题层级（12pt/600）、正文色与主按钮几何全部对齐设置界面；
    主按钮复用 save_* 色族（中性灰强调），保持全应用交互强调一致。
    """
    font_stack = ("'Alibaba PuHuiTi 3.0', "
                  "'Segoe UI Variable Text', 'Segoe UI', "
                  "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
    return f"""
    QWidget#errDlg {{ background: transparent; }}
    QWidget#errPanel {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QDialog#errDlg, QLabel#errTitle, QLabel#errBody, QLabel#errHint,
    QLabel#errFieldLabel, QLabel#errHintLine, QLabel#errInline,
    QPushButton#errOk, QPushButton#errGhost {{
        font-family: {font_stack};
    }}
    QLabel#errTitle {{
        color: {p['text']};
        font-size: 12pt;
        font-weight: 600;
    }}
    QLabel#errBody {{
        color: {p['text']};
        font-size: 10pt;
    }}
    /* 操作提示块：浅色小卡，与设置界面的说明块同一层级语言 */
    QWidget#errHintCard {{
        background: {p['input_bg']};
        border: 1px solid {p['border']};
        border-radius: 8px;
    }}
    QLabel#errHint {{
        color: {p['desc']};
        font-size: 9.5pt;
        background: transparent;
    }}
    /* 热键冲突弹窗：直接录制新组合的字段区 */
    QLabel#errFieldLabel {{
        color: {p['text']};
        font-size: 10.5pt;
        font-weight: 500;
    }}
    QLineEdit#hotkeyEdit {{
        background: {p['input_bg']}; border: 1px solid {p['input_border']};
        border-radius: 8px; padding: 8px 12px; color: {p['text']};
        selection-background-color: {p['selection_bg']};
        selection-color: {p['text']};
        font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
        font-size: 10pt;
    }}
    QLineEdit#hotkeyEdit:focus {{ border: 1px solid {p['focus_border']}; }}
    QLabel#errHintLine {{
        color: {p['desc']};
        font-size: 9pt;
    }}
    QLabel#errInline {{
        color: {p['danger']};
        font-size: 9.5pt;
    }}
    QPushButton#errGhost {{
        background: {p['btn_bg']}; color: {p['text']}; border: none;
        border-radius: 8px; padding: 7px 18px; font-size: 10pt;
    }}
    QPushButton#errGhost:hover {{ background: {p['btn_hover']}; }}
    QPushButton#errGhost:pressed {{ background: {p['btn_pressed']}; }}
    QPushButton#errOk {{
        background: {p['save_bg']};
        color: {p['save_text']};
        border: none;
        border-radius: 8px;
        padding: 7px 22px;
        font-size: 10pt;
        font-weight: 700;
    }}
    QPushButton#errOk:hover {{ background: {p['save_hover']}; color: {p['save_hover_text']}; }}
    QPushButton#errOk:pressed {{ background: {p['btn_pressed']}; }}
    """
