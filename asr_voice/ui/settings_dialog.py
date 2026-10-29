"""设置对话框：UI 化编辑 config.yaml 常用配置。

设计要点：
1. 表驱动字段定义，新增配置项只需在 _FIELDS/_ADVANCED_FIELDS 加一行
   （七元组：段 / 键 / 标签 / 控件类型 / 参数 / 行内说明 / 悬停提示）
2. 保存时逐字段调用 update_config_field（行级替换，保留 yaml 注释与格式）
3. 保存后由 QFileSystemWatcher 触发现有热加载流程，无需手动刷新
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal, QEvent, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..hotkey.hold import hold_flag_enabled
from ..i18n import t
from ..steam_integration import external_services_allowed, is_steam_build
from .doc_viewer import doc_path
from .icons import app_icon, uac_shield_icon
from .presets import (
    ENGINE_CATEGORIES,
    engines_by_group,
    engine_group_of,
    provider_menu_items,
)
from .settings_styles import (
    SETTINGS_STYLES,
    _DEFAULT_STYLE,
    _settings_qss,
    _theme_palette,
)
from .settings_widgets import (
    AccordionCard,
    EngineOrderRow,
    FlatSlider,
    HotkeyCaptureEdit,
    SliderSpinBox,
    ThemeCardGroup,
    ToggleSwitch,
)

logger = logging.getLogger(__name__)

def _key_part(value: str) -> str:
    """combo 选项值归一化成键名一段：非字母数字转 `_`，空串用 "auto" 占位（免悬空键）。"""
    part = str(value or "").strip().lower()
    if not part:
        return "auto"
    return "".join(ch if ch.isalnum() else "_" for ch in part)


def _combo_labels_key(section: str, key: str) -> str:
    """combo 显示名映射本身的词条键（映射表整体缺失时的兜底）。"""
    return f"set.combo.{section}.{key}"


def _combo_option_key(section: str, key: str, value: str) -> str:
    """combo 单个选项显示名的词条键。"""
    return f"set.combo.{section}.{key}.{_key_part(value)}"


# combo 选项的友好显示名：界面展示 t() 后的文案，保存时映射回原始值写入 config.yaml。
# 导入期不能求值 t()（语言未定），故这里只存 i18n 键。
_COMBO_LABELS = {
    ("tencent", "filter_modal"): {
        "0": "set.combo.tencent.filter_modal.0",
        "1": "set.combo.tencent.filter_modal.1",
        "2": "set.combo.tencent.filter_modal.2",
    },
    ("tencent", "filter_punc"): {
        "0": "set.combo.tencent.filter_punc.0",
        "1": "set.combo.tencent.filter_punc.1",
        "2": "set.combo.tencent.filter_punc.2",
    },
    ("tencent", "convert_num_mode"): {
        "0": "set.combo.tencent.convert_num_mode.0",
        "1": "set.combo.tencent.convert_num_mode.1",
    },
    ("tencent", "filter_dirty"): {
        "0": "set.combo.tencent.filter_dirty.0",
        "1": "set.combo.tencent.filter_dirty.1",
        "2": "set.combo.tencent.filter_dirty.2",
    },
    # 分类调用下拉显示真实模型名（行标签已注明档位，候选不再用中文营销名）：
    # 腾讯模型 ID 无前缀，不设映射直接显示
    ("xfyun", "std_lang"): {
        "cn": "set.combo.xfyun.std_lang.cn",
        "en": "set.combo.xfyun.std_lang.en",
    },
    ("funasr", "device"): {
        "auto": "set.combo.funasr.device.auto",
        "cpu": "set.combo.funasr.device.cpu",
        "cuda": "set.combo.funasr.device.cuda",
    },
    # FunASR 分块档位：值为中文原文（历史持久化值，见 core/funasr 的取值判定）
    # 只翻显示名，不动值——否则老配置读不回来。
    ("funasr", "chunk_preset"): {
        "默认": "set.combo.funasr.chunk_preset.default",  # noqa: i18n
        "低延迟": "set.combo.funasr.chunk_preset.low_latency",  # noqa: i18n
        "高准确": "set.combo.funasr.chunk_preset.high_accuracy",  # noqa: i18n
    },
    # 火山：豆包 1.0 / 2.0 各自独立的计费方式（右键菜单切版本，自动套用对应版本的计费）
    ("volcengine", "bigasr_billing"): {
        "duration": "set.combo.volcengine.billing.duration",
        "concurrent": "set.combo.volcengine.billing.concurrent",
    },
    ("volcengine", "seedasr_billing"): {
        "duration": "set.combo.volcengine.billing.duration",
        "concurrent": "set.combo.volcengine.billing.concurrent",
    },
    # 语音对话（豆包 S2S）
    ("dialog", "model"): {
        "1.2.1.1": "set.combo.dialog.model.1_2_1_1",
        "2.2.0.0": "set.combo.dialog.model.2_2_0_0",
    },
    ("dialog", "speaker"): {
        "zh_female_vv_jupiter_bigtts": "set.combo.dialog.speaker.zh_female_vv_jupiter_bigtts",
        "zh_female_xiaohe_jupiter_bigtts": "set.combo.dialog.speaker.zh_female_xiaohe_jupiter_bigtts",
        "zh_male_yunzhou_jupiter_bigtts": "set.combo.dialog.speaker.zh_male_yunzhou_jupiter_bigtts",
        "zh_male_xiaotian_jupiter_bigtts": "set.combo.dialog.speaker.zh_male_xiaotian_jupiter_bigtts",
        "en_male_tim_uranus_bigtts": "set.combo.dialog.speaker.en_male_tim_uranus_bigtts",
        "en_female_dacey_uranus_bigtts": "set.combo.dialog.speaker.en_female_dacey_uranus_bigtts",
        "en_female_stokie_uranus_bigtts": "set.combo.dialog.speaker.en_female_stokie_uranus_bigtts",
    },
    ("dialog", "provider"): {
        "doubao": "set.combo.dialog.provider.doubao",
        "aliyun": "set.combo.dialog.provider.aliyun",
        "hermes": "set.combo.dialog.provider.hermes",
    },
    # 对话专属识别引擎：第一个候选是空串，不映射会显成一行空白
    ("dialog.asr", "engine"): {
        "": "set.combo.dialog_asr.engine.auto",
        "tencent": "set.combo.dialog_asr.engine.tencent",
        "aliyun": "set.combo.dialog_asr.engine.aliyun",
    },
    ("dialog.qwen", "region"): {
        "legacy": "set.combo.dialog_qwen.region.legacy",
        "beijing": "set.combo.dialog_qwen.region.beijing",
        "singapore": "set.combo.dialog_qwen.region.singapore",
    },
    ("dialog.qwen", "model"): {
        "qwen-audio-3.0-realtime-plus": "set.combo.dialog_qwen.model.plus",
        "qwen-audio-3.0-realtime-flash": "set.combo.dialog_qwen.model.flash",
    },
    ("dialog.qwen", "turn_detection"): {
        "server_vad": "set.combo.dialog_qwen.turn_detection.server_vad",
        "smart_turn": "set.combo.dialog_qwen.turn_detection.smart_turn",
    },
}

# O2.0 官方音色（文档 1.1 音色表；SC2.0 克隆音色 saturn_*/S_* 直接在
# speaker 下拉手填——combo 现值不在候选时自动 editable）
_SPEAKER_OPTIONS = [
    "zh_female_vv_jupiter_bigtts",
    "zh_female_xiaohe_jupiter_bigtts",
    "zh_male_yunzhou_jupiter_bigtts",
    "zh_male_xiaotian_jupiter_bigtts",
    "en_male_tim_uranus_bigtts",
    "en_female_dacey_uranus_bigtts",
    "en_female_stokie_uranus_bigtts",
]

# 阿里云 Qwen-Audio Realtime 官方龙系音色（下拉直显 ID，不臆造中文名）
_QWEN_VOICE_OPTIONS = [
    "longanqian", "longanlingxin", "longanlingxi", "longanxiaoxin", "longanlufeng",
]

# 值为整数的 combo 字段：写入 yaml 时用裸数字（int），而非带引号的字符串
_COMBO_INT_FIELDS = {("tencent", "filter_modal"), ("tencent", "filter_punc"),
                     ("tencent", "convert_num_mode"), ("tencent", "filter_dirty")}

# 值为浮点的字段：写入 yaml 时用裸小数（float），而非带引号的字符串
_FLOAT_FIELDS = {("dialog.qwen", "vad_threshold")}


def _sec(title_key: str) -> tuple:
    """分组内小节标题：复用字段七元组、wtype="header" 渲染为次级标题行。

    `title_key` 是 i18n 键而非文案，文案统一在渲染点 t(field[2]) 取。
    """
    return ("", None, title_key, "header", None, "", "")


def _tier_models(group: str, slot: str) -> list[str]:
    """某云引擎右键菜单某分类档的候选模型列表（取 presets 分类定义的模型 id）。"""
    for s, _, models in ENGINE_CATEGORIES.get(group, []):
        if s == slot:
            return [m for m, _ in models]
    return []


# ---- 图标（常规图标＝自绘线稿：细线 1.5px + 圆头端点；steam 例外，用官方标志位图） ----

def _steam_symbol_pixmap(color: str) -> QPixmap | None:
    """内置 Steam 标志（已按最终尺寸预渲染）→ 染成目标色；资源缺失返回 None。

    运行期只染色、不缩放（先渲大再缩会糊掉圆环细节）；图形形状不得改动。
    """
    path = Path(__file__).resolve().parent.parent / "assets" / "steam_symbol.png"
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    # SourceIn：只保留原图透明度、整体覆盖成目标色（单色化）
    q = QPainter(pm)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(pm.rect(), QColor(color))
    q.end()
    return pm


def _nav_pixmap(kind: str, color: str) -> QPixmap:
    """画一个 18×18 线稿图标（导航用）。

    kind：grid=四方块 / key=钥匙 / sliders=滑杆 / sparkle=四角星 /
    mic=麦克风 / steam=官方标志位图 / info=圆点 i。
    """
    pm = QPixmap(18, 18)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "grid":
        for x, y in ((2, 2), (10, 2), (2, 10), (10, 10)):
            p.drawRoundedRect(QRectF(x, y, 6, 6), 1.5, 1.5)
    elif kind == "key":
        p.drawEllipse(QRectF(1.5, 1.5, 6.5, 6.5))
        p.drawLine(7, 7, 15, 15)
        p.drawLine(11.5, 11.5, 14, 9)
        p.drawLine(13.5, 13.5, 16, 11)
    elif kind == "sliders":
        for y, kx in ((4, 12.5), (9, 5.5), (14, 9.5)):
            p.drawLine(1, int(y), 17, int(y))
            p.drawEllipse(QRectF(kx - 2.4, y - 2.4, 4.8, 4.8))
    elif kind == "sparkle":
        path = QPainterPath()
        pts = [(9, 0.8), (11.4, 6.6), (17.2, 9), (11.4, 11.4), (9, 17.2),
               (6.6, 11.4), (0.8, 9), (6.6, 6.6)]
        path.moveTo(*pts[0])
        for pt in pts[1:]:
            path.lineTo(*pt)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "mic":
        p.drawRoundedRect(QRectF(6.5, 1.5, 5, 9.5), 2.5, 2.5)
        p.drawArc(QRectF(4.5, 8, 9, 9), 180 * 16, 180 * 16)
        p.drawLine(9, 17, 9, 15.5)
    elif kind == "chat":
        # 语音对话：两个错落的对话气泡（沿用细线 1.5px + 圆头端点风格）
        p.drawRoundedRect(QRectF(1.5, 2.5, 11, 7.5), 3, 3)
        p.drawLine(4.5, 10, 3.5, 13)
        p.drawLine(3.5, 13, 6.5, 10)
        p.drawRoundedRect(QRectF(8, 8.5, 8.5, 7), 3, 3)
        p.drawLine(14.5, 15.5, 15.5, 17.5)
        p.drawLine(15.5, 17.5, 12, 15.5)
    elif kind == "steam":
        # Steam 页：官方标志（不再手绘，见 _steam_symbol_pixmap 的来源说明）。
        symbol = _steam_symbol_pixmap(color)
        if symbol is not None:
            off = (18 - symbol.width()) // 2
            p.drawPixmap(off, off, symbol)
        else:
            # 兜底：资源缺失（被裁剪的构建 / 文件被删）时保底一个可辨识的图形
            p.drawEllipse(QRectF(3.5, 3.5, 11, 11))
            p.drawLine(6.5, 11.5, 11.5, 6.5)
    else:  # info
        p.drawEllipse(QRectF(1.5, 1.5, 15, 15))
        p.drawEllipse(QRectF(8.2, 4.8, 1.6, 1.6))
        p.drawLine(9, 8.6, 9, 12.6)
    p.end()
    return pm


def _nav_icon(kind: str) -> QIcon:
    """导航项图标：常态灰、选中亮（QListWidget 选中时用 Selected 模式）。"""
    ic = QIcon()
    ic.addPixmap(_nav_pixmap(kind, "#9a9a9e"), QIcon.Mode.Normal)
    ic.addPixmap(_nav_pixmap(kind, "#f0f0f0"), QIcon.Mode.Selected)
    return ic


def _text_menu_dim_color(hex_color: str) -> str:
    """#RRGGBB -> rgba(...) 透明版：禁用菜单项的弱化文字色（明暗主题通用）。"""
    v = int(hex_color.lstrip("#"), 16)
    return "rgba({},{},{},{})".format((v >> 16) & 255, (v >> 8) & 255, v & 255, 0.35)


def _build_text_menu(edit, pal: dict, pos=None):
    """构建文本框右键菜单（与 exec 拆开：测试可直接检查而不阻塞）。

    启用态按当前文本状态算、对齐 Qt 默认菜单；只读框只留复制/全选。
    禁用项必须有弱化色，否则与可用项同色分不出来。
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QMenu, QTextBrowser

    is_line = isinstance(edit, QLineEdit)
    is_browser = isinstance(edit, QTextBrowser)
    read_only = edit.isReadOnly()
    sel = (edit.hasSelectedText() if is_line
           else edit.textCursor().hasSelection())
    secret = (edit.echoMode() == QLineEdit.EchoMode.Password
              if is_line else False)
    if is_line:
        full = edit.text()
        fully = sel and edit.selectedText() == full
    else:
        cur = edit.textCursor()
        full = edit.toPlainText()
        fully = cur.selectionStart() == 0 and cur.selectionEnd() == len(full)
    clip = QGuiApplication.clipboard().text()
    paste_ok = (not read_only and bool(clip)) if is_line else edit.canPaste()
    if is_line:
        undo_ok, redo_ok = edit.isUndoAvailable(), edit.isRedoAvailable()
    else:
        # QTextEdit.undoAvailable 不带括号是信号不是属性值，走 document 读
        undo_ok = edit.document().isUndoAvailable()
        redo_ok = edit.document().isRedoAvailable()
    menu = QMenu(edit)
    menu.setStyleSheet(
        "QMenu{background:%s;color:%s;border:1px solid %s;padding:4px;}"
        "QMenu::item{padding:5px 18px;border-radius:4px;}"
        "QMenu::item:selected{background:%s;}"
        "QMenu::item:disabled{color:%s;}"
        % (pal["panel"], pal["text"], pal["border"], pal["combo_sel"],
           _text_menu_dim_color(pal["text"])))

    def act(key, slot, enabled):
        a = menu.addAction(t(key))
        a.triggered.connect(slot)
        a.setEnabled(enabled)

    if read_only or is_browser:
        # 只读框（热键捕获、文档浏览）默认菜单只有复制/全选，保持同形态；
        # 浏览框光标在链接上时 Qt 还带「复制链接地址」，文档外链要保住
        act("set.textmenu.copy", edit.copy, sel and not secret)
        if is_browser and pos is not None:
            href = (edit.cursorForPosition(pos)
                    .charFormat().anchorHref() or "")
            if href:
                act("set.textmenu.copy_link",
                    lambda: QGuiApplication.clipboard().setText(href), True)
        act("set.textmenu.select_all", edit.selectAll, bool(full) and not fully)
        return menu

    act("set.textmenu.undo", edit.undo, undo_ok)
    act("set.textmenu.redo", edit.redo, redo_ok)
    menu.addSeparator()
    act("set.textmenu.cut", edit.cut, sel and not read_only and not secret)
    act("set.textmenu.copy", edit.copy, sel and not secret)
    act("set.textmenu.paste", edit.paste, paste_ok)
    act("set.textmenu.delete",
        edit.del_ if is_line
        else lambda: edit.textCursor().removeSelectedText(),
        sel and not read_only)
    menu.addSeparator()
    act("set.textmenu.select_all", edit.selectAll, bool(full) and not fully)
    return menu


def _install_text_menu(edit, pal: dict) -> None:
    """文本框右键换应用主题菜单：windows11 默认菜单带系统图标，与界面不符。"""
    edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _on_menu(pos) -> None:
        menu = _build_text_menu(edit, pal, pos)
        menu.exec(edit.mapToGlobal(pos))
        menu.deleteLater()

    edit.customContextMenuRequested.connect(_on_menu)


# 页面名 i18n 键 -> 图标 kind（导航顺序即页面顺序）。
# 键化而非直接存页名：本表在导入期求值，此刻语言未定（见 i18n 模块的
# 「导入期求值禁令」），存中文会把页名永久冻结成导入时那一门语言。
_NAV_KINDS = {
    "set.page.general": "grid",
    "set.page.keys": "key",
    "set.page.params": "sliders",
    "set.page.ai_fix": "sparkle",
    "set.page.recording": "mic",
    "set.page.voice_mode": "chat",
    "set.page.steam": "steam",
    "set.page.about": "info",
}

# 关于页「开源组件」卡：(库名, 许可证)。
# 许可号是法律标识（SPDX 标识 / 官方名称），不翻译、不抽串；含描述性汉字的条目
# 第二项直接存 i18n 键，渲染时 t()（无词条时回退键名，等价于原样显示）。
_ABOUT_OSS = [
    ("CPython", "PSF"),
    ("PySide6 / Shiboken6", "LGPL-3.0"),
    ("sounddevice", "MIT"),
    ("PortAudio", "set.about.lic_mit_like"),
    ("CFFI / pycparser", "MIT / BSD"),
    ("numpy", "BSD-3-Clause"),
    ("keyboard", "MIT"),
    ("pyperclip", "BSD"),
    ("websockets", "BSD-3-Clause"),
    ("PyYAML", "MIT"),
    ("pywinrt", "MIT"),
    ("pycaw", "MIT"),
    ("comtypes", "MIT"),
    ("FunASR", "set.about.lic_funasr"),
    ("ModelScope", "Apache-2.0"),
    ("PyTorch / torchaudio", "BSD"),
]

# 关于页「联系方式」卡：B 站空间动态（文档阅读 / 版本更新发布渠道）
_ABOUT_CONTACT_URL = "https://space.bilibili.com/3492318/dynamic"

# 关于页「使用帮助」卡：仓库里的用户文档，按界面语言选中文/英文版
_ABOUT_DOC_URLS = {
    "zh": "https://github.com/LangeHeris/Open-RealtimeASR-UI/blob/main/docs/User_Doc_zh.md",
    "en": "https://github.com/LangeHeris/Open-RealtimeASR-UI/blob/main/docs/User_Doc_en.md",
}
_ABOUT_DOC_URL = _ABOUT_DOC_URLS["zh"]


# ---- 创意工坊：物品行的文本与提示 ----
# 标题从本地清单（ori_workshop.json）读，等价于发布流程上传的标题——不必走
# Steam 的异步 UGC 查询（flat 层拿不到 CallResult 绑定）。
# 未安装 / 无清单的物品退回显示 `#ID`，完整 ID 与本地目录在悬停提示里。
def _workshop_installed_manifest(item):
    """已安装的 SDK 物品 → 解析本地清单；未安装 / 无清单返回 None。"""
    if not item.install_dir:
        return None
    from pathlib import Path

    from ..workshop import parse_manifest

    return parse_manifest(Path(item.install_dir), item.item_id)


def _workshop_kind_bits(manifest) -> tuple:
    """清单 → （方块字形, 副标题文案）。SDK 视图与文件系统视图共用防漂移。"""
    kinds = list(manifest.kinds)
    if manifest.capabilities and not kinds:
        kinds = ["addon"]
    glyph = (_WORKSHOP_KIND_GLYPH.get(kinds[0], "package")
             if len(kinds) == 1 else "package")
    subtitle = "、".join(t(f"set.workshop.kind.{k}") for k in manifest.kinds)
    if manifest.capabilities:
        extra = t("set.workshop.kind.addon")
        subtitle = f"{subtitle}、{extra}" if subtitle else extra
    return glyph, subtitle


def _workshop_item_text(item, name: str = "") -> str:
    """一行状态文本：安装/下载/待更新 三态（有包名时以包名替代 ID 作主语）。"""
    label = name or t("set.workshop.item_id", item_id=item.item_id)
    if item.downloading or item.download_pending:
        return f"{label} — " + t("set.workshop.installing",
                                percent=item.download_percent)
    if item.needs_update:
        return f"{label} — " + t("set.workshop.needs_update")
    if item.installed:
        return f"{label} — " + t("set.workshop.installed")
    return f"{label} — " + t("set.workshop.subscribed")


def _workshop_item_tooltip(item) -> str:
    """悬停详情：物品 ID 与本地目录（排查"装了却不生效"时最有用的一行）。"""
    lines = [t("set.workshop.item_id", item_id=item.item_id)]
    if item.install_dir:
        lines.append(item.install_dir)
    return "\n".join(lines)


def _workshop_pack_text(manifest) -> str:
    """只读视图的一行：**包名**（ori_workshop.json 的 name）+ 内容类型。"""
    parts = [t(f"set.workshop.kind.{k}") for k in manifest.kinds]
    # capabilities 不是内容类型（kinds 里不会有），功能包单独补一个标签——
    # 否则"云端与 AI 功能包"这种无内容条目的物品在列表里只剩一个光秃秃的名字
    if manifest.capabilities:
        parts.append(t("set.workshop.kind.addon"))
    kinds = "、".join(parts)
    return f"{manifest.name} — {kinds}" if kinds else manifest.name


def _workshop_pack_tooltip(manifest) -> str:
    """只读视图的悬停详情：包目录 + 版本/作者（有就显示）。"""
    lines = [str(manifest.item_dir)]
    meta = " · ".join(x for x in (manifest.version, manifest.author) if x)
    if meta:
        lines.append(meta)
    return "\n".join(lines)


def _workshop_selected_id(lst) -> int:
    """取列表当前选中行的物品 ID（无选中返回 0）。"""
    row = lst.currentRow()
    if row < 0:
        return 0
    entry = lst.item(row)
    if entry is None:
        return 0
    try:
        return int(entry.data(Qt.ItemDataRole.UserRole) or 0)
    except (TypeError, ValueError):
        return 0


def _workshop_community_url() -> str:
    """本应用的社区创意工坊页。

    用 AppID 而不是硬编码：AppID 只能由平台客户端注入（项目约定「真 ID
    不入库」），拿不到时退回 Steam 首页而不是拼一个错的链接。
    """
    try:
        from ..steam_integration import get_integration

        integ = get_integration()
        app_id = integ.app_id()
    except Exception:
        app_id = 0
    if app_id:
        return f"https://steamcommunity.com/app/{int(app_id)}/workshop/"
    return "https://steamcommunity.com/workshop/"


# ---- 创意工坊：物品行的渲染 ----
# 行控件对鼠标透明（WA_TransparentForMouseEvents）——点击/悬停/选中/双击仍归
# QListWidget 处理，否则交互被这层吃掉。
# 所有 objectName 一律带 workshop 前缀，避免污染其它页面的样式。

# 内容类型（ori_workshop.json 的 kinds 取值）-> 图标文件名（assets/workshop/ 下
# Lucide，ISC 授权，见 THIRD_PARTY_NOTICES.md「界面图标」一节）。多类型 / 未知
# -> package。换图标只改这里 + 换 assets/workshop/ 里的 SVG 再跑
# tools/render_workshop_icons.py，手绘回退映射（_WORKSHOP_GLYPH_DRAWN）跟着加一项。
_WORKSHOP_KIND_GLYPH = {
    "models": "cpu",
    "themes": "palette",
    "fonts": "type",
    "phrases": "list",
    "prompts": "sparkles",
    "addon": "puzzle",
}

# 图标名 -> 手绘图形的分支名。只在 assets/workshop/ 资源缺失时生效（精简
# 安装 / 手动删文件 / 打包漏投），保证界面不破；资源正常时永远走上面的 PNG。
_WORKSHOP_GLYPH_DRAWN = {
    "cpu": "chip",
    "palette": "drop",
    "type": "letter",
    "list": "lines",
    "sparkles": "sparkle",
    "puzzle": "plus",
    "package": "package",
}

# 图标资源目录（与 _steam_symbol_pixmap 同一套 Path 定位：frozen 下也能解析）
_WORKSHOP_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "workshop"
# (图标名, 颜色, 尺寸, dpr) -> 已染色的 QPixmap；设置页反复重绘列表时省掉重复加载
_WORKSHOP_ICON_CACHE: dict = {}

# 列表最多显示几行（再多交给滚动条），以及取不到行高时的回落值
_WORKSHOP_LIST_MAX_ROWS = 5
_WORKSHOP_ROW_FALLBACK_H = 52


def _screen_dpr() -> float:
    """主屏 devicePixelRatio；拿不到屏幕（offscreen / 极端早期启动）按 1.0。"""
    try:
        screen = QGuiApplication.primaryScreen()
    except Exception:
        return 1.0
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _workshop_icon_pixmap(glyph: str, color: str, size: int,
                          dpr: float) -> QPixmap | None:
    """assets/workshop/ 的 Lucide 图标 → 染色后的 QPixmap；资源缺失返回 None。

    单色位图 + SourceIn 整体染色，只保留原图透明度；@2x 自带 DPR=2，高 DPI 直接取用不放大。
    """
    key = (glyph, color, size, dpr)
    cached = _WORKSHOP_ICON_CACHE.get(key, False)
    if cached is not False:
        return None if cached is None else cached
    pm = None
    # 高 DPI 优先 @2x，缺失退回 1x（渲染尺寸由 QLabel 侧按 DPR 缩放，只是略软）
    for name in (f"{glyph}@2x.png" if dpr > 1.0 else f"{glyph}.png",
                 f"{glyph}.png" if dpr > 1.0 else f"{glyph}@2x.png"):
        path = _WORKSHOP_ICON_DIR / name
        if path.is_file():
            pm = QPixmap(str(path))
            if not pm.isNull():
                break
            pm = None
    if pm is None:
        _WORKSHOP_ICON_CACHE[key] = None
        return None
    q = QPainter(pm)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(pm.rect(), QColor(color))
    q.end()
    _WORKSHOP_ICON_CACHE[key] = pm
    return pm


def _workshop_glyph_pixmap(glyph: str, color: str, size: int = 20) -> QPixmap:
    """size×size 的内容类型图标：优先 Lucide 位图，缺失时回退手绘线稿。

    字形只按**内容类别**区分，不画厂商商标；坐标按 size×size 逻辑空间书写，高 DPI 整体 scale。
    """
    dpr = _screen_dpr()
    icon = _workshop_icon_pixmap(glyph, color, size, dpr)
    if icon is not None:
        return icon
    drawn = _WORKSHOP_GLYPH_DRAWN.get(glyph, "package")
    # 手绘回退也走 QImage：带 DPR 的 QPixmap 上直接画圆角矩形会触发 Qt 的
    # 右下角填充 bug（见 _workshop_tile_pixmap 的说明）
    dev = max(1, int(round(size * dpr)))
    img = QImage(dev, dev, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if dpr != 1.0:
        p.scale(dpr, dpr)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if drawn == "chip":                      # 模型：芯片
        p.drawRoundedRect(QRectF(5.5, 5.5, 9.0, 9.0), 2.0, 2.0)
        for x in (8.0, 12.0):
            p.drawLine(x, 3.2, x, 5.5)
            p.drawLine(x, 14.5, x, 16.8)
    elif drawn == "drop":                    # 主题：一滴
        path = QPainterPath()
        path.moveTo(10.0, 3.0)
        path.cubicTo(15.0, 9.0, 15.0, 12.0, 13.2, 14.2)
        path.cubicTo(11.0, 17.0, 9.0, 17.0, 6.8, 14.2)
        path.cubicTo(5.0, 12.0, 5.0, 9.0, 10.0, 3.0)
        p.drawPath(path)
    elif drawn == "letter":                  # 字体：A
        p.drawLine(5.0, 16.0, 10.0, 4.0)
        p.drawLine(10.0, 4.0, 15.0, 16.0)
        p.drawLine(7.2, 11.6, 12.8, 11.6)
    elif drawn == "lines":                   # 短语：三行词条
        p.drawLine(4.0, 6.0, 16.0, 6.0)
        p.drawLine(4.0, 10.0, 13.0, 10.0)
        p.drawLine(4.0, 14.0, 16.0, 14.0)
    elif drawn == "sparkle":                 # 提示词：四角星
        path = QPainterPath()
        pts = [(10.0, 2.6), (11.9, 8.1), (17.4, 10.0), (11.9, 11.9),
               (10.0, 17.4), (8.1, 11.9), (2.6, 10.0), (8.1, 8.1)]
        path.moveTo(*pts[0])
        for px, py in pts[1:]:
            path.lineTo(px, py)
        path.closeSubpath()
        p.drawPath(path)
    elif drawn == "plus":                    # 功能扩展：圈加号
        p.drawEllipse(QRectF(3.2, 3.2, 13.6, 13.6))
        p.drawLine(10.0, 6.8, 10.0, 13.2)
        p.drawLine(6.8, 10.0, 13.2, 10.0)
    else:                                    # package：包裹盒（物品本身的默认形）
        p.drawRoundedRect(QRectF(3.0, 4.5, 14.0, 12.0), 2.0, 2.0)
        p.drawLine(3.0, 8.5, 17.0, 8.5)
        p.drawLine(8.0, 8.5, 8.0, 11.0)
        p.drawLine(8.0, 11.0, 12.0, 11.0)
        p.drawLine(12.0, 11.0, 12.0, 8.5)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def _workshop_tile_pixmap(glyph: str, bg: str, fg: str, size: int = 30) -> QPixmap:
    """类型方块：圆角底 + 居中字形（按主屏 DPR 出图，高 DPI 不放大成马赛克）。

    **必须画在 QImage 上再 `fromImage` 转 QPixmap**：Qt 对自带 DPR 的 QPixmap 直接
    开 QPainter 画圆角矩形有 bug（圆角失效、右下角被填死）；QImage 无 DPR 语义。
    """
    dpr = _screen_dpr()
    dev = max(1, int(round(size * dpr)))
    img = QImage(dev, dev, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(0.5, 0.5, dev - 1.0, dev - 1.0), 8.0 * dpr, 8.0 * dpr)
    p.end()
    g = _workshop_glyph_pixmap(glyph, fg)
    # 图标可能自带 DPR（@2x 资源是 2.0）：先折成逻辑边长再居中，否则高 DPI 下
    # 会把 40 设备像素当成 40 逻辑像素，图标跑出方块外
    g_dpr = g.devicePixelRatio() or 1.0
    off = (size - g.width() / g_dpr) / 2.0
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.drawPixmap(off * dpr, off * dpr, g)
    q.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def _workshop_item_state(item) -> tuple:
    """SDK 物品 -> (状态文案, 徽标 objectName, 下载百分比或 None)。

    文案直接复用既有的 `set.workshop.*` 词条（不新增词条）：这些状态原来挤在
    同一行文本的尾巴上，现在拆成"标题 + 徽标"两层信息。
    """
    if item.downloading or item.download_pending:
        pct = int(item.download_percent or 0)
        return (t("set.workshop.installing", percent=pct),
                "workshopChipBusy", pct)
    if item.needs_update:
        return (t("set.workshop.needs_update"), "workshopChipTodo", None)
    if item.installed:
        return (t("set.workshop.installed"), "workshopChipDone", None)
    return (t("set.workshop.subscribed"), "workshopChipIdle", None)


class _WorkshopRow(QWidget):
    """工坊列表的一行：方块 + 标题/副标题 +（可选）进度条 + 状态徽标。"""

    def __init__(self, palette: dict, *, glyph: str, title: str, subtitle: str = "",
                 chip: str = "", chip_style: str = "workshopChipIdle",
                 progress: Optional[int] = None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 6, 10, 6)
        row.setSpacing(10)

        tile = QLabel()
        # 方块底色刻意取 `card_border` 而不是 `input_bg`：列表自己的底色就是
        # input_bg，同色等于方块整个隐形（目检发现）
        tile.setPixmap(_workshop_tile_pixmap(
            glyph, palette["card_border"], palette["accent"]))
        tile.setFixedSize(30, 30)
        row.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = QLabel(title)
        name.setObjectName("workshopRowTitle")
        col.addWidget(name)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("workshopRowSub")
            col.addWidget(sub)
        if progress is not None:
            bar = QProgressBar()
            bar.setObjectName("workshopProgress")
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, progress)))
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            col.addWidget(bar)
        row.addLayout(col, 1)

        if chip:
            badge = QLabel(chip)
            badge.setObjectName(chip_style)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)



# 字段定义：(section, key, label, widget类型, 附加参数, 行内说明, 悬停提示)
# widget类型：str=文本框 / int=数字框(min,max) / slider=滑块+数字框(min,max,step[,suffix]) /
# bool=拨动开关 / combo=下拉(options) / secret=密码框 / hotkey=按键捕获 /
# device_combo=设备下拉 / billing=计费下拉 / header=小节标题
def _gpu_switch_available() -> bool:
    """显卡加速开关的显示条件：DLC 已装（能力层）或 env_gpu 目录存在（开发机）。

    开发机没有 平台客户端，`dlc_installed` 恒 False；只看它的话这个开关
    在源码模式下永远不出现，等于没法测。故补一条目录探测。
    """
    from ..steam_integration import STEAM_DLC_GPU_APP_ID, get_integration

    if get_integration().dlc_installed(STEAM_DLC_GPU_APP_ID):
        return True
    try:
        from pathlib import Path

        return (Path(sys.executable).resolve().parent.parent / "env_gpu").is_dir()
    except Exception:
        return False


_FIELDS = [
    # ---- 常规：腾讯云 ----
    ("tencent", "secret_id", "set.tencent.secret_id", "secret", None,
     "set.tencent.secret_id.desc",
     "set.tencent.secret_id.tip"),
    ("tencent", "secret_key", "set.tencent.secret_key", "secret", None,
     "set.tencent.secret_key.desc",
     "set.tencent.secret_key.tip"),
    ("tencent", "app_id", "set.tencent.app_id", "str", None,
     "set.tencent.app_id.desc",
     "set.tencent.app_id.tip"),
    # ---- 常规：阿里云 ----
    ("aliyun", "api_key", "set.aliyun.api_key", "secret", None,
     "set.aliyun.api_key.desc",
     "set.aliyun.api_key.tip"),
    # ---- 常规：讯飞 ----
    ("xfyun", "app_id", "set.xfyun.app_id", "str", None,
     "set.xfyun.app_id.desc",
     "set.xfyun.app_id.tip"),
    ("xfyun", "api_key", "set.xfyun.api_key", "secret", None,
     "set.xfyun.api_key.desc",
     "set.xfyun.api_key.tip"),
    ("xfyun", "api_secret", "set.xfyun.api_secret", "secret", None,
     "set.xfyun.api_secret.desc",
     "set.xfyun.api_secret.tip"),
    ("xfyun", "std_app_id", "set.xfyun.std_app_id", "str", None,
     "set.xfyun.std_app_id.desc",
     "set.xfyun.std_app_id.tip"),
    ("xfyun", "std_api_key", "set.xfyun.std_api_key", "secret", None,
     "set.xfyun.std_api_key.desc",
     "set.xfyun.std_api_key.tip"),
    # ---- 常规：火山引擎 ----
    ("volcengine", "api_key", "set.volcengine.api_key", "secret", None,
     "set.volcengine.api_key.desc",
     "set.volcengine.api_key.tip"),
    ("volcengine", "app_id", "set.volcengine.app_id", "str", None,
     "set.volcengine.app_id.desc",
     "set.volcengine.app_id.tip"),
    ("volcengine", "access_token", "set.volcengine.access_token", "secret", None,
     "set.volcengine.access_token.desc",
     "set.volcengine.access_token.tip"),
    # ---- 常规：热键 / 界面 ----
    ("hotkey", "toggle", "set.hotkey.toggle", "hotkey", None,
     "set.hotkey.toggle.desc",
     "set.hotkey.toggle.tip"),
    ("hotkey", "hold_to_talk", "set.hotkey.hold_to_talk", "bool", None,
     "set.hotkey.hold_to_talk.desc",
     "set.hotkey.hold_to_talk.tip"),
    ("hotkey", "hold_threshold_ms", "set.hotkey.hold_threshold_ms", "slider", (100, 3000, 50, "set.unit.ms"),
     "set.hotkey.hold_threshold_ms.desc",
     "set.hotkey.hold_threshold_ms.tip"),
    ("ui", "auto_hide_seconds", "set.ui.auto_hide_seconds", "slider", (0, 3600, 30, "set.unit.sec"),
     "set.ui.auto_hide_seconds.desc", None),
    ("ui", "bar_opacity", "set.ui.bar_opacity", "slider", (0, 100, 5, "%"),
     "set.ui.bar_opacity.desc", None),
    ("ui", "settings_style", "set.ui.settings_style", "theme_cards", None,
     "set.ui.settings_style.desc",
     "set.ui.settings_style.tip"),
    # 界面语言：值为 ""（跟随系统）/ zh / en，语言无关故持久化安全。
    # combo 的 extra 用 dict 形态（值→i18n 键），避免字段表里写文案。
    ("ui", "language", "set.ui.language", "combo",
     {"": "set.combo.ui.language.auto",
      "zh": "set.combo.ui.language.zh",
      "en": "set.combo.ui.language.en"},
     "set.ui.language.desc", "set.ui.language.tip"),
    # ---- 语音对话（豆包 S2S）：接入 ----
    ("dialog", "enable", "set.dialog.enable", "bool", None,
     "set.dialog.enable.desc",
     "set.dialog.enable.tip"),
    ("dialog", "provider", "set.dialog.provider", "combo",
     ["doubao", "aliyun", "hermes"],
     "set.dialog.provider.desc",
     "set.dialog.provider.tip"),
    ("hotkey", "dialog", "set.hotkey.dialog", "hotkey", None,
     "set.hotkey.dialog.desc",
     "set.hotkey.dialog.tip"),
    ("dialog", "api_key", "set.dialog.api_key", "secret", None,
     "set.dialog.api_key.desc",
     "set.dialog.api_key.tip"),
    ("dialog", "app_id", "set.dialog.app_id", "str", None,
     "set.dialog.app_id.desc",
     "set.dialog.app_id.tip"),
    ("dialog", "access_token", "set.dialog.access_token", "secret", None,
     "set.dialog.access_token.desc",
     "set.dialog.access_token.tip"),
    # ---- 语音对话：模型与人设 ----
    ("dialog", "model", "set.dialog.model", "combo", ["1.2.1.1", "2.2.0.0"],
     "set.dialog.model.desc",
     "set.dialog.model.tip"),
    ("dialog", "bot_name", "set.dialog.bot_name", "str", None,
     "set.dialog.bot_name.desc",
     None),
    ("dialog", "system_role", "set.dialog.system_role", "text", None,
     "set.dialog.system_role.desc",
     "set.dialog.system_role.tip"),
    ("dialog", "speaking_style", "set.dialog.speaking_style", "str", None,
     "set.dialog.speaking_style.desc",
     "set.dialog.speaking_style.tip"),
    ("dialog", "character_manifest", "set.dialog.character_manifest", "text", None,
     "set.dialog.character_manifest.desc",
     "set.dialog.character_manifest.tip"),
    # ---- 语音对话：音色与播报 ----
    ("dialog", "speaker", "set.dialog.speaker", "combo", _SPEAKER_OPTIONS,
     "set.dialog.speaker.desc",
     "set.dialog.speaker.tip"),
    ("dialog", "speech_rate", "set.dialog.speech_rate", "slider", (-50, 100, 5, ""),
     "set.dialog.speech_rate.desc",
     None),
    ("dialog", "loudness_rate", "set.dialog.loudness_rate", "slider", (-50, 100, 5, ""),
     "set.dialog.loudness_rate.desc",
     None),
    ("dialog", "output_device", "set.dialog.output_device", "output_combo", None,
     "set.dialog.output_device.desc",
     "set.dialog.output_device.tip"),
    # ---- 语音对话：收音与判停 ----
    ("dialog", "end_smooth_window_ms", "set.dialog.end_smooth_window_ms", "slider", (500, 5000, 50, "set.unit.ms"),
     "set.dialog.end_smooth_window_ms.desc",
     "set.dialog.end_smooth_window_ms.tip"),
    ("dialog", "half_duplex", "set.dialog.half_duplex", "bool", None,
     "set.dialog.half_duplex.desc",
     "set.dialog.half_duplex.tip"),
    # ---- 语音模式：Hermes Agent ----
    ("dialog.hermes", "base_url", "set.dialog_hermes.base_url", "str", None,
     "set.dialog_hermes.base_url.desc",
     "set.dialog_hermes.base_url.tip"),
    ("dialog.hermes", "api_key", "set.dialog_hermes.api_key", "secret", None,
     "set.dialog_hermes.api_key.desc", ""),
    ("dialog.hermes", "model", "set.dialog_hermes.model", "str", None,
     "set.dialog_hermes.model.desc",
     "set.dialog_hermes.model.tip"),
    ("dialog.hermes", "session_title", "set.dialog_hermes.session_title", "str", None,
     "set.dialog_hermes.session_title.desc",
     "set.dialog_hermes.session_title.tip"),
    ("dialog.hermes", "system_hint", "set.dialog_hermes.system_hint", "text", None,
     "set.dialog_hermes.system_hint.desc",
     "set.dialog_hermes.system_hint.tip"),
    ("dialog.hermes", "connect_timeout_ms", "set.dialog_hermes.connect_timeout_ms", "slider",
     (1000, 30000, 500, "set.unit.ms"), "set.dialog_hermes.connect_timeout_ms.desc", ""),
    ("dialog.hermes", "turn_timeout_ms", "set.dialog_hermes.turn_timeout_ms", "slider",
     (10000, 300000, 5000, "set.unit.ms"), "set.dialog_hermes.turn_timeout_ms.desc",
     "set.dialog_hermes.turn_timeout_ms.tip"),
    # ---- 语音模式：对话专属识别 ----
    ("dialog.asr", "engine", "set.dialog_asr.engine", "combo",
     ["", "tencent", "aliyun"], "set.dialog_asr.engine.desc",
     "set.dialog_asr.engine.tip"),
    ("dialog.asr", "model", "set.dialog_asr.model", "str", None,
     "set.dialog_asr.model.desc", ""),
    ("dialog.asr", "lang", "set.dialog_asr.lang", "str", None,
     "set.dialog_asr.lang.desc", ""),
    ("dialog.asr", "hotword", "set.dialog_asr.hotword", "str", None,
     "set.dialog_asr.hotword.desc", ""),
    # ---- 语音对话：会话行为 ----
    ("dialog", "enable_user_query_exit", "set.dialog.enable_user_query_exit", "bool", None,
     "set.dialog.enable_user_query_exit.desc",
     None),
    ("dialog", "keep_context", "set.dialog.keep_context", "bool", None,
     "set.dialog.keep_context.desc",
     None),
    ("dialog", "auto_reconnect", "set.dialog.auto_reconnect", "bool", None,
     "set.dialog.auto_reconnect.desc",
     None),
    ("dialog", "strict_audit", "set.dialog.strict_audit", "bool", None,
     "set.dialog.strict_audit.desc",
     None),
    # ---- 语音对话（阿里云 Qwen-Audio Realtime）：provider=aliyun 时显 ----
    ("dialog.qwen", "region", "set.dialog_qwen.region", "combo", ["legacy", "beijing", "singapore"],
     "set.dialog_qwen.region.desc",
     "set.dialog_qwen.region.tip"),
    ("dialog.qwen", "workspace_id", "set.dialog_qwen.workspace_id", "str", None,
     "set.dialog_qwen.workspace_id.desc",
     "set.dialog_qwen.workspace_id.tip"),
    ("dialog.qwen", "api_key", "set.dialog_qwen.api_key", "secret", None,
     "set.dialog_qwen.api_key.desc",
     "set.dialog_qwen.api_key.tip"),
    ("dialog.qwen", "model", "set.dialog_qwen.model", "combo",
     ["qwen-audio-3.0-realtime-plus", "qwen-audio-3.0-realtime-flash"],
     "set.dialog_qwen.model.desc",
     "set.dialog_qwen.model.tip"),
    ("dialog.qwen", "voice", "set.dialog_qwen.voice", "combo", _QWEN_VOICE_OPTIONS,
     "set.dialog_qwen.voice.desc",
     None),
    ("dialog.qwen", "instructions", "set.dialog_qwen.instructions", "text", None,
     "set.dialog_qwen.instructions.desc",
     "set.dialog_qwen.instructions.tip"),
    ("dialog.qwen", "turn_detection", "set.dialog_qwen.turn_detection", "combo", ["server_vad", "smart_turn"],
     "set.dialog_qwen.turn_detection.desc",
     "set.dialog_qwen.turn_detection.tip"),
    ("dialog.qwen", "vad_threshold", "set.dialog_qwen.vad_threshold", "str", None,
     "set.dialog_qwen.vad_threshold.desc",
     "set.dialog_qwen.vad_threshold.tip"),
    ("dialog.qwen", "silence_duration_ms", "set.dialog_qwen.silence_duration_ms", "slider", (200, 6000, 100, "set.unit.ms"),
     "set.dialog_qwen.silence_duration_ms.desc",
     "set.dialog_qwen.silence_duration_ms.tip"),
    ("dialog.qwen", "enable_speech_emotion", "set.dialog_qwen.enable_speech_emotion", "bool", None,
     "set.dialog_qwen.enable_speech_emotion.desc",
     None),
    ("dialog.qwen", "max_history_turns", "set.dialog_qwen.max_history_turns", "slider", (1, 50, 1, "set.unit.turn"),
     "set.dialog_qwen.max_history_turns.desc",
     "set.dialog_qwen.max_history_turns.tip"),
    ("dialog.qwen", "enable_search", "set.dialog_qwen.enable_search", "bool", None,
     "set.dialog_qwen.enable_search.desc",
     None),
    # ---- 发行构建专用 ----
    # 字段恒在表里（配置项本身与发行渠道无关），是否渲染由发行层判定函数
    # 决定：源码构建整组不出现；cloud_sync_keys 另有「已打开过就仍渲染」的
    # 例外，见 _steam_page_groups 里的说明。
    ("steam", "cloud_sync_keys", "set.steam.cloud_sync_keys", "bool", None,
     "set.steam.cloud_sync_keys_desc",
     "set.steam.cloud_sync_keys_tip"),
    ("steam", "use_gpu", "set.steam.use_gpu", "bool", None,
     "set.steam.use_gpu_desc",
     "set.steam.use_gpu_tip"),
]

_ADVANCED_FIELDS = [
    # ---- 高级：腾讯云 ----
    ("tencent", "menu_model_large_2_0", "set.tencent.menu_model_large_2_0", "combo", _tier_models("tencent", "large_2_0"),
     "set.tencent.menu_model_large_2_0.desc", None),
    ("tencent", "menu_model_large_1_0", "set.tencent.menu_model_large_1_0", "combo", _tier_models("tencent", "large_1_0"),
     "set.tencent.menu_model_large_1_0.desc", None),
    ("tencent", "menu_model_general", "set.tencent.menu_model_general", "combo", _tier_models("tencent", "general"),
     "set.tencent.menu_model_general.desc", None),
    ("tencent", "filter_modal", "set.tencent.filter_modal", "combo", ["0", "1", "2"],
     "set.tencent.filter_modal.desc", None),
    ("tencent", "filter_punc", "set.tencent.filter_punc", "combo", ["0", "1", "2"],
     "set.tencent.filter_punc.desc", None),
    ("tencent", "convert_num_mode", "set.tencent.convert_num_mode", "combo", ["0", "1"],
     "set.tencent.convert_num_mode.desc", None),
    ("tencent", "filter_dirty", "set.tencent.filter_dirty", "combo", ["0", "1", "2"],
     "set.tencent.filter_dirty.desc", None),
    ("tencent", "hotword_id", "set.tencent.hotword_id", "str", None,
     "set.tencent.hotword_id.desc",
     "set.tencent.hotword_id.tip"),
    ("tencent", "customization_id", "set.tencent.customization_id", "str", None,
     "set.tencent.customization_id.desc",
     "set.tencent.customization_id.tip"),
    # ---- 高级：FunASR 本地 ----
    ("funasr", "model", "set.funasr.model", "str", None,
     "set.funasr.model.desc",
     "set.funasr.model.tip"),
    ("funasr", "device", "set.funasr.device", "combo", ["auto", "cpu", "cuda"],
     "set.funasr.device.desc", None),
    # 候选值是持久化原文（中文），显示名在 _COMBO_LABELS 里映射到词条
    ("funasr", "chunk_preset", "set.funasr.chunk_preset", "combo", ["默认", "低延迟", "高准确"],  # noqa: i18n
     "set.funasr.chunk_preset.desc",
     "set.funasr.chunk_preset.tip"),
    ("funasr", "hotword", "set.funasr.hotword", "str", None,
     "set.funasr.hotword.desc", "set.funasr.hotword.tip"),
    ("funasr", "local_dir", "set.funasr.local_dir", "str", None,
     "set.funasr.local_dir.desc",
     "set.funasr.local_dir.tip"),
    # ---- 高级：阿里云 ----
    ("aliyun", "disfluency_removal", "set.aliyun.disfluency_removal", "bool", None,
     "set.aliyun.disfluency_removal.desc", None),
    ("aliyun", "max_sentence_silence", "set.aliyun.max_sentence_silence", "slider", (200, 6000, 100, "set.unit.ms"),
     "set.aliyun.max_sentence_silence.desc", None),
    ("aliyun", "vocabulary_id", "set.aliyun.vocabulary_id", "str", None,
     "set.aliyun.vocabulary_id.desc",
     "set.aliyun.vocabulary_id.tip"),
    ("aliyun", "language_hints", "set.aliyun.language_hints", "str", None,
     "set.aliyun.language_hints.desc",
     "set.aliyun.language_hints.tip"),
    # ---- 高级：讯飞 ----
    ("xfyun", "std_lang", "set.xfyun.std_lang", "combo", ["cn", "en"],
     "set.xfyun.std_lang.desc", "set.xfyun.std_lang.tip"),
    ("xfyun", "filter_modal", "set.xfyun.filter_modal", "bool", None,
     "set.xfyun.filter_modal.desc", "set.xfyun.filter_modal.tip"),
    ("xfyun", "std_filter_modal", "set.xfyun.std_filter_modal", "bool", None,
     "set.xfyun.std_filter_modal.desc", "set.xfyun.std_filter_modal.tip"),
    ("xfyun", "std_punc", "set.xfyun.std_punc", "bool", None,
     "set.xfyun.std_punc.desc", "set.xfyun.std_punc.tip"),
    ("xfyun", "pd", "set.xfyun.pd", "str", None,
     "set.xfyun.pd.desc",
     "set.xfyun.pd.tip"),
    # ---- 高级：火山引擎 ----
    ("volcengine", "bigasr_billing", "set.volcengine.bigasr_billing", "billing", ("duration", "concurrent"),
     "set.volcengine.bigasr_billing.desc", "set.volcengine.bigasr_billing.tip"),
    ("volcengine", "seedasr_billing", "set.volcengine.seedasr_billing", "billing", ("duration", "concurrent"),
     "set.volcengine.seedasr_billing.desc", "set.volcengine.seedasr_billing.tip"),
    ("volcengine", "enable_ddc", "set.volcengine.enable_ddc", "bool", None,
     "set.volcengine.enable_ddc.desc", None),
    ("volcengine", "enable_nonstream", "set.volcengine.enable_nonstream", "bool", None,
     "set.volcengine.enable_nonstream.desc", "set.volcengine.enable_nonstream.tip"),
    # ---- 高级：录音检测 ----
    ("audio", "warmup_capture", "set.audio.warmup_capture", "bool", None,
     "set.audio.warmup_capture.desc",
     "set.audio.warmup_capture.tip"),
    ("audio", "input_device", "set.audio.input_device", "device_combo", None,
     "set.audio.input_device.desc",
     "set.audio.input_device.tip"),
    ("recording", "auto_stop_seconds", "set.recording.auto_stop_seconds", "slider", (0, 300, 5, "set.unit.sec"),
     "set.recording.auto_stop_seconds.desc", None),
    ("recording", "max_session_seconds", "set.recording.max_session_seconds", "slider", (0, 3600, 10, "set.unit.sec"),
     "set.recording.max_session_seconds.desc",
     "set.recording.max_session_seconds.tip"),
    ("recording", "pause_background_audio", "set.recording.pause_background_audio", "bool", None,
     "set.recording.pause_background_audio.desc",
     "set.recording.pause_background_audio.tip"),
    ("recording", "mute_background_audio", "set.recording.mute_background_audio", "bool", None,
     "set.recording.mute_background_audio.desc",
     "set.recording.mute_background_audio.tip"),
    ("recording", "mute_only_game_mode", "set.recording.mute_only_game_mode", "bool", None,
     "set.recording.mute_only_game_mode.desc",
     "set.recording.mute_only_game_mode.tip"),
    ("recording", "cue_sound", "set.recording.cue_sound", "bool", None,
     "set.recording.cue_sound.desc", "set.recording.cue_sound.tip"),
    ("recording", "cue_style", "set.recording.cue_style", "combo",
     {"bubble": "set.combo.recording.cue_style.bubble",
      "cork": "set.combo.recording.cue_style.cork",
      "click": "set.combo.recording.cue_style.click"},
     "set.recording.cue_style.desc", "set.recording.cue_style.tip"),
    ("vad", "energy_threshold", "set.vad.energy_threshold", "slider", (50, 5000, 10),
     "set.vad.energy_threshold.desc",
     "set.vad.energy_threshold.tip"),
    ("vad", "silence_duration_ms", "set.vad.silence_duration_ms", "slider", (200, 3000, 50, "set.unit.ms"),
     "set.vad.silence_duration_ms.desc", None),
    ("vad", "min_speech_ms", "set.vad.min_speech_ms", "slider", (100, 2000, 50, "set.unit.ms"),
     "set.vad.min_speech_ms.desc", None),
    # ---- 高级：输出 ----
    ("injection", "paste_delay_ms", "set.injection.paste_delay_ms", "slider", (10, 1000, 10, "set.unit.ms"),
     "set.injection.paste_delay_ms.desc",
     "set.injection.paste_delay_ms.tip"),
    # ---- 高级：AI 修正 ----
    ("llm", "enable", "set.llm.enable", "bool", None,
     "set.llm.enable.desc", "set.llm.enable.tip"),
    ("llm", "base_url", "set.llm.base_url", "str", None,
     "set.llm.base_url.desc",
     "set.llm.base_url.tip"),
    ("llm", "api_key", "set.llm.api_key", "secret", None,
     "set.llm.api_key.desc",
     "set.llm.api_key.tip"),
    ("llm", "model", "set.llm.model", "str", None,
     "set.llm.model.desc",
     "set.llm.model.tip"),
    ("llm", "timeout_ms", "set.llm.timeout_ms", "slider", (1000, 10000, 500, "set.unit.ms"),
     "set.llm.timeout_ms.desc", None),
    ("llm", "disable_thinking", "set.llm.disable_thinking", "bool", None,
     "set.llm.disable_thinking.desc",
     "set.llm.disable_thinking.tip"),
    ("llm", "batch_sentences", "set.llm.batch_sentences", "slider", (1, 10, 1),
     "set.llm.batch_sentences.desc",
     "set.llm.batch_sentences.tip"),
    ("llm", "batch_idle_ms", "set.llm.batch_idle_ms", "slider", (500, 10000, 500, "set.unit.ms"),
     "set.llm.batch_idle_ms.desc",
     "set.llm.batch_idle_ms.tip"),
    ("llm", "wait_for_correction", "set.llm.wait_for_correction", "bool", None,
     "set.llm.wait_for_correction.desc",
     "set.llm.wait_for_correction.tip"),
    ("llm", "history_context", "set.llm.history_context", "bool", None,
     "set.llm.history_context.desc",
     "set.llm.history_context.tip"),
    ("llm", "prompt", "set.llm.prompt", "str", None,
     "set.llm.prompt.desc",
     "set.llm.prompt.tip"),
    # ---- 高级：语音命令 ----
    ("commands", "prefix", "set.commands.prefix", "str", None,
     "set.commands.prefix.desc",
     "set.commands.prefix.tip"),
    ("commands", "hotkey", "set.commands.hotkey", "hotkey", None,
     "set.commands.hotkey.desc",
     "set.commands.hotkey.tip"),
    ("commands", "ai_prefix", "set.commands.ai_prefix", "str", None,
     "set.commands.ai_prefix.desc",
     "set.commands.ai_prefix.tip"),
    ("commands", "ai_thinking", "set.commands.ai_thinking", "bool", None,
     "set.commands.ai_thinking.desc",
     "set.commands.ai_thinking.tip"
     ),
    ("commands", "ai_prompt_template", "set.commands.ai_prompt_template", "text", None,
     "set.commands.ai_prompt_template.desc",
     "set.commands.ai_prompt_template.tip"),
    ("commands", "phrases_enable", "set.commands.phrases_enable", "bool", None,
     "set.commands.phrases_enable.desc",
     "set.commands.phrases_enable.tip"),
    ("commands", "standby_seconds", "set.commands.standby_seconds", "slider", (3, 30, 1, "set.unit.sec"),
     "set.commands.standby_seconds.desc", None),
    # ---- 高级：热键 ----
    ("hotkey", "hold_poll_ms", "set.hotkey.hold_poll_ms", "slider", (5, 200, 5, "set.unit.ms"),
     "set.hotkey.hold_poll_ms.desc",
     "set.hotkey.hold_poll_ms.tip"),
]


def _field_label_keys() -> dict:
    """(section, key) -> 该字段标签的 i18n 键（运行时报错文案取显示名用）。"""
    out = {}
    for table in (_FIELDS, _ADVANCED_FIELDS):
        for f in table:
            if len(f) > 2 and f[0] and f[1]:
                out[(f[0], f[1])] = f[2]
    return out


def _list_input_devices() -> list[str]:
    """枚举输入设备名：只列 WASAPI（现代低延迟）下的真实输入设备，
    避免把 MME/DirectSound/WDM-KS 下同一物理设备的重复条目全列出。

    每次调用都实时走 PortAudio 枚举，天然反映设备热插拔。
    """
    import sounddevice as sd

    apis = sd.query_hostapis()
    # 优先 WASAPI，无则回退默认 host api
    api_idx = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]),
                   sd.default.hostapi)
    api = sd.query_hostapis(api_idx)
    names = []
    for i in api["devices"]:
        d = sd.query_devices(i)
        if d.get("max_input_channels", 0) > 0:
            names.append(d["name"])
    return names


def _list_output_devices() -> list[str]:
    """枚举输出设备名（AI 播报用）：同 _list_input_devices 的 WASAPI 过滤。"""
    import sounddevice as sd

    apis = sd.query_hostapis()
    api_idx = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]),
                   sd.default.hostapi)
    api = sd.query_hostapis(api_idx)
    names = []
    for i in api["devices"]:
        d = sd.query_devices(i)
        if d.get("max_output_channels", 0) > 0:
            names.append(d["name"])
    return names


class DeviceCombo(QComboBox):
    """设备下拉框：每次展开下拉时重新枚举设备列表（输入/输出共用）。

    设置窗口打开期间插入/拔出设备（USB 声卡、蓝牙耳机等），
    点开下拉即可看到最新列表，无需重开设置窗口。
    """

    def __init__(self, current: str = "", inputs: bool = True):
        super().__init__()
        self._cfg_device = current  # 配置文件里的设备名（sounddevice 按子串匹配）
        self._inputs = inputs
        self.refresh()

    def refresh(self) -> None:
        """重建列表项，尽量保持当前选择不跳变。"""
        # 优先保持用户当前 UI 选择（改过未保存也不丢），其次配置值
        keep = self.currentData() if self.count() else None
        target = keep if keep else self._cfg_device
        self.blockSignals(True)
        self.clear()
        self.addItem(t("set.device.system_default"), "")
        try:
            names = (_list_input_devices() if self._inputs
                     else _list_output_devices())
            for name in names:
                self.addItem(name, name)
        except Exception:
            logger.debug("列举输出设备失败", exc_info=True)
        idx = 0
        if target:
            idx = self.findData(target)
            if idx < 0:  # 配置名按子串匹配，精确找不到时回退子串遍历
                for i in range(1, self.count()):
                    if target in str(self.itemData(i) or ""):
                        idx = i
                        break
        self.setCurrentIndex(max(0, idx))
        self.blockSignals(False)

    def showPopup(self) -> None:
        self.refresh()
        super().showPopup()


class SettingsDialog(QDialog):
    """配置编辑对话框。传入当前 cfg（SimpleNamespace），保存时写回 config.yaml。

    悬浮条不透明度滑块支持实时预览：拖动时发 bar_opacity_preview 信号通知
    app 即时改悬浮条 alpha（不写文件，点保存才落盘）。
    """

    # 实时预览信号：app 连接到 bar.set_bar_opacity
    bar_opacity_preview = Signal(int)

    # AI 修正连通性检测完成（后台线程 emit，自动 queued 回主线程）
    llm_test_done = Signal(bool, str)

    # 语音对话测试连接完成（后台线程 emit，自动 queued 回主线程）
    dialog_test_done = Signal(bool, str)

    # 创意工坊：后台动作完成通知（ok, 动作, 详情）。
    # 动作："publish"（详情=标题）/ "unsubscribe"（详情=物品 ID）。
    # 之所以不让后台线程直接碰控件：退订/上传都是阻塞调用
    # （上传大包可达十几分钟），跑在后台线程里，回主线程只能靠信号。
    workshop_done = Signal(bool, str, str)

    # 「永久为管理员启动」已确认 UAC：提权实例已接管，app 收到后退出当前实例
    restart_requested = Signal()

    def __init__(self, cfg, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle(t("set.window_title"))
        self.setMinimumWidth(720)
        self.setMinimumHeight(520)
        self.resize(860, 620)

        # 现代无边框窗口：去掉系统标题栏，自绘标题栏（拖动/关闭）。
        # 注意：不设 WA_TranslucentBackground——透明表面会禁用 ClearType
        # 亚像素渲染（文字只能灰度抗锯齿，观感发糊）；不透明窗口圆角由
        # Win11 DWM 自动裁切，文字恢复 ClearType 锐度（Win10 为方角）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)

        # 字体：设置界面固定字体栈（不跟随用户自定义字体）——用户字体通常只有单字重，
        # 加粗/层次出不来；栈首普惠体（多字重已随 fonts/ 全量注册），文件缺失时
        # Qt 自动落栈内系统回退位，不崩不空白。基础字号 10.5pt。
        font = QFont()
        font.setFamilies(["Alibaba PuHuiTi 3.0",
                          "Segoe UI Variable Text", "Segoe UI",
                          "Microsoft YaHei UI", "Microsoft YaHei"])
        font.setPointSizeF(10.5)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(font)

        # 现代化样式：直接应用主题 palette
        self._palette = _theme_palette(cfg)
        self.setStyleSheet(_settings_qss(self._palette))

        self._widgets: dict[tuple[str, Optional[str]], QWidget] = {}

        def pick(section: str, keys: tuple, src=None) -> list:
            fields = _FIELDS if src is None else src
            return [f for f in fields if f[0] == section and f[1] in keys]

        # ---- 布局：圆角根容器 + 自绘标题栏 + 分页（常用 / 高级） ----
        outer = QWidget(self)
        outer.setObjectName("dialogRoot")
        root = QVBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏（可拖动窗口；左侧标题，右侧「打开配置文件」胶囊 + 关闭按钮
        # 与参考图顶栏一致）
        self._drag_pos: Optional[object] = None
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setCursor(Qt.OpenHandCursor)
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(22, 14, 12, 8)
        title_label = QLabel(t("set.title"))
        title_label.setObjectName("dialogTitle")
        btn_open = QPushButton(t("set.btn.open_config"))
        btn_open.setObjectName("btnGhost")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.setToolTip(t("set.btn.open_config_tip"))
        btn_open.clicked.connect(self._open_config_file)
        btn_reset = QPushButton(t("set.btn.reset_defaults"))
        btn_reset.setObjectName("btnGhost")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.setToolTip(t("set.btn.reset_defaults_tip"))
        btn_reset.clicked.connect(self._on_reset_defaults)
        btn_close = QPushButton("×")
        btn_close.setObjectName("btnClose")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setToolTip(t("set.btn.close_tip"))
        btn_close.clicked.connect(self.reject)
        tb.addWidget(title_label)
        tb.addStretch(1)
        tb.addWidget(btn_open)
        tb.addSpacing(8)
        tb.addWidget(btn_reset)
        tb.addSpacing(10)
        tb.addWidget(btn_close)
        root.addWidget(title_bar)

        def _title_mouse_press(ev) -> None:
            if ev.button() == Qt.LeftButton:
                self._drag_pos = (
                    ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )

        def _title_mouse_move(ev) -> None:
            if self._drag_pos is not None and ev.buttons() & Qt.LeftButton:
                self.move(ev.globalPosition().toPoint() - self._drag_pos)

        def _title_mouse_release(ev) -> None:
            self._drag_pos = None

        title_bar.mousePressEvent = _title_mouse_press
        title_bar.mouseMoveEvent = _title_mouse_move
        title_bar.mouseReleaseEvent = _title_mouse_release

        # ---- 主体：左侧导航 + 右侧分页（Win11 设置风格） ----
        content = QVBoxLayout()
        content.setContentsMargins(12, 2, 16, 14)
        content.setSpacing(10)

        nav = QListWidget()
        nav.setObjectName("navList")
        nav.setFixedWidth(196)
        nav.setSpacing(1)
        nav.setIconSize(QSize(20, 20))
        nav.setFocusPolicy(Qt.NoFocus)
        stack = QStackedWidget()

        def add_page(page_key: str, page: QWidget) -> None:
            # page_key 是 i18n 键（_NAV_KINDS 同口径）；显示名在此处才 t()
            # 因为此刻语言已由 main 的 apply_configured_language 定好。
            item = QListWidgetItem(_nav_icon(_NAV_KINDS.get(page_key, "info")),
                                   t(page_key))
            nav.addItem(item)
            # _build_page 已内置滚动区，不再外包 QScrollArea
            stack.addWidget(page)

        # 外部服务不可用时（见 steam_integration.external_services_allowed）：
        # 引擎密钥页、AI 修正页、语音对话页、通用 AI 接口分组、引擎排序编辑器、
        # 语音命令的 AI 字段全部不装配，只留内置离线引擎与纯本地功能。
        _ext = external_services_allowed()

        # 通用设置：永久提权按钮（置顶，无分区标题）/ 热键 / 输出 / 界面 / 引擎排序
        # 参考图无页头：内容直接从分区开始，页面归属只看左侧导航选中态；
        # 提权按钮行作为 QWidget 直插页顶（不走分组，无「程序行为」标题）
        general_groups = [
            self._build_admin_launch_row(),
            # 长按说话：两个面向用户的字段与「录音热键」同组紧邻显示。
            # pick 只在单张表里过滤，「录音热键」在 _FIELDS、hold_poll_ms 在 _ADVANCED_FIELDS，
            # 故分两次 pick 后拼接；写成单次 pick(..., _ADVANCED_FIELDS) 会得到空分组，
            # 把「录音热键」悄悄抹掉（tests/test_hold_ui_config.py 有护栏）。
            ("set.group.hotkey", pick("hotkey", ("toggle", "hold_to_talk",
                                     "hold_threshold_ms"))
             + pick("hotkey", ("hold_poll_ms",), _ADVANCED_FIELDS)),
            ("set.group.output", pick("injection", ("paste_delay_ms",), _ADVANCED_FIELDS)),
            ("set.group.interface", pick("ui", ("language", "auto_hide_seconds",
                                                "bar_opacity", "settings_style"))),
        ]
        if _ext:
            general_groups.append(self._build_engine_order_editor())
        # Steam 相关项不再挂在「通用设置」页：它们与通用配置是两码事
        # （云存档 / 显卡加速扩展包 / 创意工坊），挤在同一页会让"通用"越滚越长。
        # 独立成「Steam」页，见下方 add_page("set.page.steam", ...)。
        add_page("set.page.general", self._build_page(general_groups))

        # 引擎密钥：各厂商一张可展开卡（复刻参考图「插件配置」卡片列表）；
        # 显示/隐藏与排序开关已移至"常规 -> 引擎排序"。当前引擎的卡片默认展开。
        keys_by_provider = {
            "tencent": ("secret_id", "secret_key", "app_id"),
            "aliyun": ("api_key",),
            "xfyun": (("set.sec.xfyun_large", ("app_id", "api_key", "api_secret")),
                      ("set.sec.xfyun_std", ("std_app_id", "std_api_key"))),
            "volcengine": (("set.sec.auth_new", ("api_key",)),
                           ("set.sec.auth_legacy", ("app_id", "access_token"))),
        }
        ordered = self._ordered_providers()
        current_engine = str(getattr(self.cfg, "engine", "") or "").split(":", 1)[0]
        if not _ext:
            # 外部服务不可用时只剩内置离线引擎：厂商卡片与厂商参数卡不渲染
            ordered = [p for p in ordered if p == "funasr"]
        key_cards = []
        for p in ordered:
            spec = keys_by_provider.get(p)
            if not spec:
                continue  # funasr 无密钥，无卡片
            if isinstance(spec[0], tuple):
                # 讯飞/火山：按小节分列多套密钥
                fields = []
                for sec_title, keys in spec:
                    fields.append(_sec(sec_title))
                    fields += [f for f in _FIELDS if f[0] == p and f[1] in keys]
            else:
                fields = [f for f in _FIELDS if f[0] == p and f[1] in spec]
            key_cards.append(self._build_accordion_group(
                t(self._ENGINE_PROVIDER_TITLES[p]),
                t(self._ENGINE_KEY_DESCS.get(p, "")),
                fields, expanded=(p == current_engine)))
        # 外部服务不可用时无厂商密钥可填，该页不出现（留一个空页只会让人以为漏了功能）
        if _ext:
            # 通用 AI 接口：语音命令「帮我」与 AI 修正共用的端点/密钥/模型，
            # 卡片样式与厂商卡一致，排在厂商卡之后
            key_cards.append(self._build_accordion_group(
                t("set.group.llm_common"), t("set.llm_common.desc"),
                pick("llm", ("base_url", "api_key", "model"), _ADVANCED_FIELDS)
                + [self._build_llm_test_row()]))
            add_page("set.page.keys", self._build_page(key_cards))

        # 引擎参数：各厂商识别效果参数，合并到一页折叠卡列表展示。
        # 卡片顺序与名称跟随 ui.engine_order 与统一短名（与引擎密钥页/右键菜单一致）；
        # 讯飞按版本小节、火山按豆包 1.0/2.0 小节分列。
        params_by_provider = {
            "tencent": ([_sec("set.sec.menu_models")]
                        + pick("tencent", ("menu_model_large_2_0",
                                           "menu_model_large_1_0",
                                           "menu_model_general"), _ADVANCED_FIELDS)
                        + [_sec("set.sec.filtering")]
                        + pick("tencent", ("filter_modal", "filter_punc",
                                           "convert_num_mode",
                                           "filter_dirty", "hotword_id",
                                           "customization_id"), _ADVANCED_FIELDS)),
            "aliyun": ([_sec("set.sec.filtering")]
                       + pick("aliyun",
                              ("disfluency_removal", "max_sentence_silence",
                               "vocabulary_id", "language_hints"),
                              _ADVANCED_FIELDS)),
            "xfyun": ([_sec("set.sec.xfyun_large")]
                      + pick("xfyun", ("filter_modal",), _ADVANCED_FIELDS)
                      + [_sec("set.sec.xfyun_std")]
                      + pick("xfyun", ("std_lang", "std_punc",
                                       "std_filter_modal", "pd"),
                             _ADVANCED_FIELDS)),
            "volcengine": ([_sec("set.sec.doubao_1")]
                           + pick("volcengine", ("bigasr_billing",), _ADVANCED_FIELDS)
                           + [_sec("set.sec.doubao_2")]
                           + pick("volcengine", ("seedasr_billing",), _ADVANCED_FIELDS)
                           + pick("volcengine", ("enable_ddc", "enable_nonstream"),
                                  _ADVANCED_FIELDS)),
            "funasr": pick("funasr", ("model", "device", "chunk_preset", "hotword",
                                     "local_dir"),
                           _ADVANCED_FIELDS),
        }
        param_cards = []
        _frozen = bool(getattr(sys, "frozen", False))
        for p in ordered:
            # 打包版不含本地引擎依赖：FunASR 参数页换成"不可用说明"卡片
            # 避免用户误以为填了本地目录/下载渠道就能用
            if p == "funasr" and _frozen:
                param_cards.append(self._build_funasr_frozen_card())
                continue
            param_cards.append(self._build_accordion_group(
                t(self._ENGINE_PROVIDER_TITLES[p]),
                t(self._engine_param_desc_key(p)),
                params_by_provider[p], expanded=(p == current_engine)))
        add_page("set.page.params", self._build_page(param_cards))

        # 页名已是"AI 修正"，组内不再重复"文本修正"标题（多余）；
        # 主开关也可在右键菜单「注入逻辑」处快速启停，这里统一管理修正行为参数。
        # 端点/密钥/模型/连通性检测在「引擎密钥 → 通用 AI 接口」（语音命令共用）
        # 外部服务不可用时 AI 修正页不出现
        if _ext:
            add_page("set.page.ai_fix", self._build_page([
                ("", pick("llm", ("enable", "wait_for_correction", "history_context",
                                  "timeout_ms", "disable_thinking",
                                  "batch_sentences", "batch_idle_ms",
                                  "prompt"),
                          _ADVANCED_FIELDS)),
            ]))

        phrase_editor = self._build_phrase_editor()
        # 语音命令页：外部服务不可用时去掉「帮我」AI 前缀相关字段（走外部 LLM 服务）
        # 其余本地命令（删除上一句、自定义短语、待命热键）完整保留
        _cmd_keys = (("prefix", "hotkey", "ai_prefix", "ai_thinking",
                      "ai_prompt_template", "phrases_enable", "standby_seconds")
                     if _ext else
                     ("prefix", "hotkey", "phrases_enable", "standby_seconds"))
        add_page("set.page.commands", self._build_page([
            ("set.group.commands",
             pick("commands", _cmd_keys, _ADVANCED_FIELDS)),
            self._build_command_reference(),
            phrase_editor,
        ]))
        # 自定义短语词表：仅开启「自定义短语」开关后显示（_widgets 在
        # _build_page/_make_row 期间填充，绑定须在该页构建之后）
        phrase_sw = self._widgets.get(("commands", "phrases_enable"))
        if phrase_sw is not None:
            phrase_editor.setVisible(phrase_sw.isChecked())
            phrase_sw.toggled.connect(phrase_editor.setVisible)

        add_page("set.page.recording", self._build_page([
            ("set.group.recording",
             pick("audio", ("warmup_capture", "input_device"), _ADVANCED_FIELDS) +
             pick("recording", ("auto_stop_seconds", "max_session_seconds",
                                "pause_background_audio", "mute_background_audio",
                                "mute_only_game_mode", "cue_sound", "cue_style"),
                  _ADVANCED_FIELDS)),
            ("set.group.vad",
             pick("vad", ("energy_threshold", "silence_duration_ms",
                          "min_speech_ms"), _ADVANCED_FIELDS)),
        ]))

        # 语音模式页：接入（三家共用）→ provider 专属字段区（豆包 / Qwen /
        # Hermes 互斥显隐）→ 收音与播报（三家共用）。provider 下拉切换只显当前
        # 提供商字段组（显隐联动同短语编辑器 phrase_sw.toggled→setVisible）。
        # 外部服务不可用时语音对话页不出现（三家都是外部付费服务）。
        if _ext:
            provider_area, doubao_grp, qwen_grp, hermes_grp = (
                self._build_dialog_provider_area(pick))
            add_page("set.page.voice_mode", self._build_page([
                ("set.group.dialog_in",
                 pick("dialog", ("enable",)) +
                 pick("hotkey", ("dialog",)) +
                 pick("dialog", ("provider",)) +
                 [self._build_dialog_test_row()]),
                provider_area,
                ("set.group.dialog_io",
                 pick("dialog", ("output_device", "half_duplex", "auto_reconnect"))),
            ]))
            # provider 显隐联动：初始按配置值设定，之后随下拉切换（_widgets 已在
            # _build_dialog_provider_area/_build_page 期间填充，绑定须在构建之后）
            self._dialog_doubao_group = doubao_grp
            self._dialog_qwen_group = qwen_grp
            self._dialog_hermes_group = hermes_grp
            self._on_dialog_provider_changed()
            _prov_combo = self._widgets.get(("dialog", "provider"))
            if _prov_combo is not None:
                _prov_combo.currentIndexChanged.connect(self._on_dialog_provider_changed)

        # Steam 页（仅发行构建）：云存档 / 显卡加速扩展包 / 创意工坊。
        # 整页创建挂在 is_steam_build() 上：_steam_page_groups 对源码构建返回空，
        # 无条件建页会让源码构建多出一个空白「Steam」标签。
        if is_steam_build():
            add_page("set.page.steam",
                     self._build_page(self._steam_page_groups(pick)))

        # 关于：头部信息卡（图标+名称+版本）+ 可展开卡列表（同引擎密钥页样式）
        add_page("set.page.about", self._build_page(
            [self._build_about_header()] + self._build_about_cards()))

        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)

        # 参考图无导航分隔线：左侧导航与内容同底色，仅选中胶囊区分
        body_row = QHBoxLayout()
        body_row.setSpacing(8)
        body_row.addWidget(nav)
        body_row.addWidget(stack, 1)
        content.addLayout(body_row, 1)

        btn_save = QPushButton(t("set.btn.save"))
        btn_save.setObjectName("btnSave")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton(t("set.btn.close"))
        btn_cancel.setToolTip(t("set.btn.close_tip2"))
        btn_cancel.clicked.connect(self.reject)
        # 重启生效提示：小字灰文本放底部左侧（不是横条），悬停可见详情
        restart_hint = QLabel(t("set.restart_hint"))
        restart_hint.setObjectName("fieldDesc")
        restart_hint.setToolTip(t("set.restart_hint_tip"))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addWidget(restart_hint)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        content.addLayout(btn_row)
        root.addLayout(content)

        # 顶层布局：只放圆角根容器（translucent 窗口下由容器承载背景）
        top = QVBoxLayout(self)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(outer)

        # 无边框窗口自由拉伸：边缘 8px 命中区 + 悬停方向光标 + 原生系统
        # 拉伸（startSystemResize）。事件过滤挂在根容器与标题栏上——
        # 四周 8px 命中带正落在两者的内边距区，不遮挡任何交互控件
        self._resize_margin = 8
        outer.setMouseTracking(True)
        outer.installEventFilter(self)
        title_bar.setMouseTracking(True)
        title_bar.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        """边缘拉伸过滤器：悬停显示方向箭头，左键按住走系统级缩放。

        命中边缘时吞掉按下事件（返回 True），标题栏拖动不冲突。局部变量名刻意用 ev_type
        而非 t：Python 变量判定是**函数级**的，一旦出现 `t = ...` 即遮蔽本模块的 i18n.t()。
        """
        ev_type = ev.type()
        if ev_type == QEvent.Type.MouseMove:
            edge = self._edge_at(ev.globalPosition().toPoint())
            if edge is not None:
                obj.setCursor(self._cursor_for_edge(edge))
            else:
                obj.unsetCursor()
        elif ev_type == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(ev.globalPosition().toPoint())
            if edge is not None and self.windowHandle() is not None:
                self.windowHandle().startSystemResize(edge)
                return True
        elif ev_type == QEvent.Type.MouseButtonRelease:
            obj.unsetCursor()
        return super().eventFilter(obj, ev)

    def _edge_at(self, global_pos) -> Optional[Qt.Edges]:
        """窗口坐标命中哪条边缘（角优先，角=两条边的组合标志）；不贴边返回 None。"""
        local = self.mapFromGlobal(global_pos)
        m = self._resize_margin
        w, h = self.width(), self.height()
        left = local.x() <= m
        right = local.x() >= w - m
        top = local.y() <= m
        bottom = local.y() >= h - m
        if top and left:
            return Qt.Edge.TopEdge | Qt.Edge.LeftEdge
        if top and right:
            return Qt.Edge.TopEdge | Qt.Edge.RightEdge
        if bottom and left:
            return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
        if bottom and right:
            return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
        if left:
            return Qt.Edge.LeftEdge
        if right:
            return Qt.Edge.RightEdge
        if top:
            return Qt.Edge.TopEdge
        if bottom:
            return Qt.Edge.BottomEdge
        return None

    @staticmethod
    def _cursor_for_edge(edge) -> Qt.CursorShape:
        """边缘（含边组合的角）→ 对应方向的双向箭头光标。"""
        if edge in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        if edge in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edge in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                    Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    def _build_page(self, groups: list, stretch: bool = True) -> QWidget:
        """构建设置页：无页头，扁平分区用发丝分割线串联，手风琴卡片之间纯留白。

        groups 项可为 ("标题", fields) 元组、已构建的 QWidget 或 AccordionCard。
        """
        outer = QWidget()
        v = QVBoxLayout(outer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 滚动区：分区/卡片列表
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 12, 10, 8)
        lay.setSpacing(0)
        prev_card = True  # 首项之前不加分隔线
        for g in groups:
            is_card = isinstance(g, AccordionCard)
            if lay.count():
                if is_card or prev_card:
                    lay.addSpacing(12)
                else:
                    # 扁平分区之间：醒目的区块分割线 + 加大留白，区块边界一眼可辨
                    lay.addSpacing(18)
                    sep = QFrame()
                    sep.setObjectName("groupSep")
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setFixedHeight(1)
                    lay.addWidget(sep)
                    lay.addSpacing(14)
            if isinstance(g, QWidget):
                lay.addWidget(g)
            else:
                gtitle, fields = g
                lay.addWidget(self._build_group(gtitle, fields))
            prev_card = is_card
        if stretch:
            lay.addStretch(1)
        v.addWidget(self._wrap_scroll(body), 1)
        return outer

    def _build_about_header(self) -> QWidget:
        """关于页头部卡：圆角图标 + 产品名 + 版本/性质（卡片样式同引擎密钥页）。"""
        card = QWidget()
        card.setObjectName("accordionCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(card)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(self._rounded_pixmap(app_icon(), 44, 10))
        h.addWidget(icon)

        left = QVBoxLayout()
        left.setSpacing(2)
        name = QLabel("Open-RealtimeASR-UI")
        name.setObjectName("accordionTitle")
        left.addWidget(name)
        meta = QLabel(t("set.about.version", version=__version__))
        meta.setObjectName("accordionDesc")
        left.addWidget(meta)
        h.addLayout(left, 1)
        return card

    @staticmethod
    def _rounded_pixmap(icon: QIcon, size: int, radius: int) -> QPixmap:
        """程序图标裁成圆角瓷砖（关于头部卡用）。"""
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, icon.pixmap(size, size))
        p.end()
        return out

    @staticmethod
    def _about_text(body: str) -> QLabel:
        """折叠卡正文段落：可选择的富文本。"""
        b = QLabel(body)
        b.setWordWrap(True)
        b.setTextFormat(Qt.RichText)
        b.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return b

    def _build_about_cards(self) -> list:
        """关于页折叠卡列表：点击展开正文（同引擎密钥页手风琴样式）。"""
        # 外部服务不可用时的正文不提云端引擎 / AI（那些入口不存在）
        body_key = ("set.about.app_body" if external_services_allowed()
                    else "set.about.app_body_offline")
        cards = []

        about = AccordionCard(t("set.about.app_title"), t("set.about.app_desc"),
                              expanded=True)
        v = QVBoxLayout(about.body_container())
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(8)
        v.addWidget(self._about_text(t(body_key)))
        v.addWidget(self._about_text(t("set.about.app_flow")))
        cards.append(about)

        # 使用帮助：内置文档离线可读（不依赖网络 / 版本严格对应）
        # 同时保留在线入口看最新版
        help_card = AccordionCard(t("set.about.help_title"),
                                  t("set.about.help_desc"))
        v = QVBoxLayout(help_card.body_container())
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        doc_btn = QPushButton(t("set.about.help_btn"))
        if doc_path() is None:
            # 这台机器上没有随包文档（干净克隆缺文件等）：只留在线入口
            doc_btn.setEnabled(False)
            doc_btn.setToolTip(t("set.about.help_missing"))
        else:
            doc_btn.setToolTip(t("set.about.help_btn_tip"))
        doc_btn.clicked.connect(self._open_bundled_doc)
        row.addWidget(doc_btn)
        online_btn = QPushButton(t("set.about.help_btn_online"))
        online_btn.setToolTip(self._doc_url())
        online_btn.clicked.connect(self._open_doc_url)
        row.addWidget(online_btn)
        row.addStretch(1)
        v.addLayout(row)
        cards.append(help_card)

        oss = AccordionCard(t("set.about.oss_title"), t("set.about.oss_desc"))
        v = QVBoxLayout(oss.body_container())
        v.setContentsMargins(0, 6, 0, 8)
        v.setSpacing(6)
        for lib, lic in _ABOUT_OSS:
            v.addWidget(self._about_text(f"<b>{lib}</b> · {t(lic)}"))
        v.addWidget(self._about_text(t("set.about.oss_footer")))
        cards.append(oss)

        contact = AccordionCard(t("set.about.contact_title"),
                                t("set.about.contact_desc"))
        v = QVBoxLayout(contact.body_container())
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        btn = QPushButton(t("set.about.contact_btn"))
        btn.setToolTip(_ABOUT_CONTACT_URL)
        btn.clicked.connect(self._open_contact_url)
        row.addWidget(btn)
        row.addStretch(1)
        v.addLayout(row)
        cards.append(contact)

        cards.append(self._build_log_card())
        return cards

    def _open_contact_url(self) -> None:
        """用系统默认浏览器打开 B 站空间动态（关于页「联系方式」卡）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(_ABOUT_CONTACT_URL))

    @staticmethod
    def _doc_url() -> str:
        """用户文档链接：按界面语言选中/英文版，未知语言回落中文。"""
        from ..i18n import language
        return _ABOUT_DOC_URLS.get(language(), _ABOUT_DOC_URL)

    def _open_bundled_doc(self) -> None:
        """打开随包的内置文档（关于页「使用帮助」卡）。"""
        from .doc_viewer import open_user_doc
        dlg = open_user_doc(parent=self, cfg=self.cfg)
        if dlg is None:
            QMessageBox.warning(self, t("common.notice"),
                                t("set.about.help_open_fail"))

    def _open_doc_url(self) -> None:
        """用系统默认浏览器打开使用文档（关于页「使用帮助」卡）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self._doc_url()))

    def _build_command_reference(self) -> QWidget:
        """语音命令参考表：默认收起的折叠区（点标题展开/收起），不占页面空间。"""
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        btn = QPushButton(t("set.cmdref.expand"))
        btn.setObjectName("cmdRefToggle")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(t("set.cmdref.toggle_tip"))
        v.addWidget(btn)

        body = QWidget()
        body.setVisible(False)
        grid = QGridLayout(body)
        grid.setContentsMargins(4, 8, 4, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        # 动作 ID -> 效果说明的 i18n 键（说法列由命令表变体自动生成，见下）
        _CMD_EFFECT_KEYS = {
            "enter": "set.cmdref.effect.enter",
            "delete_last": "set.cmdref.effect.delete_last",
            "select_all": "set.cmdref.effect.select_all",
            "undo": "set.cmdref.effect.undo",
            "backspace": "set.cmdref.effect.backspace",
            "stop_record": "set.cmdref.effect.stop_record",
            "method_keys": "set.cmdref.effect.method_keys",
            "method_clip": "set.cmdref.effect.method_clip",
            "keep_clipboard": "set.cmdref.effect.keep_clipboard",
            "live_intermediate": "set.cmdref.effect.live_intermediate",
            "llm_enable": "set.cmdref.effect.llm_enable",
            "interrupt_delete": "set.cmdref.effect.interrupt_delete",
        }
        rows = [
            (t("set.cmdref.trigger_say"), t("set.cmdref.trigger_effect")),
            (t("set.cmdref.hotkey_say"), t("set.cmdref.hotkey_effect")),
        ]
        # 说法列直接取命令表全部变体：参考表与实际匹配永远同步
        # 以后补变体（如口音替代词）不需要改这里
        from ..core import voice_commands

        for variants, action, is_toggle in voice_commands.local_commands():
            effect = t(_CMD_EFFECT_KEYS[action]) if action in _CMD_EFFECT_KEYS else ""
            if is_toggle:
                effect += t("set.cmdref.toggle_suffix")
            rows.append((" / ".join(variants), effect))
        rows += [
            (t("set.cmdref.ai_polish_say"), t("set.cmdref.ai_polish_effect")),
            (t("set.cmdref.ai_punct_say"), t("set.cmdref.ai_punct_effect")),
            (t("set.cmdref.ai_translate_say"), t("set.cmdref.ai_translate_effect")),
            (t("set.cmdref.ai_expand_say"), t("set.cmdref.ai_expand_effect")),
            (t("set.cmdref.ai_tone_say"), t("set.cmdref.ai_tone_effect")),
            (t("set.cmdref.ai_format_say"), t("set.cmdref.ai_format_effect")),
            (t("set.cmdref.phrase_say"), t("set.cmdref.phrase_effect")),
        ]
        head1 = QLabel(t("set.cmdref.head_say"))
        head2 = QLabel(t("set.cmdref.head_effect"))
        for h in (head1, head2):
            h.setObjectName("fieldSection")
        grid.addWidget(head1, 0, 0)
        grid.addWidget(head2, 0, 1)
        for i, (say, effect) in enumerate(rows, start=1):
            l1 = QLabel(say)
            l1.setWordWrap(True)
            l2 = QLabel(effect)
            l2.setWordWrap(True)
            grid.addWidget(l1, i, 0)
            grid.addWidget(l2, i, 1)
        note = QLabel(t("set.cmdref.note"))
        note.setObjectName("fieldDesc")
        note.setWordWrap(True)
        grid.addWidget(note, len(rows) + 1, 0, 1, 2)
        v.addWidget(body)

        def _toggle():
            expanded = body.isVisible()
            body.setVisible(not expanded)
            btn.setText(t("set.cmdref.collapse") if not expanded
                        else t("set.cmdref.expand"))

        btn.clicked.connect(_toggle)
        return wrap

    def _build_phrase_editor(self) -> QWidget:
        """自定义短语词表编辑器：两列表格（短语名/短语内容）+ 添加/删除。

        点「保存」时随其他配置一并写入状态目录 phrases.json。
        """
        from ..core import custom_phrases

        sec = QWidget()
        v = QVBoxLayout(sec)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        st = QLabel(t("set.phrase.title"))
        st.setObjectName("sectionTitle")
        v.addWidget(st)
        desc = QLabel(t("set.phrase.desc"))
        desc.setObjectName("fieldDesc")
        desc.setWordWrap(True)
        v.addWidget(desc)

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels([t("set.phrase.col_name"),
                                         t("set.phrase.col_content")])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setFixedHeight(200)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 160)

        for item in custom_phrases.entries():
            name = str(item.get("name") or "")
            content = str(item.get("content") or "")
            if not name and not content:
                continue
            self._append_phrase_row(table, name, content)

        v.addSpacing(8)
        v.addWidget(table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_add = QPushButton(t("set.phrase.add"))
        btn_del = QPushButton(t("set.phrase.remove"))
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch(1)
        v.addSpacing(6)
        v.addLayout(btn_row)

        self._phrase_table = table
        btn_add.clicked.connect(lambda _=False: self._append_phrase_row(table, "", ""))
        btn_del.clicked.connect(self._on_phrase_remove)
        return sec

    def _append_phrase_row(self, table: QTableWidget, name: str,
                           content: str) -> None:
        """词表追加一行（打开对话框填充与「添加」按钮共用）。"""
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(content))

    def _on_phrase_remove(self) -> None:
        """删除选中的短语行（整行选中模式，可能多行）。"""
        table = self._phrase_table
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows:
            self._themed_msg(t("set.phrase.need_selection"))
            return
        for r in rows:
            table.removeRow(r)

    def _collect_phrases(self) -> tuple[list[dict], str]:
        """收集词表行为短语列表；出错返回 ([], 错误文案)。空行跳过，
        短语名与内容须成对填写，短语名不区分大小写去重。"""
        table = self._phrase_table
        out: list[dict] = []
        seen: set[str] = set()
        for r in range(table.rowCount()):
            name_item = table.item(r, 0)
            content_item = table.item(r, 1)
            name = (name_item.text().strip() if name_item else "")
            content = (content_item.text().strip() if content_item else "")
            if not name and not content:
                continue
            if not name or not content:
                return [], t("set.phrase.err_incomplete")
            key = name.lower()
            if key in seen:
                return [], t("set.phrase.err_duplicate", name=name)
            seen.add(key)
            out.append({"name": name, "content": content})
        return out, ""

    @staticmethod
    def _wrap_scroll(widget: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _open_config_file(self) -> None:
        """用系统默认程序打开 config.yaml（记事本等）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..config.loader import find_config_path
        path = find_config_path()
        if path:
            logger.info("打开配置文件：%s", path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            logger.warning("未找到配置文件路径")

    def _on_reset_defaults(self) -> None:
        """还原默认设置：确认后模板覆盖 config.yaml，热加载即时生效。

        还原后关闭对话框（不保存）——表单里还挂着旧值，继续点「保存」
        会把旧配置写回去，白还原一场。
        """
        ret = self._themed_msg(
            t("set.reset.confirm_body"),
            buttons=(t("set.reset.btn_cancel"), t("set.reset.btn_ok")),
            default=t("set.reset.btn_cancel"), title=t("set.reset.title"))
        if ret != t("set.reset.btn_ok"):
            return
        from ..config.loader import reset_config_to_defaults

        try:
            reset_config_to_defaults()
        except Exception as exc:
            logger.warning("还原默认配置失败：%s", exc, exc_info=True)
            self._themed_msg(t("set.reset.err", error=str(exc)),
                             title=t("set.reset.title"))
            return
        self._themed_msg(t("set.reset.ok"), title=t("set.reset.title"))
        self.reject()

    def _build_log_card(self) -> QWidget:
        """关于页"日志与排障"折叠卡：保留天数 + 打开目录 + 清空日志。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..logging_setup import resolve_log_file

        card = AccordionCard(t("set.log.title"), t("set.log.desc"))
        lay = QVBoxLayout(card.body_container())
        lay.setSpacing(0)

        # 保留天数（下次启动生效）
        row_ret = QWidget()
        hl = QHBoxLayout(row_ret)
        hl.setContentsMargins(0, 8, 0, 12)
        hl.setSpacing(8)
        hl.addWidget(QLabel(t("set.log.retention")))
        self._retention_spin = QSpinBox()
        self._retention_spin.setRange(1, 365)
        self._retention_spin.setSuffix(t("set.unit.days"))
        try:
            self._retention_spin.setValue(
                int(getattr(self.cfg.logging, "retention_days", 30) or 30))
        except Exception:
            self._retention_spin.setValue(30)
        hl.addWidget(self._retention_spin)
        hint = QLabel(t("set.log.retention_hint"))
        hint.setObjectName("fieldDesc")
        hl.addWidget(hint)
        hl.addStretch(1)
        lay.addWidget(row_ret)

        sep = QFrame()
        sep.setObjectName("rowSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        lay.addWidget(sep)

        # 操作按钮
        row_btn = QWidget()
        bh = QHBoxLayout(row_btn)
        bh.setContentsMargins(0, 12, 0, 4)
        bh.setSpacing(8)
        btn_dir = QPushButton(t("set.log.open_dir"))
        btn_dir.setToolTip(t("set.log.open_dir_tip"))
        btn_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(resolve_log_file(self.cfg).parent))))
        btn_clear = QPushButton(t("set.log.clear"))
        btn_clear.setToolTip(t("set.log.clear_tip"))
        btn_clear.clicked.connect(self._clear_logs)
        bh.addWidget(btn_dir)
        bh.addWidget(btn_clear)
        bh.addStretch(1)
        lay.addWidget(row_btn)
        return card

    def _clear_logs(self) -> None:
        """清空全部日志文件（运行中安全调用，随后重建空日志）。"""
        from ..logging_setup import clear_all_logs

        try:
            clear_all_logs(self.cfg)
            self._themed_msg(t("set.log.cleared"), title=t("set.log.cleared_title"))
        except Exception as exc:
            logger.warning("清空日志失败：%s", exc, exc_info=True)
            self._themed_msg(t("set.log.clear_err", error=str(exc)),
                             title=t("set.log.clear_err_title"))

    # ---- 构建扁平分区 ----

    @staticmethod
    def _apply_control_width(w: QWidget) -> None:
        """按控件类型定宽（参考图：控件紧凑右对齐、宽度与内容匹配）。

        ThemeCardGroup 不定宽——它在 _make_row 里独占整行通栏铺开。
        """
        if isinstance(w, DeviceCombo):
            w.setMinimumWidth(280)
        elif isinstance(w, HotkeyCaptureEdit):
            w.setFixedWidth(200)
        elif isinstance(w, QLineEdit):
            w.setMinimumWidth(280)
        elif isinstance(w, QComboBox):
            w.setMinimumWidth(190)
        elif isinstance(w, SliderSpinBox):
            w.setMinimumWidth(300)
        elif isinstance(getattr(w, "_asr_value_widget", None), QTextEdit):
            w.setMinimumWidth(430)

    def _make_row(self, field) -> Optional[QWidget]:
        """构建一个字段行：标签+说明在左，控件右对齐。

        字段七元组里的 label/desc/tip 是**导入期求值**的 i18n 键，渲染时才 t()；
        _widgets 以 (section, key) 为键，与语言无关。
        """
        section, key, label_key, wtype, extra, desc_key, tip_key = field
        widget = self._make_widget(section, key, label_key, wtype, extra)
        if widget is None:
            return None
        # 组合控件（如 Prompt 编辑器）经 _asr_value_widget 指定保存时读取的内层控件
        self._widgets[(section, key)] = getattr(widget, "_asr_value_widget", widget)
        if tip_key:
            widget.setToolTip(t(tip_key))
            widget.setToolTipDuration(10000)
        self._apply_control_width(widget)

        def _label_block() -> QVBoxLayout:
            left = QVBoxLayout()
            left.setSpacing(2)
            lbl = QLabel(t(label_key))
            lbl.setObjectName("fieldLabel")
            left.addWidget(lbl)
            if desc_key:
                d = QLabel(t(desc_key))
                d.setObjectName("fieldDesc")
                d.setWordWrap(True)
                d.setTextInteractionFlags(Qt.TextSelectableByMouse)
                # 水平尺寸交给布局分配：忽略"单行全长"的宽度提示，说明文字
                # 才会在可用宽度内自动换行完整显示，而不是把行/页面撑出
                # 横向滚动（长说明被裁切的原因）
                d.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                left.addWidget(d)
            return left

        # 主题卡片（参考图「外观」）：不占右侧控件位，标签在上、三张卡片通栏
        if isinstance(widget, ThemeCardGroup):
            row = QWidget()
            vv = QVBoxLayout(row)
            vv.setContentsMargins(0, 12, 0, 14)
            vv.setSpacing(8)
            vv.addLayout(_label_block())
            vv.addWidget(widget)
            return row

        row = QWidget()
        g = QGridLayout(row)
        g.setContentsMargins(0, 12, 0, 12)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(0)
        g.setColumnStretch(0, 1)
        g.addLayout(_label_block(), 0, 0)
        # 多行编辑类控件（Prompt 编辑器）：顶部对齐；其余垂直居中
        tall = isinstance(getattr(widget, "_asr_value_widget", widget), QTextEdit)
        w_align = Qt.AlignRight | (Qt.AlignTop if tall else Qt.AlignVCenter)
        g.addWidget(widget, 0, 1, w_align)
        # 悬浮条不透明度：拖动滑块实时预览（不写文件，点保存才落盘）
        if (section, key) == ("ui", "bar_opacity") and isinstance(widget, SliderSpinBox):
            inner = widget.findChild(FlatSlider)
            if inner is not None:
                # valueChanged 覆盖键盘+鼠标+代码赋值，sliderMoved 仅鼠标拖动
                inner.valueChanged.connect(
                    lambda v: self.bar_opacity_preview.emit(int(v))
                )
        return row

    def _build_group(self, title: str, fields: list) -> QWidget:
        """构建扁平分区：纯文字分区标题 + 行清单 + 行间细分割线。

        fields 项为七元组，也可混入 _sec() 的小节标题行（wtype="header"）
        或已构建的 QWidget（直接作为一行插入并参与行间分割线排版）。
        """
        sec = QWidget()
        v = QVBoxLayout(sec)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        if title:
            st = QLabel(t(title))
            st.setObjectName("sectionTitle")
            v.addWidget(st)
        count = 0
        for field in fields:
            if isinstance(field, QWidget):
                if count:
                    sep = QFrame()
                    sep.setObjectName("rowSep")
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setFixedHeight(1)
                    v.addWidget(sep)
                v.addWidget(field)
                count += 1
                continue
            if field[3] == "header":
                h = QLabel(t(field[2]))
                h.setObjectName("cardSection")
                v.addWidget(h)
                continue
            row = self._make_row(field)
            if row is None:
                continue
            if count:
                sep = QFrame()
                sep.setObjectName("rowSep")
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFixedHeight(1)
                v.addWidget(sep)
            v.addWidget(row)
            count += 1
        return sec

    def _build_accordion_group(self, title: str, desc: str, fields: list,
                               expanded: bool = False) -> QWidget:
        """构建可展开卡片：头部标题+说明+箭头，点击展开字段行清单。

        fields 允许混入已构建的 QWidget（如连通性检测按钮行），同样走行间分割线排版。
        """
        card = AccordionCard(title, desc, expanded)
        body = card.body_container()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 2, 0, 8)
        v.setSpacing(0)
        count = 0
        for field in fields:
            if isinstance(field, QWidget):
                row = field
            elif field[3] == "header":
                h = QLabel(t(field[2]))
                h.setObjectName("cardSection")
                v.addWidget(h)
                continue
            else:
                row = self._make_row(field)
                if row is None:
                    continue
            sep = QFrame()
            sep.setObjectName("rowSep")
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFixedHeight(1)
            v.addWidget(sep)
            v.addWidget(row)
            count += 1
        return card

    def _build_funasr_frozen_card(self) -> QWidget:
        """打包版 FunASR 参数占位卡：本地引擎依赖未打包，仅说明不可用。"""
        card = AccordionCard(t("set.funasr.frozen_title"),
                             t("set.funasr.frozen_desc"), False)
        v = QVBoxLayout(card.body_container())
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(8)
        v.addWidget(self._about_text(t("set.funasr.frozen_body")))
        return card

    # 引擎提供商统一短名（引擎密钥页 / 引擎参数页 / 右键菜单共用）。
    # 类属性在导入期求值——只存 i18n 键，渲染点 t()。
    _ENGINE_PROVIDER_TITLES = {
        "tencent": "engine.provider.tencent",
        "aliyun": "engine.provider.aliyun",
        "xfyun": "engine.provider.xfyun",
        "volcengine": "engine.provider.volcengine",
        "funasr": "engine.provider.funasr",
    }

    # 折叠卡说明（引擎密钥页 / 引擎参数页，参考图卡片副标题风格）
    _ENGINE_KEY_DESCS = {
        "tencent": "set.engine.key_desc.tencent",
        "aliyun": "set.engine.key_desc.aliyun",
        "xfyun": "set.engine.key_desc.xfyun",
        "volcengine": "set.engine.key_desc.volcengine",
    }
    _ENGINE_PARAM_DESCS = {
        "tencent": "set.engine.param_desc.tencent",
        "aliyun": "set.engine.param_desc.aliyun",
        "xfyun": "set.engine.param_desc.xfyun",
        "volcengine": "set.engine.param_desc.volcengine",
        "funasr": "set.funasr.chinese_only",
    }

    def _engine_param_desc_key(self, provider: str) -> str:
        """引擎参数卡的说明词条键（funasr 按可用性分叉）。

        外部服务不可用时无云端引擎入口，"英文请使用云端引擎"这句不成立，故换一份纯事实文案。
        """
        if provider == "funasr" and not external_services_allowed():
            return "set.funasr.chinese_only_offline"
        return self._ENGINE_PARAM_DESCS.get(provider, "")

    def _ordered_providers(self) -> list:
        """按 ui.engine_order 排序的提供商列表（与右键菜单一致）。

        order 缺失/非法项回退默认顺序；始终返回全部 5 个提供商
        （去重后），保证设置页分组完整不因配置损坏而缺卡。"""
        raw = str(getattr(getattr(self.cfg, "ui", None), "engine_order", "") or "")
        custom = [p.strip() for p in raw.split(",") if p.strip()]
        default_order = ["aliyun", "volcengine", "xfyun", "tencent", "funasr"]
        ordered, seen = [], set()
        for p in custom + [d for d in default_order if d not in custom]:
            if p in self._ENGINE_PROVIDER_TITLES and p not in seen:
                ordered.append(p)
                seen.add(p)
        return ordered

    def _build_engine_order_editor(self) -> QWidget:
        """识别引擎排序编辑器：苹果「分组列表」风（大圆角组 + inset 发丝分隔线）。

        上下按钮调整 provider 分组顺序：后台线程写 ui.engine_order（避免 IO 阻塞）并就地
        更新 self.cfg.ui.engine_order；展开区各模型行只控制显隐（ui.engine_hidden_models）。
        """
        order_str = getattr(self.cfg.ui, "engine_order", "") or ""
        order = [p.strip() for p in str(order_str).split(",") if p.strip()]
        # 补全配置中缺失的 provider（保留已列顺序，缺失项追加在末尾）
        for p in self._ENGINE_PROVIDER_TITLES:
            if p not in order:
                order.append(p)

        box = QWidget()
        vlay = QVBoxLayout(box)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 12, 0, 6)
        hl.setSpacing(8)
        st = QLabel(t("set.engine.order_title"))
        st.setObjectName("sectionTitle")
        hl.addWidget(st)
        hl.addStretch(1)
        vlay.addWidget(head)

        # 大圆角组容器：所有厂商行块 + inset 分隔线住在里面（iOS 分组语言）
        group = QWidget()
        group.setObjectName("engineOrderGroup")
        group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        gl = QVBoxLayout(group)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(0)
        vlay.addWidget(group)
        # 重排（_reorder_engine_rows）操作的是组布局
        self._engine_order_box = group

        # 组下脚注：iOS 设置 section footer 的灰色小字
        footer = QLabel(t("set.engine.order_footer"))
        footer.setObjectName("eoFooter")
        footer.setWordWrap(True)
        fwrap = QWidget()
        fw = QVBoxLayout(fwrap)
        fw.setContentsMargins(14, 6, 0, 0)
        fw.setSpacing(0)
        fw.addWidget(footer)
        vlay.addWidget(fwrap)

        # 并行存 [provider 键, 行块 widget]；移动时交换并按新顺序重排布局
        current_group = engine_group_of(
            str(getattr(self.cfg, "engine", "") or ""))
        self._engine_order_rows: list[list] = []
        for p in order:
            row = self._make_engine_provider_entry(
                p, expanded=(p == current_group),
                badge=(t("set.engine.badge_current") if p == current_group else ""))
            gl.addWidget(row)
            self._engine_order_rows.append([p, row])
        # 首行禁用 ▲、末行禁用 ▼ 并隐藏末行底部分隔线
        self._refresh_engine_steppers()
        return box

    def _make_engine_provider_entry(self, provider: str,
                                    expanded: bool = False,
                                    badge: str = "") -> QWidget:
        """单个厂商行块（EngineOrderRow，苹果分组列表风）。

        头行 = 厂商名/副标题 + 步进器 + 显隐开关；展开区为各模型行（仅名称 + 显隐开关）。
        总开关关闭时头行置灰、模型区整体禁用；显隐写入 ui.engine_hidden_models。
        """
        models = engines_by_group().get(provider, [])
        if provider in ENGINE_CATEGORIES:
            # 该厂商右键菜单只显示分类名（如 大模型2.0 / 大模型版 / 实时语音
            # 转写大模型 / 豆包大模型 2.0），此列表同步按分类展示；开关隐藏的
            # 是各分类当前配置调用的模型，与右键菜单过滤同键
            models = [(eid, title)
                      for _, title, eid in provider_menu_items(provider, self.cfg)]
            unit = t("set.engine.unit_categories")
            model_tip = t("set.engine.model_tip_categories")
        else:
            unit = t("set.engine.unit_models")
            model_tip = t("set.engine.model_tip_models")
        hidden = set(self._engine_hidden_models())
        n_hidden = sum(1 for eid, _ in models if eid in hidden)
        desc = f"{len(models)}{unit}" + (
            t("set.engine.hidden_suffix", n=n_hidden) if n_hidden else "")
        row = EngineOrderRow(t(self._ENGINE_PROVIDER_TITLES[provider]), desc,
                             expanded=expanded, badge=badge,
                             palette=self._palette)

        # ---- 头行右侧：排序步进器 + 显隐开关（开关最右，iOS 惯例）----
        # 显隐开关：关掉 = 该提供商引擎从右键"识别引擎"菜单整组隐藏。
        # 不配"显示/隐藏"状态字——开关形态本身已表意。先 setChecked 回显
        # 再 connect，避免初始化误写配置。
        show_on = bool(self._read_cfg("ui", f"engine_show_{provider}"))
        row.add_header_widget(row.make_stepper(
            lambda _checked, r=row: self._move_engine_row(r, -1),
            lambda _checked, r=row: self._move_engine_row(r, 1),
        ))
        toggle = ToggleSwitch(self._palette)
        toggle.setChecked(show_on)
        toggle.setToolTip(t("set.engine.provider_tip"))
        toggle.toggled.connect(
            lambda checked, p=provider: self._on_engine_show_toggled(p, checked)
        )
        # 隐藏时头行文字置灰（QSS: eoVendorName/Meta/Badge:disabled）
        toggle.toggled.connect(row.set_content_enabled)
        row.set_content_enabled(show_on)
        row.add_header_widget(toggle)

        # ---- 展开区：模型行（缩进对齐厂商文本，行间隔 inset 发丝线）----
        body = row.body_container()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)
        model_toggles: list[ToggleSwitch] = []

        def _refresh_desc() -> None:
            # 迭代变量取名 sw 而非 t：同一函数里的 t() 是 i18n 取词
            # 重名会让读者（和静态检查）误以为遮蔽；genexp 作用域虽不真遮蔽
            # 但一旦日后改成显式 for 循环就会变成真遮蔽。
            n = sum(1 for sw in model_toggles if not sw.isChecked())
            row.set_desc(f"{len(models)}{unit}" + (
                t("set.engine.hidden_suffix", n=n) if n else ""))

        for i, (eid, label) in enumerate(models):
            if i:
                msep = QFrame()
                msep.setObjectName("eoSep")
                msep.setFrameShape(QFrame.Shape.HLine)
                msep.setFixedHeight(1)
                bv.addWidget(msep)
            mrow = QWidget()
            mh = QHBoxLayout(mrow)
            # 左缩进 36 与厂商文本对齐（14 边距 + 14 箭头 + 8 间距）
            mh.setContentsMargins(36, 5, 12, 5)
            mh.setSpacing(8)
            mlabel = QLabel(label)
            mlabel.setObjectName("eoModelName")
            mh.addWidget(mlabel)

            m_on = eid not in hidden
            mtoggle = ToggleSwitch(self._palette)
            mtoggle.setChecked(m_on)
            mtoggle.setToolTip(model_tip)
            mtoggle.toggled.connect(
                lambda checked, e=eid: self._on_engine_model_toggled(e, checked)
            )
            mtoggle.toggled.connect(
                lambda checked, lbl=mlabel: lbl.setEnabled(checked))
            mlabel.setEnabled(m_on)
            model_toggles.append(mtoggle)
            mtoggle.toggled.connect(_refresh_desc)
            mh.addStretch(1)
            mh.addWidget(mtoggle)
            bv.addWidget(mrow)

        # 厂商总开关关闭：模型区整体禁用（整组已隐藏，逐模型开关无意义）
        body.setEnabled(show_on)
        toggle.toggled.connect(body.setEnabled)
        return row

    def _refresh_engine_steppers(self) -> None:
        """刷新各行块：首行禁用 ▲、末行禁用 ▼；末行隐藏底部 inset 分隔线。"""
        rows = self._engine_order_rows
        n = len(rows)
        for i, (_, row) in enumerate(rows):
            row.set_step_enabled(i > 0, i < n - 1)
            row.set_trailing_sep_visible(i < n - 1)

    def _engine_hidden_models(self) -> list:
        """ui.engine_hidden_models 解析为隐藏引擎 ID 列表（保持书写顺序）。"""
        raw = str(getattr(getattr(self.cfg, "ui", None),
                          "engine_hidden_models", "") or "")
        return [e.strip() for e in raw.split(",") if e.strip()]

    def _on_engine_model_toggled(self, engine_id: str, show: bool) -> None:
        """引擎模型"显示"开关：更新 ui.engine_hidden_models 并后台写盘。

        与厂商显隐开关同链路：就地更新内存 cfg（悬浮条下次建菜单即生效），
        文件 IO 放后台线程避免卡 UI，写完由 QFileSystemWatcher 热加载兜底。
        """
        ids = self._engine_hidden_models()
        if show:
            ids = [e for e in ids if e != engine_id]
        elif engine_id not in ids:
            ids.append(engine_id)
        value = ",".join(ids)
        try:
            self.cfg.ui.engine_hidden_models = value
        except AttributeError:
            logger.debug("同步内存 engine_hidden_models 失败（cfg.ui 缺失）",
                         exc_info=True)
        import threading
        threading.Thread(
            target=self._write_engine_hidden_models, args=(value,),
            daemon=True, name=f"engine-models-{engine_id}",
        ).start()

    @staticmethod
    def _write_engine_hidden_models(value: str) -> None:
        from ..config.loader import update_config_field

        if update_config_field("engine_hidden_models", value,
                               section="ui", value_type="str"):
            logger.info("引擎模型显隐已更新：%s", value or "（全部显示）")

    def _move_engine_row(self, row: QWidget, delta: int) -> None:
        """把指定行上下移动一格：交换并行列表项 + 按新顺序重排布局 + 持久化。"""
        rows = self._engine_order_rows
        idx = next((i for i, (_, r) in enumerate(rows) if r is row), -1)
        if idx < 0:
            return
        new_idx = idx + delta
        if not (0 <= new_idx < len(rows)):
            return
        rows[idx], rows[new_idx] = rows[new_idx], rows[idx]
        self._reorder_engine_rows()
        # 换位后首/末卡变了，同步刷新步进器可用态
        self._refresh_engine_steppers()
        order_str = ",".join(k for k, _ in rows)
        # 就地更新内存 cfg（悬浮条下次建菜单即生效）
        try:
            self.cfg.ui.engine_order = order_str
        except AttributeError:
            logger.debug("同步内存 engine_order 失败（cfg.ui 缺失）", exc_info=True)
        # 后台线程写文件持久化（避免 IO 阻塞 UI）
        import threading
        threading.Thread(
            target=self._write_engine_order, args=(order_str,),
            daemon=True, name="engine-order-write",
        ).start()

    def _reorder_engine_rows(self) -> None:
        """按 self._engine_order_rows 当前顺序，把行 widget 从布局中移除再依序插回。"""
        vlay = self._engine_order_box.layout()
        for _, r in self._engine_order_rows:
            vlay.removeWidget(r)
        for _, r in self._engine_order_rows:
            vlay.addWidget(r)

    @staticmethod
    def _write_engine_order(order_str: str) -> None:
        from ..config.loader import update_config_field

        if update_config_field("engine_order", order_str,
                               section="ui", value_type="str"):
            logger.info("识别引擎排序已更新：%s", order_str)

    def _on_engine_show_toggled(self, provider: str, checked: bool) -> None:
        """引擎提供商"显示"开关：后台线程写 config.yaml，热加载即时刷新右键菜单。

        文件 IO 在杀软扫描/云同步盘介入时可达数秒，同步执行会卡死 UI 线程。
        """
        import threading

        threading.Thread(
            target=self._write_engine_show,
            args=(provider, checked),
            daemon=True,
            name=f"engine-show-{provider}",
        ).start()

    @staticmethod
    def _write_engine_show(provider: str, checked: bool) -> None:
        from ..config.loader import update_config_field

        ok = update_config_field(
            f"engine_show_{provider}",
            "true" if checked else "false",
            section="ui",
            value_type="bool",
        )
        if ok:
            logger.info("引擎提供商显隐已切换：%s -> %s", provider, checked)
        else:
            logger.warning("写入 engine_show_%s 失败：未找到配置文件", provider)

    # ---- Prompt 版本编辑器 ----

    def _prompt_temp_item(self) -> str:
        """「（未保存）」这个伪版本名。原为类属性 _PROMPT_TEMP_ITEM，
        A6 起改为方法：类属性在导入期求值，写 t() 会把当时语言焊死。"""
        return t("set.prompt.temp_item")

    def _build_prompt_editor(self, current: str) -> QWidget:
        """AI 修正 Prompt 编辑器：版本下拉 + 多行编辑 + 新增/保存/删除。

        下拉选中版本即载入其内容（编辑期间不自动切换选中）；保存把编辑框内容写入
        选中的版本（内置"内置方案"除外）；运行时生效的仍是 llm.prompt（点「保存」才落盘）。
        """
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        combo = QComboBox()
        btn_add = QPushButton(t("set.prompt.add"))
        btn_save = QPushButton(t("set.prompt.save"))
        btn_del = QPushButton(t("set.prompt.delete"))
        row.addWidget(combo, 1)
        row.addWidget(btn_add)
        row.addWidget(btn_save)
        row.addWidget(btn_del)
        v.addLayout(row)

        edit = QTextEdit()
        edit.setFixedHeight(120)
        edit.setPlaceholderText(t("set.prompt.placeholder"))
        _install_text_menu(edit, self._workshop_palette())
        v.addWidget(edit)

        self._prompt_combo = combo
        self._prompt_edit = edit
        self._prompt_del_btn = btn_del
        # _make_row 注册用：保存时读取的控件是内层多行编辑框
        container._asr_value_widget = edit

        self._refresh_prompt_combo(current)

        combo.currentIndexChanged.connect(self._on_prompt_version_changed)
        btn_add.clicked.connect(self._on_prompt_add)
        btn_save.clicked.connect(self._on_prompt_save_current)
        btn_del.clicked.connect(self._on_prompt_delete)
        return container

    def _build_admin_launch_row(self) -> QWidget:
        """通用设置页置顶：永久提权动作行（全宽动作按钮 + 灰色说明/状态）。

        点击立即写配置（不等「保存」）：开启时创建"以最高权限运行"计划任务，
        首次需 UAC 确认一次，此后开机/手动启动都由任务管理员拉起、不再弹 UAC。
        """
        from ..core.elevation import (
            create_admin_task,
            delete_admin_task,
            is_elevated,
            relaunch_elevated,
        )
        from ..config.loader import update_config_field

        self._always_admin = bool(getattr(self.cfg.ui, "always_admin", False))
        self._currently_elevated = is_elevated()

        row = QWidget()
        v = QVBoxLayout(row)
        v.setContentsMargins(0, 12, 0, 12)
        v.setSpacing(8)

        # 全宽动作按钮：未开启蓝（btnWide）/ 已开启绿（btnWideGreen）
        # _refresh 里切 objectName 后 re-polish 使新样式生效；
        # 计划任务机制细节放 tooltip（原先挂在字段标签上，标签已去掉）
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        # UAC 盾牌图标置于按钮文字左侧（Windows 提权按钮惯例）
        # 蓝/绿两态背景上蓝黄盾牌均清晰，无需随态切换
        btn.setIcon(uac_shield_icon())
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(t("set.admin.tip"))
        btn.setToolTipDuration(10000)
        v.addWidget(btn)

        # 文案刻意回避「标」字：用户环境特定 DPI 下该字形光栅化出黑点
        # 调整落位无效（见提权行上轮改动），直接换掉该字符最稳
        desc = QLabel(
            t("set.admin.desc")
            + (t("set.admin.desc_elevated") if self._currently_elevated else "")
        )
        desc.setObjectName("fieldDesc")
        desc.setWordWrap(True)
        v.addWidget(desc)

        status = QLabel()
        status.setObjectName("fieldDesc")
        status.setWordWrap(True)
        # 默认隐藏：开/关状态由按钮颜色+文案自表达，这里只承接点击后的操作反馈；
        # QLabel 空文本仍占行高，隐藏才能省掉空白行（参考 _build_llm_test_row）
        status.setVisible(False)
        v.addWidget(status)

        def _refresh() -> None:
            if self._always_admin:
                btn.setText(t("set.admin.disable"))
                btn.setObjectName("btnWideGreen")
            else:
                btn.setText(t("set.admin.enable"))
                btn.setObjectName("btnWide")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        def _on_toggle() -> None:
            enable = not self._always_admin
            if not update_config_field(
                "always_admin", "true" if enable else "false",
                section="ui", value_type="bool",
            ):
                status.setText(t("set.admin.err_write"))
                status.setVisible(True)
                return
            # 直接同步内存 cfg，避免等热加载
            try:
                self.cfg.ui.always_admin = enable
            except Exception:
                logger.debug("同步内存配置 ui.always_admin 失败，忽略", exc_info=True)
            self._always_admin = enable

            if not enable:
                _refresh()
                task_ok, task_msg = delete_admin_task()
                if task_ok:
                    status.setText(t("set.admin.off_ok"))
                else:
                    status.setText(t("set.admin.off_err", error=task_msg))
                status.setVisible(True)
                return
            if self._currently_elevated:
                # 已是管理员：直接创建计划任务，无需重启
                task_ok, task_msg = create_admin_task()
                _refresh()
                if task_ok:
                    status.setText(t("set.admin.on_ok"))
                else:
                    status.setText(t("set.admin.on_err", error=task_msg))
                status.setVisible(True)
                return
            # 未提权：UAC 确认一次后重启接管，提权实例启动时自动创建任务
            ok, msg = relaunch_elevated()
            if ok:
                self.restart_requested.emit()
                return
            # 提权没成立（用户点了「否」/ 其它失败）≠ 已启用：把上面刚写下去的
            # 配置回滚。留一个 true 会让按钮显示"已开启"，而且每次启动都会再弹
            # 一次 UAC——用户明明已经拒绝过，还要被反复问。想启用再点一次即可。
            if not update_config_field(
                "always_admin", "false", section="ui", value_type="bool",
            ):
                logger.warning("回滚 ui.always_admin 失败，配置可能仍为 true")
            try:
                self.cfg.ui.always_admin = False
            except Exception:
                logger.debug("回滚内存配置 ui.always_admin 失败，忽略",
                             exc_info=True)
            self._always_admin = False
            _refresh()
            status.setText(t("set.admin.on_cancelled"))
            status.setVisible(True)
            logger.info("管理员启动未启用，已回滚开关：%s", msg)

        btn.clicked.connect(_on_toggle)
        _refresh()
        return row

    def _steam_page_groups(self, pick) -> list:
        """渠道发行分组（仅发行构建渲染）。

        `pick` 是 __init__ 内的闭包（按 _FIELDS 过滤），故由调用处传进来。
        源码构建返回空列表，通用设置页与以前一模一样。
        """
        from ..steam_integration import (
            STEAM_DLC_GPU_APP_ID,
            get_integration,
            is_steam_build,
            nvidia_gpu_present,
        )

        if not is_steam_build():
            return []
        # 「云存档同步密钥」开关。渲染条件（满足其一）：external_services_allowed()
        # （源码构建 / 发行构建装了工坊内容包），或配置里开关已是 True（绝不把打开过的
        # 用户锁在外面，能看见也能关掉）。默认 False＝密钥默认不进云。真双向同步：
        # 开关决定密钥随云存档上传，恢复时云上那份含密钥文件存在与否决定是否下载回来。
        _keys_already_on = bool(getattr(
            getattr(self.cfg, "steam", None), "cloud_sync_keys", False))
        if external_services_allowed() or _keys_already_on:
            rows = list(pick("steam", ("cloud_sync_keys",)))
        else:
            rows = []
        if _gpu_switch_available():
            rows = rows + pick("steam", ("use_gpu",))
        # 有 N 卡但没装扩展包：补一个安装入口（配置里也提示过一次，见 app.run）
        if STEAM_DLC_GPU_APP_ID and nvidia_gpu_present() and \
                not get_integration().dlc_installed(STEAM_DLC_GPU_APP_ID):
            rows.append(self._build_gpu_install_row())
        # 创意工坊作为独立一张卡（不走扁平分区）：它内部有列表 + 多按钮，塞进
        # _build_group 的字段流会被行间分割线排版拉坏。SDK 不可用时照样建卡，
        # 让用户看到"为什么没有工坊"而不是以为功能凭空消失。
        return [("set.steam.group", rows), self._build_workshop_card()]

    # ---- 创意工坊 ----
    def _build_workshop_card(self) -> QWidget:
        """「创意工坊」卡片（仅发行构建）。

        只做「查看订阅 + 单物品管理 + 上传本地目录」，不设强制项；
        阻塞调用（退订 / 上传）一律丢后台线程，否则会冻住整个设置界面。
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..steam_integration import get_integration

        integ = get_integration()

        card = QWidget()
        card.setObjectName("accordionCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)

        # 卡片自带标题：它是直接插进页面的 QWidget（不走 _build_group）
        # 不像其它分组由外面渲染标题，缺了就成了一张无名的卡
        title = QLabel(t("set.workshop.group"))
        title.setObjectName("sectionTitle")
        v.addWidget(title)

        intro = QLabel(t("set.workshop.intro"))
        intro.setObjectName("cardSection")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # 按钮行：浏览社区 / 发布本地目录 / 刷新
        tools = QHBoxLayout()
        tools.setSpacing(8)
        btn_browse = QPushButton(t("set.workshop.browse"))
        # 按钮行给出层级：「去社区看看」是这一页唯一的"往前一步"动作
        # 用与主按钮同族的实底（但仍是本卡专属 objectName，不动全局主按钮样式）
        btn_browse.setObjectName("workshopPrimary")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_workshop_community_url())))
        btn_refresh = QPushButton(t("set.workshop.refresh"))
        btn_refresh.setObjectName("btnGhost")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._refresh_workshop)
        btn_publish = QPushButton(t("set.workshop.publish"))
        btn_publish.setObjectName("btnGhost")
        btn_publish.setCursor(Qt.PointingHandCursor)
        btn_publish.setToolTip(t("set.workshop.publish_tip"))
        btn_publish.clicked.connect(self._on_workshop_publish)
        for b in (btn_browse, btn_publish, btn_refresh):
            tools.addWidget(b)
        tools.addStretch(1)
        v.addLayout(tools)

        # 状态标签：不可用 / 空 / 上传进度 三态共用
        self._workshop_status = QLabel()
        self._workshop_status.setWordWrap(True)
        self._workshop_status.setVisible(False)
        v.addWidget(self._workshop_status)

        # 物品列表。高度贴合内容（见 _fit_workshop_list_height）：原来固定
        # minHeight=120，只有一两个物品时下方留一大片空白，像"没加载完"。
        self._workshop_list = QListWidget()
        self._workshop_list.setObjectName("workshopList")
        self._workshop_list.setUniformItemSizes(True)
        self._workshop_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._workshop_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._workshop_list.itemDoubleClicked.connect(
            self._on_workshop_priority)
        # 右键菜单：在社区中查看 / 立即下载 / 退订（见 _on_workshop_menu）。
        # 不用行内按钮：行控件对鼠标透明（见 _WorkshopRow），点击/选中/双击
        # 全归 QListWidget，在透明行里塞按钮要另造一套事件转发，不值。
        self._workshop_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._workshop_list.customContextMenuRequested.connect(
            self._on_workshop_menu)
        v.addWidget(self._workshop_list)

        if not integ.workshop_available():
            # SDK 不可用（提权启动 / 没经 Steam 拉起）：内容照样能被文件系统
            # 来源发现并加载（见 workshop.py 顶部说明），只是订阅/下载/发布
            # 这些写操作做不了。所以列表要照常列出已装的包——直接隐藏会让人
            # 以为"工坊彻底废了"，而实际上模型/主题/字体/短语都在生效。
            self._refresh_workshop()
            btn_publish.setEnabled(False)
            btn_refresh.setEnabled(False)
        else:
            self._refresh_workshop()
            # 下载状态由 Steam 客户端在后台推进，启动一个轻量定时器
            # 让列表里的进度跟着走（否则数字会一直停在打开设置那一刻）
            self._workshop_timer = QTimer(self)
            self._workshop_timer.setInterval(1500)
            self._workshop_timer.timeout.connect(self._refresh_workshop)
            self._workshop_timer.start()
        self.workshop_done.connect(self._on_workshop_done)
        return card

    def _refresh_workshop(self) -> None:
        """重建物品列表：SDK 可用走 SDK 物品列表（有状态/进度），
        不可用（提权 / 非 Steam 拉起）退回文件系统发现，列出本机已装包。
        """
        from ..steam_integration import get_integration

        lst = getattr(self, "_workshop_list", None)
        if lst is None:
            return
        # 重建前记住选中的物品 ID：clear + addAll 会丢掉选中态
        # 用户每点一次刷新就得重新点一遍自己关心的那一行
        selected = _workshop_selected_id(lst)
        lst.clear()

        integ = get_integration()
        if integ.workshop_available():
            lst.setVisible(True)
            self._fill_workshop_list_from_sdk(lst, selected)
            return
        self._fill_workshop_list_from_filesystem(lst)

    def _fill_workshop_list_from_sdk(self, lst, selected: int) -> None:
        from ..steam_integration import get_integration

        items = get_integration().workshop_items()
        if not items:
            self._show_workshop_status(t("set.workshop.empty"))
            self._fit_workshop_list_height()
            return
        self._show_workshop_status("")
        palette = self._workshop_palette()
        for item in items:
            # item 自身的文本必须清空：QListView 会先画 item 文本、再把
            # setItemWidget 的行控件铺在它上面，而行控件是透明的 —— 留着文本
            # 就是两层字叠在一起（目检发现）。语义文本改挂 AccessibleTextRole
            # 读屏与 diag 脚本照旧拿得到，只是不再"画"出来。
            manifest = _workshop_installed_manifest(item)
            name = manifest.name if manifest is not None else ""
            entry = QListWidgetItem("")
            entry.setData(Qt.ItemDataRole.AccessibleTextRole,
                          _workshop_item_text(item, name))
            entry.setData(Qt.ItemDataRole.UserRole, item.item_id)
            entry.setToolTip(_workshop_item_tooltip(item))
            chip, chip_style, progress = _workshop_item_state(item)
            # 已安装的读本地清单显示包名 + 内容类型（与文件系统视图同一套
            # 文案）；未安装 / 无清单退回紧凑的 `#ID`。原来一律显示「ID 300…」：
            # 长 ID 把主标题占满、整列一模一样，用户分不清谁是谁。
            if manifest is not None:
                glyph, subtitle = _workshop_kind_bits(manifest)
                row = _WorkshopRow(
                    palette, glyph=glyph, title=manifest.name,
                    subtitle=subtitle, chip=chip, chip_style=chip_style,
                    progress=progress)
            else:
                row = _WorkshopRow(
                    palette, glyph="package", title=f"#{item.item_id}",
                    chip=chip, chip_style=chip_style, progress=progress)
            entry.setSizeHint(QSize(0, max(
                _WORKSHOP_ROW_FALLBACK_H, row.sizeHint().height())))
            lst.addItem(entry)
            lst.setItemWidget(entry, row)
            if item.item_id == selected:
                entry.setSelected(True)
        self._fit_workshop_list_height()

    def _fill_workshop_list_from_filesystem(self, lst) -> None:
        """SDK 不可用时的只读视图：列出文件系统发现到的内容包。"""
        from ..workshop import installed_manifests

        manifests = installed_manifests()
        if not manifests:
            # 一个包都没有：连列表一起藏掉。留着会是个刺眼的白框
            # ，而"不可用"提示本身已经说清了状况。
            lst.setVisible(False)
            self._show_workshop_status(t("set.workshop.unavailable"))
            self._fit_workshop_list_height()
            return
        # 有内容就显示（即使 SDK 挂了，这些包也确实在生效）。
        # "能用"与"能管"是两件事——不说清会让人以为是坏了。
        lst.setVisible(True)
        self._show_workshop_status(
            t("set.workshop.readonly", count=len(manifests)))
        palette = self._workshop_palette()
        for manifest in manifests:
            entry = QListWidgetItem("")
            entry.setData(Qt.ItemDataRole.AccessibleTextRole,
                          _workshop_pack_text(manifest))
            entry.setData(Qt.ItemDataRole.UserRole, 0)
            entry.setToolTip(_workshop_pack_tooltip(manifest))
            # 包名做主标题、内容类型做副标题（逻辑与 SDK 视图共用
            # _workshop_kind_bits，防两处漂移）
            glyph, subtitle = _workshop_kind_bits(manifest)
            row = _WorkshopRow(
                palette, glyph=glyph, title=manifest.name, subtitle=subtitle,
                chip=t("set.workshop.installed"),
                chip_style="workshopChipDone")
            entry.setSizeHint(QSize(0, max(
                _WORKSHOP_ROW_FALLBACK_H, row.sizeHint().height())))
            lst.addItem(entry)
            lst.setItemWidget(entry, row)
        self._fit_workshop_list_height()

    def _workshop_palette(self) -> dict:
        """取当前配色（自绘方块要用到颜色）。

        `_palette` 在 `__init__` 里就设好了；这里仍兜一层，因为 diag 脚本会
        用 `__new__` 造半个对话框来单独渲染这张卡。
        """
        pal = getattr(self, "_palette", None)
        if not pal:
            pal = _theme_palette(getattr(self, "cfg", None))
            self._palette = pal
        return pal

    def _fit_workshop_list_height(self) -> None:
        """列表高度贴合内容（最多 `_WORKSHOP_LIST_MAX_ROWS` 行，更多交给滚动条）。
        """
        lst = getattr(self, "_workshop_list", None)
        if lst is None:
            return
        rows = lst.count()
        # 用 isHidden() 而不是 isVisible()：构建期父窗口还没显示，isVisible()
        # 恒为 False，会把高度压成 0。isHidden() 只看"是否被显式隐藏"。
        if rows == 0 or lst.isHidden():
            lst.setFixedHeight(0)
            return
        per = lst.sizeHintForRow(0)
        if per <= 0:
            per = _WORKSHOP_ROW_FALLBACK_H
        # 2px 是 item 的上下外边距（见 QSS `#workshopList::item`）
        # 12px 是列表自己的 padding + 边框，不留出来最后一行会被裁
        lst.setFixedHeight(
            min(rows, _WORKSHOP_LIST_MAX_ROWS) * (per + 2) + 12)

    def _on_workshop_priority(self, item) -> None:
        """把某个物品的下载提到最高优先级（双击列表项触发）。"""
        if not isinstance(item, QListWidgetItem):
            return
        item_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if not item_id:
            return
        self._request_workshop_download(item_id)

    def _request_workshop_download(self, item_id: int) -> None:
        """请求把物品的下载提到最高优先级（双击与右键菜单共用）。"""
        from ..steam_integration import get_integration

        get_integration().workshop_download(int(item_id))
        self._refresh_workshop()

    def _on_workshop_menu(self, pos) -> None:
        """物品行右键菜单：在社区中查看 / 立即下载 / 退订。"""
        lst = getattr(self, "_workshop_list", None)
        if lst is None:
            return
        entry = lst.itemAt(pos)
        if entry is None:
            return
        item_id = int(entry.data(Qt.ItemDataRole.UserRole) or 0)
        if not item_id:
            # 文件系统视图（只读模式）：行携带 0，没有可管理的物品 ID
            return
        lst.setCurrentItem(entry)
        self._build_workshop_item_menu(item_id).exec(
            lst.viewport().mapToGlobal(pos))

    def _build_workshop_item_menu(self, item_id: int):
        """构建物品右键菜单（与 exec 拆开：测试可直接检查而不阻塞）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMenu

        palette = self._workshop_palette()
        menu = QMenu(self)
        # 样式照气泡右键菜单的轻量做法（bar_widgets._show_menu）：只染
        # 表面 / 文字 / 描边 / 选中底色，不引入完整的悬浮条菜单体系
        menu.setStyleSheet(
            "QMenu{background:%s;color:%s;border:1px solid %s;padding:4px;}"
            "QMenu::item{padding:5px 18px;border-radius:4px;}"
            "QMenu::item:selected{background:%s;}"
            % (palette["panel"], palette["text"], palette["border"],
               palette["combo_sel"]))
        act_view = menu.addAction(t("set.workshop.view_community"))
        act_view.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(f"steam://url/CommunityFilePage/{int(item_id)}")))
        act_download = menu.addAction(t("set.workshop.priority_now"))
        act_download.triggered.connect(
            lambda: self._request_workshop_download(int(item_id)))
        # 退订是破坏性操作，与上面两项隔开
        menu.addSeparator()
        act_unsub = menu.addAction(t("set.workshop.unsubscribe"))
        act_unsub.triggered.connect(
            lambda: self._on_workshop_unsubscribe(int(item_id)))
        return menu

    def _on_workshop_unsubscribe(self, item_id: int) -> None:
        """退订物品：先确认，再丢后台线程执行（取消订阅后 Steam 移除本地内容）。

        确认框不能省（误点代价是重新订阅 + 重新下载），默认按钮放「否」；
        阻塞调用必须在后台线程等结果，经 workshop_done 回主线程刷新与提示。
        """
        if not item_id:
            return
        answer = QMessageBox.question(
            self, t("set.workshop.unsubscribe"),
            t("set.workshop.unsubscribe_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        import threading

        threading.Thread(
            target=self._unsubscribe_worker, args=(int(item_id),),
            daemon=True, name="workshop-unsubscribe").start()

    def _unsubscribe_worker(self, item_id: int) -> None:
        """后台线程：执行退订并把结果发回主线程（不碰任何控件）。"""
        from ..steam_integration import get_integration

        ok = bool(get_integration().workshop_unsubscribe(int(item_id)))
        self.workshop_done.emit(ok, "unsubscribe", str(item_id))

    def _on_workshop_publish(self) -> None:
        """选目录 → 后台线程上传。"""
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from ..steam_integration import get_integration
        from ..workshop import MANIFEST_NAME, parse_manifest

        folder = QFileDialog.getExistingDirectory(
            self, t("set.workshop.select_folder"))
        if not folder:
            return
        # 先校验清单：缺清单/无有效内容的目录即使上传成功
        # 应用也永远不会认它 —— "发布成功但没有任何效果"是最难解释的一种结果
        manifest = parse_manifest(Path(folder))
        if manifest is None:
            QMessageBox.warning(
                self, t("set.workshop.group"),
                t("set.workshop.folder_missing_manifest"))
            return

        self._show_workshop_status(t("set.workshop.publishing", percent=0))
        title = manifest.name
        # 物品页描述来自清单：发布方在 ori_workshop.json 里写什么，工坊
        # 页面就展示什么——功能包尤其需要，页面上必须能看到"需自备密钥"
        # 这类说明（口径与 README、应用内文案保持一致）
        description = manifest.description

        def _worker() -> None:
            item_id = get_integration().workshop_publish(
                folder, title=title, description=description,
                tags=["OpenRealtimeASR"], change_note=title)
            self.workshop_done.emit(bool(item_id), "publish", title)

        import threading
        threading.Thread(target=_worker, daemon=True,
                         name="workshop-publish").start()

    def _on_workshop_done(self, ok: bool, action: str, detail: str) -> None:
        """工坊后台动作完成（发布 / 退订）：刷新列表并按动作提示结果。"""
        self._refresh_workshop()
        if action == "unsubscribe":
            self._show_workshop_status(
                t("set.workshop.unsubscribed") if ok
                else t("set.workshop.unsubscribe_failed"))
            return
        self._show_workshop_status(
            t("set.workshop.published", title=detail) if ok
            else t("set.workshop.publish_failed"))

    def _show_workshop_status(self, text: str) -> None:
        label = getattr(self, "_workshop_status", None)
        if label is None:
            return
        label.setText(text or "")
        label.setVisible(bool(text))

    def _build_gpu_install_row(self) -> QWidget:
        """「安装显卡加速包」按钮行：交给 平台客户端弹安装对话框。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..steam_integration import STEAM_DLC_GPU_APP_ID

        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 2, 0, 0)
        h.setSpacing(10)
        btn = QPushButton(t("set.steam.gpu_install"))
        btn.setObjectName("btnGhost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"steam://install/{STEAM_DLC_GPU_APP_ID}")))
        h.addWidget(btn)
        h.addStretch(1)
        return wrap

    def _build_llm_test_row(self) -> QWidget:
        """「引擎密钥 → 通用 AI 接口」卡内：连通性检测按钮行（读表单当前值发最小请求）。

        按钮发后台线程请求（不卡 UI），结果经 llm_test_done 信号回主线程，
        状态标签即时反馈：灰=检测中 / 绿=成功 / 红=失败（附原因）。
        """
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 2, 0, 0)
        h.setSpacing(10)

        btn = QPushButton(t("set.llm_test.btn"))
        btn.setObjectName("btnGhost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(t("set.llm_test.btn_tip"))
        status = QLabel()
        status.setWordWrap(True)
        status.setVisible(False)
        h.addWidget(btn)
        h.addWidget(status, 1)

        self._llm_test_btn = btn
        self._llm_test_status = status
        self.llm_test_done.connect(self._on_llm_test_done)
        btn.clicked.connect(self._on_llm_test)
        return wrap

    def _llm_field(self, key: str) -> str:
        """读 AI 修正表单当前值（未保存的编辑也生效）。"""
        w = self._widgets.get(("llm", key))
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ""

    def _on_llm_test(self) -> None:
        """发起连通性检测：先校验必填项，再起后台线程执行。"""
        base_url = self._llm_field("base_url")
        api_key = self._llm_field("api_key")
        model = self._llm_field("model")
        if not base_url:
            self._show_llm_test_result(False, t("set.llm_test.need_base_url"))
            return
        if not model:
            self._show_llm_test_result(False, t("set.llm_test.need_model"))
            return
        if not api_key:
            self._show_llm_test_result(False, t("set.llm_test.need_api_key"))
            return

        think_w = self._widgets.get(("llm", "disable_thinking"))
        disable_thinking = (think_w.isChecked()
                            if isinstance(think_w, QCheckBox) else True)
        timeout_w = self._widgets.get(("llm", "timeout_ms"))
        form_timeout = (int(timeout_w.value())
                        if isinstance(timeout_w, SliderSpinBox) else 3000)
        # 检测超时放宽到至少 8 秒（首次建连可能慢；修正链路仍用表单值）
        timeout_ms = max(form_timeout, 8000)

        self._show_llm_test_result(None, t("set.llm_test.testing"))
        import threading
        threading.Thread(
            target=self._llm_test_worker,
            args=(base_url, api_key, model, disable_thinking, timeout_ms),
            daemon=True, name="llm-conn-test",
        ).start()

    def _llm_test_worker(self, base_url: str, api_key: str, model: str,
                         disable_thinking: bool, timeout_ms: int) -> None:
        """后台线程：执行最小化 chat 请求并回发结果。"""
        from ..postprocess.openai_client import check_connectivity

        ok, msg = check_connectivity(base_url, api_key, model,
                                     disable_thinking, timeout_ms)
        try:
            self.llm_test_done.emit(ok, msg)
        except RuntimeError:
            pass  # 对话框已销毁，静默丢弃

    def _on_llm_test_done(self, ok: bool, msg: str) -> None:
        """检测完成（主线程）：渲染结果（成功只报连通，不展示模型回复）。"""
        if ok:
            self._show_llm_test_result(True, t("set.llm_test.ok"))
        else:
            self._show_llm_test_result(False, msg)

    def _show_llm_test_result(self, ok: Optional[bool], text: str) -> None:
        """更新检测状态标签：None=检测中（灰），True=成功（绿），False=失败（红）。"""
        btn, status = self._llm_test_btn, self._llm_test_status
        if ok is None:
            btn.setEnabled(False)
            color = self._palette.get("secondary", "#9d9da5")
        else:
            btn.setEnabled(True)
            color = "#2e9e5b" if ok else "#d9534f"
        status.setText(text)
        status.setStyleSheet(f"color: {color};")
        status.setVisible(True)

    # ---- 语音对话：provider 专属字段区（豆包 / Qwen / Hermes 互斥显隐）----

    def _build_provider_container(self, groups: list) -> QWidget:
        """把 N 个 ("标题", fields) 分区竖排进一个可整组显隐的容器。

        分区之间用与 _build_page 一致的发丝分隔线（groupSep + 上下留白）。
        """
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        for i, (gtitle, fields) in enumerate(groups):
            if i:
                lay.addSpacing(18)
                sep = QFrame()
                sep.setObjectName("groupSep")
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFixedHeight(1)
                lay.addWidget(sep)
                lay.addSpacing(14)
            lay.addWidget(self._build_group(gtitle, fields))
        return wrap

    def _build_dialog_provider_area(self, pick) -> tuple:
        """对话页 provider 专属字段区：豆包 / Qwen / Hermes 三容器竖排、互斥显隐。
        返回 (area, doubao, qwen, hermes)；pick 为 __init__ 内的字段筛选闭包。

        「对话专属识别」(dialog.asr.*) 只被 Hermes 对话路径消费，另两家走服务端识别、
        本端不起 ASR，显示这一组等于给用户一个拧了没反应的旋钮。"""
        area = QWidget()
        v = QVBoxLayout(area)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        doubao = self._build_provider_container([
            ("set.group.doubao_keys",
             pick("dialog", ("api_key", "app_id", "access_token"))),
            ("set.group.doubao_model",
             pick("dialog", ("model", "bot_name", "system_role",
                             "speaking_style", "character_manifest"))),
            ("set.group.doubao_voice",
             pick("dialog", ("speaker", "speech_rate", "loudness_rate"))),
            ("set.group.doubao_behavior",
             pick("dialog", ("end_smooth_window_ms", "enable_user_query_exit",
                             "keep_context", "strict_audit"))),
        ])
        qwen = self._build_provider_container([
            ("set.group.qwen_in",
             pick("dialog.qwen", ("region", "workspace_id", "api_key"))),
            ("set.group.qwen_model",
             pick("dialog.qwen", ("model", "voice", "instructions"))),
            ("set.group.qwen_io",
             pick("dialog.qwen", ("turn_detection", "vad_threshold",
                                  "silence_duration_ms"))),
            ("set.group.qwen_behavior",
             pick("dialog.qwen", ("enable_speech_emotion", "max_history_turns",
                                  "enable_search"))),
        ])
        hermes = self._build_provider_container([
            ("set.group.hermes_in",
             pick("dialog.hermes", ("base_url", "api_key", "model",
                                    "session_title"))),
            ("set.group.hermes_timeout",
             pick("dialog.hermes", ("system_hint", "connect_timeout_ms",
                                    "turn_timeout_ms"))),
            ("set.group.dialog_asr",
             pick("dialog.asr", ("engine", "model", "lang", "hotword"))),
        ])
        v.addWidget(doubao)
        v.addWidget(qwen)
        v.addWidget(hermes)
        return area, doubao, qwen, hermes

    def _current_dialog_provider(self) -> str:
        """读 provider 下拉当前值并归一为 "doubao" | "aliyun" | "hermes"。

        combo 显示中文名，需反查回原始值（与 _save 同口径）；控件缺失或值异常回退
        "doubao"。白名单须与 loader.dialog_provider 三值判定一致，否则选中值被静默降级。"""
        combo = self._widgets.get(("dialog", "provider"))
        if combo is None:
            return "doubao"
        labels = self._combo_value_keys("dialog", "provider")
        text = combo.currentText()
        for val, key in labels.items():
            # 键名反查：_COMBO_LABELS 存的是 i18n 键，下拉显示的是 t(键)
            # 故比较对象也要 t() 一次（抽串前这里直接比中文显示名）
            if t(key) == text:
                return val
        return text if text in ("doubao", "aliyun", "hermes") else "doubao"

    def _on_dialog_provider_changed(self, *_args) -> None:
        """provider 下拉切换：豆包 / Qwen / Hermes 三组互斥显隐。

        *_args 吸收 currentIndexChanged 的 index 参数，也允许初始化时无参直调。
        容器缺失时静默跳过（防御，理论上不会发生）。
        """
        prov = self._current_dialog_provider()
        doubao = getattr(self, "_dialog_doubao_group", None)
        qwen = getattr(self, "_dialog_qwen_group", None)
        hermes = getattr(self, "_dialog_hermes_group", None)
        if doubao is not None:
            doubao.setVisible(prov == "doubao")
        if qwen is not None:
            qwen.setVisible(prov == "aliyun")
        if hermes is not None:
            hermes.setVisible(prov == "hermes")

    def _is_in_hidden_group(self, widget) -> bool:
        """widget 是否属于「另一 provider 的隐藏组」。

        判据用容器 isHidden() 而非 widget.isVisible()：后者受导航页/窗口显示状态影响，
        切走页签会把两组都算不可见、导致预校验与写盘被误跳过（_save 对隐藏组不校验不写盘）。"""
        for name in ("_dialog_doubao_group", "_dialog_qwen_group",
                     "_dialog_hermes_group"):
            group = getattr(self, name, None)
            if group is not None and group.isHidden() and group.isAncestorOf(widget):
                return True
        return False

    # ---- 语音对话「测试连接」（照 _build_llm_test_row 模式）----

    def _build_dialog_test_row(self) -> QWidget:
        """「语音模式 → 接入」组内：测试连接按钮行（读表单当前值建一次会话）。

        后台线程执行握手（不卡 UI），结果经 dialog_test_done 信号回主线程。
        一次测试 = 一次完整 StartSession（计一次 query），成功即收尾。
        """
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 2, 0, 0)
        h.setSpacing(10)

        btn = QPushButton(t("set.dialog_test.btn"))
        btn.setObjectName("btnGhost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(t("set.dialog_test.btn_tip"))
        status = QLabel()
        status.setWordWrap(True)
        status.setVisible(False)
        h.addWidget(btn)
        h.addWidget(status, 1)

        self._dialog_test_btn = btn
        self._dialog_test_status = status
        self.dialog_test_done.connect(self._on_dialog_test_done)
        btn.clicked.connect(self._on_dialog_test)
        return wrap

    def _dialog_field(self, key: str) -> str:
        """读语音对话表单当前值（未保存的编辑也生效）。"""
        w = self._widgets.get(("dialog", key))
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ""

    def _widget_value(self, section: str, key: str) -> str:
        """按 (section, key) 读标量/combo 类表单控件当前值。

        仅覆盖 QComboBox（显示名反查回原始值）/ SliderSpinBox / QSpinBox / QTextEdit /
        QLineEdit；不处理 DeviceCombo 等需 currentData 的控件，缺失或类型不符返回 ""。
        """
        w = self._widgets.get((section, key))
        if w is None:
            return ""
        if isinstance(w, QComboBox):
            text = w.currentText().strip()
            labels = self._combo_value_keys(section, key)
            # 键名反查：labels 存 i18n 键，下拉显示 t(键)，故比较对象也 t() 一次
            return str(next((v for v, key2 in labels.items() if t(key2) == text),
                            text))
        if isinstance(w, (SliderSpinBox, QSpinBox)):
            return str(w.value())
        if isinstance(w, QTextEdit):
            return w.toPlainText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ""

    def _resolve_dialog_form_credentials(self) -> tuple[str, str, str]:
        """(api_key, app_id, access_token)：表单值优先，留空按
        resolve_dialog_credentials 规则回退（dialog 留空自动复用 volcengine 节）。"""
        from ..config.loader import resolve_dialog_credentials

        cfg_key, cfg_app, cfg_tok = resolve_dialog_credentials(self.cfg)
        key = self._dialog_field("api_key") or cfg_key
        app = self._dialog_field("app_id") or cfg_app
        tok = self._dialog_field("access_token") or cfg_tok
        return key, app, tok

    def _on_dialog_test(self) -> None:
        """发起测试连接：按当前 provider 分派（豆包 / Qwen / Hermes）。

        Hermes 只给提示、不起握手：走豆包分支会报出用户没问的问题或误显「连接正常」，
        用户会读成「Hermes 通了」，而实际首次热键连接即失败。
        """
        if self._current_dialog_provider() == "hermes":
            self._show_dialog_test_result(
                None, t("set.dialog_test.hermes_unsupported"), neutral=True)
            return
        if self._current_dialog_provider() == "aliyun":
            self._start_qwen_dialog_test()
            return
        api_key, app_id, access_token = self._resolve_dialog_form_credentials()
        if not (api_key or (app_id and access_token)):
            self._show_dialog_test_result(False, t("set.dialog_test.need_key"))
            return
        self._show_dialog_test_result(None, t("set.dialog_test.connecting"))
        import threading
        threading.Thread(
            target=self._dialog_test_worker,
            args=(api_key, app_id, access_token),
            daemon=True, name="dialog-conn-test",
        ).start()

    def _dialog_test_worker(self, api_key: str, app_id: str,
                            access_token: str) -> None:
        """后台线程：建 client → 握手开会话 → 立即收尾，回发结果与 logid。"""
        from ..asr.doubao_dialog import DoubaoDialogClient

        client = DoubaoDialogClient(api_key=api_key, app_id=app_id,
                                    access_token=access_token)
        try:
            client.start()
        except Exception as exc:
            try:
                self.dialog_test_done.emit(False, str(exc))
            except RuntimeError:
                pass  # 对话框已销毁，静默丢弃
            return
        try:
            client.stop()
        except Exception:
            logger.debug("测试连接收尾失败（不影响结果）", exc_info=True)
        try:
            self.dialog_test_done.emit(
                True, t("set.dialog_test.ok",
                        logid=client.last_logid or t("set.dialog_test.no_logid")))
        except RuntimeError:
            pass

    def _start_qwen_dialog_test(self) -> None:
        """Qwen 测试连接：读表单 region/workspace/api_key/model，api_key 留空回退
        复用 aliyun.api_key，建 ws_url 后起后台线程握手（不卡 UI）。"""
        from ..config.loader import build_qwen_ws_url, resolve_qwen_credentials

        cfg_key, cfg_ws = resolve_qwen_credentials(self.cfg)
        api_key = self._widget_value("dialog.qwen", "api_key") or cfg_key
        workspace_id = self._widget_value("dialog.qwen", "workspace_id") or cfg_ws
        region = self._widget_value("dialog.qwen", "region") or "legacy"
        model = (self._widget_value("dialog.qwen", "model")
                 or "qwen-audio-3.0-realtime-plus")
        if not api_key:
            self._show_dialog_test_result(
                False, t("set.dialog_test.need_qwen_key"))
            return
        ws_url = build_qwen_ws_url(region, workspace_id, model)
        self._show_dialog_test_result(None, t("set.dialog_test.connecting"))
        import threading
        threading.Thread(
            target=self._qwen_dialog_test_worker,
            args=(api_key, ws_url),
            daemon=True, name="qwen-dialog-conn-test",
        ).start()

    def _qwen_dialog_test_worker(self, api_key: str, ws_url: str) -> None:
        """后台线程：建 QwenDialogClient → start() 握手开会话 → 收尾，回发结果。

        start() 阻塞至 session.updated 或抛 DialogError（含中文 hint）；成功即 stop()
        （无 FinishSession 握手，重复调用安全）。回显 session.id 作报障凭据。
        """
        from ..asr.qwen_dialog import QwenDialogClient

        client = QwenDialogClient(api_key=api_key, ws_url=ws_url)
        try:
            client.start()
        except Exception as exc:
            try:
                self.dialog_test_done.emit(False, str(exc))
            except RuntimeError:
                pass  # 对话框已销毁，静默丢弃
            return
        sid = getattr(client, "session_id", "") or ""
        try:
            client.stop()
        except Exception:
            logger.debug("Qwen 测试连接收尾失败（不影响结果）", exc_info=True)
        try:
            self.dialog_test_done.emit(
                True, t("set.dialog_test.ok_session",
                        session=sid or t("set.dialog_test.no_session")))
        except RuntimeError:
            pass

    def _on_dialog_test_done(self, ok: bool, msg: str) -> None:
        """测试完成（主线程）：渲染结果。"""
        self._show_dialog_test_result(ok, msg)

    def _show_dialog_test_result(self, ok: Optional[bool], text: str,
                                 neutral: bool = False) -> None:
        """更新测试状态标签：None=检测中（灰），True=成功（绿），False=失败（红）。

        neutral=True：中性提示（灰、按钮保持可用）——用于「这里测不了，请走诊断
        脚本」这类**不是失败**的信息，别拿红色失败样式吓用户。
        """
        btn, status = getattr(self, "_dialog_test_btn", None), \
            getattr(self, "_dialog_test_status", None)
        if btn is None or status is None:
            return
        if neutral:
            btn.setEnabled(True)
            color = self._palette.get("secondary", "#9d9da5")
        elif ok is None:
            btn.setEnabled(False)
            color = self._palette.get("secondary", "#9d9da5")
        else:
            btn.setEnabled(True)
            color = "#2e9e5b" if ok else "#d9534f"
        status.setText(text)
        status.setStyleSheet(f"color: {color};")
        status.setVisible(True)

    def _themed_shell(self, title: str) -> tuple:
        """无边框主题弹窗外壳：自绘标题栏 + 圆角面板。返回 (dlg, body 布局)。

        Qt.Tool 保证弹窗不进任务栏、无窗口图标；圆角由 Win11 DWM 自动裁切。
        """
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.Tool)
        dlg.setModal(True)
        dlg.setObjectName("promptDlg")
        dlg.setStyleSheet(self.styleSheet())
        dlg.setFont(self.font())
        dlg.setMinimumWidth(280)

        panel = QWidget()
        panel.setObjectName("promptPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        # 标题栏：左侧标题 + 右上关闭按钮，按住可拖动
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setCursor(Qt.OpenHandCursor)
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(14, 8, 6, 4)
        tb.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        btn_close = QPushButton("×")
        btn_close.setObjectName("btnClose")
        btn_close.setFixedSize(26, 26)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setToolTip(t("set.btn.close"))
        btn_close.clicked.connect(dlg.reject)
        tb.addWidget(title_label)
        tb.addStretch(1)
        tb.addWidget(btn_close)
        pv.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(16, 0, 16, 12)
        body.setSpacing(10)
        pv.addLayout(body)

        drag = {"pos": None}

        def _press(ev) -> None:
            if ev.button() == Qt.LeftButton:
                drag["pos"] = (
                    ev.globalPosition().toPoint() - dlg.frameGeometry().topLeft()
                )

        def _move(ev) -> None:
            if drag["pos"] is not None and ev.buttons() & Qt.LeftButton:
                dlg.move(ev.globalPosition().toPoint() - drag["pos"])

        def _release(ev) -> None:
            drag["pos"] = None

        bar.mousePressEvent = _press
        bar.mouseMoveEvent = _move
        bar.mouseReleaseEvent = _release
        return dlg, body

    def _themed_msg(self, text: str, buttons=None, title: Optional[str] = None,
                    default: Optional[str] = None) -> str:
        """主题化消息框：无边框 + 自绘标题栏，与设置界面一体。

        返回被点击的按钮文字；关闭（X/Esc）返回空串。default 指定回车触发的默认按钮
        （缺省=最后一个）。buttons/title 缺省值须在**函数体内**取：默认参数导入期求值，
        写 t() 会把当时的界面语言焊死。"""
        if buttons is None:
            buttons = (t("common.def.ok"),)
        if title is None:
            title = t("common.notice")
        dlg, body = self._themed_shell(title)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        body.addWidget(lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        clicked = []

        def _pick(name: str) -> None:
            clicked.append(name)
            dlg.accept()

        default_name = default or buttons[-1]
        for name in buttons:
            b = QPushButton(name)
            if name == default_name:
                b.setDefault(True)
            b.clicked.connect(lambda _=False, n=name: _pick(n))
            row.addWidget(b)
        body.addLayout(row)
        dlg.exec()
        return clicked[0] if clicked else ""

    def _themed_input(self, title: str, label: str,
                      placeholder: str = "") -> tuple:
        """主题化单行输入框：无边框 + 自绘标题栏，返回 (文本, 是否确定)。"""
        dlg, body = self._themed_shell(title)
        lbl = QLabel(label)
        edit = QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        _install_text_menu(edit, self._workshop_palette())
        body.addWidget(lbl)
        body.addWidget(edit)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton(t("common.cancel"))
        btn_ok = QPushButton(t("common.def.ok"))
        btn_ok.setDefault(True)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        body.addLayout(row)
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(dlg.accept)
        edit.returnPressed.connect(dlg.accept)
        QTimer.singleShot(0, edit.setFocus)  # 打开即聚焦，直接打字
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return edit.text().strip(), ok

    def _refresh_prompt_combo(self, content: str, select_name: Optional[str] = None) -> None:
        """重建版本下拉并选中合适项，编辑框内容随之设置。

        选中规则：显式指定 > 内容与某版本一致 > 空内容选默认 > 临时项
        （临时项仅 llm.prompt 非空且不匹配任何版本时出现）。
        """
        from ..postprocess import prompt_presets

        combo, edit = self._prompt_combo, self._prompt_edit
        versions = prompt_presets.all_versions()
        combo.blockSignals(True)
        combo.clear()
        for name in versions:
            combo.addItem(name, name)  # itemData=版本名（内置与自定义同名不加标注）
        target = None
        if select_name and select_name in versions:
            target = select_name
        elif content.strip():
            for name, c in versions.items():
                if c.strip() == content.strip():
                    target = name
                    break
        else:
            target = next(iter(versions), None)
        edit.blockSignals(True)
        if target is None:
            combo.insertItem(0, self._prompt_temp_item(), "")
            combo.setCurrentIndex(0)
            edit.setPlainText(content)
        else:
            combo.setCurrentIndex(max(0, combo.findData(target)))
            edit.setPlainText(versions[target])
        edit.blockSignals(False)
        combo.blockSignals(False)
        self._update_prompt_del_btn()

    def _update_prompt_del_btn(self) -> None:
        """删除按钮仅在选中自定义版本时可用（内置版本不可删）。"""
        from ..postprocess import prompt_presets

        name = self._prompt_combo.currentData()
        self._prompt_del_btn.setEnabled(bool(name) and name in prompt_presets.custom_prompts())

    def _on_prompt_version_changed(self, index: int) -> None:
        """切换版本：把版本内容载入编辑框（临时项不加载，保留现有编辑）。"""
        from ..postprocess import prompt_presets

        name = self._prompt_combo.itemData(index)
        if name:
            content = prompt_presets.all_versions().get(name)
            if content is not None:
                self._prompt_edit.setPlainText(content)
        self._update_prompt_del_btn()

    def _on_prompt_add(self) -> None:
        """新增：命名后创建一个空的自定义版本并选中，等待输入内容。"""
        from ..postprocess import prompt_presets

        name, ok = self._themed_input(
            t("set.prompt.add_title"), t("set.prompt.add_label"),
            t("set.prompt.add_placeholder"))
        if not ok or not name:
            return
        if name in prompt_presets.BUILTIN_PROMPTS:
            self._themed_msg(t("set.prompt.err_builtin_name"),
                             title=t("set.prompt.add_short"))
            return
        if name in prompt_presets.custom_prompts():
            self._themed_msg(t("set.prompt.err_exists", name=name),
                             title=t("set.prompt.add_short"))
            return
        prompt_presets.save_custom(name, "")
        self._refresh_prompt_combo("", select_name=name)

    def _on_prompt_save_current(self) -> None:
        """保存：把编辑框当前内容写入选中的版本（内置方案除外）。"""
        from ..postprocess import prompt_presets

        name = self._prompt_combo.currentData()
        if not name:
            self._themed_msg(
                t("set.prompt.err_no_version"),
                title=t("set.prompt.save_short"))
            return
        if name in prompt_presets.BUILTIN_PROMPTS:
            self._themed_msg(
                t("set.prompt.err_builtin_readonly"),
                title=t("set.prompt.save_short"))
            return
        text = self._prompt_edit.toPlainText().strip()
        if not text:
            self._themed_msg(t("set.prompt.err_empty"),
                             title=t("set.prompt.save_short"))
            return
        if "{text}" not in text:
            ret = self._themed_msg(
                t("set.prompt.warn_no_placeholder"),
                buttons=(t("common.cancel"), t("set.prompt.save_short")),
                title=t("set.prompt.save_short"))
            if ret != t("set.prompt.save_short"):
                return
        prompt_presets.save_custom(name, text)
        self._refresh_prompt_combo(text, select_name=name)

    def _on_prompt_delete(self) -> None:
        """删除当前选中的自定义版本，回到默认版本。"""
        from ..postprocess import prompt_presets

        name = self._prompt_combo.currentData()
        if not name or name not in prompt_presets.custom_prompts():
            return
        ret = self._themed_msg(
            t("set.prompt.confirm_delete", name=name),
            buttons=(t("common.cancel"), t("set.prompt.delete_short")),
            title=t("set.prompt.delete_short"))
        if ret != t("set.prompt.delete_short"):
            return
        prompt_presets.delete_custom(name)
        self._refresh_prompt_combo("")

    def _combo_options(self, section, key, extra) -> tuple[list[str], dict[str, str]]:
        """combo 字段的 (候选值列表, 值→i18n 键) 两元组。

        extra 两种形态：list/tuple = 候选项列表，显示名查 `_COMBO_LABELS`（无映射
        则原样显示）；dict = 值→**i18n 键**映射，key 须含全部候选值，顺序即插入顺序。
        """
        if isinstance(extra, dict):
            options = [str(v) for v in extra.keys()]
            display = {v: extra[k] for k, v in zip(extra.keys(), options)}
            return options, display
        options = [str(v) for v in (extra or [])]
        mapped = _COMBO_LABELS.get((section, key), {})
        # 无映射的候选项（如直接显示模型 ID）：显示名即值，词条键回退到值本身
        display = {v: mapped.get(v, v) for v in options}
        return options, display

    def _combo_value_keys(self, section, key) -> dict[str, str]:
        """(section, key) 的 combo 反查表：原始值 -> i18n 显示键。

        与 `_combo_options` 的返回值同源（后者返回 值列表+映射），
        抽出来专供"保存/读值"侧把下拉显示名反查回原始值用。
        """
        field = next((f for table in (_FIELDS, _ADVANCED_FIELDS) for f in table
                      if f[0] == section and f[1] == key), None)
        extra = field[4] if field else None
        _, display = self._combo_options(section, key, extra)
        return display

    def _make_widget(self, section, key, label, wtype, extra) -> Optional[QWidget]:
        current = self._read_cfg(section, key)
        if (section, key) == ("llm", "prompt"):
            # 自定义 Prompt：版本下拉（内置+自定义）+ 多行编辑 + 存为/删除版本
            return self._build_prompt_editor(str(current if current is not None else ""))
        if wtype == "hotkey":
            w = HotkeyCaptureEdit(str(current if current is not None else ""))
            _install_text_menu(w, self._workshop_palette())
            return w
        if wtype == "str":
            w = QLineEdit(str(current if current is not None else ""))
            _install_text_menu(w, self._workshop_palette())
            return w
        if wtype == "text":
            # 多行文本（如「帮我」AI 命令提示词模板）：配置为空时预填内置
            # 默认模板，让用户能直接看到/修改占位符结构；清空保存=恢复默认。
            # 其余 text 字段（语音对话人设等）空就是空，不预填
            text = str(current if current is not None else "")
            if not text and (section, key) == ("commands", "ai_prompt_template"):
                from ..core.voice_commands import AI_PROCESS_PROMPT
                text = AI_PROCESS_PROMPT
            w = QTextEdit()
            w.setPlainText(text)
            w.setAcceptRichText(False)
            w.setMinimumWidth(430)
            w.setMinimumHeight(180)
            _install_text_menu(w, self._workshop_palette())
            return w
        if wtype == "secret":
            w = QLineEdit(str(current if current is not None else ""))
            w.setEchoMode(QLineEdit.EchoMode.Password)
            # 预填现值（回显为圆点）；清空则保存时跳过不覆盖
            if current:
                w.setText(str(current))
            _install_text_menu(w, self._workshop_palette())
            return w
        if wtype == "int":
            lo, hi = extra if extra else (0, 9999)
            w = QSpinBox()
            w.setRange(lo, hi)
            try:
                w.setValue(int(current))
            except (TypeError, ValueError):
                w.setValue(lo)
            return w
        if wtype == "slider":
            lo, hi, step = (extra or (0, 99, 1))[:3]
            # 单位后缀在字段表里存 i18n 键（导入期不能求值），此处才 t()
            suffix_key = extra[3] if len(extra) > 3 else ""
            suffix = t(suffix_key) if suffix_key else ""
            w = SliderSpinBox(lo, hi, step, suffix)
            try:
                w.setValue(int(current))
            except (TypeError, ValueError):
                w.setValue(lo)
            return w
        if wtype == "bool":
            w = ToggleSwitch(self._palette)
            if (section, key) == ("hotkey", "hold_to_talk"):
                # 「长按说话」的开关显示必须与运行时同一个判定函数，不能顺手 bool()：
                # hold_flag_enabled 只认 True 与白名单字符串（认不出就是关，fail closed），
                # bool() 会把拼错字符串与 YAML 裸整数 1 显示成「开」——两套真值规则会让用户
                # 看到「功能开着、按下去却毫无反应」。其它 bool 字段判据本就是 bool()，不动。
                w.setChecked(hold_flag_enabled(current))
            else:
                w.setChecked(bool(current))
            return w
        if wtype == "theme_cards":
            # 设置界面主题：分段卡片选择（参考图「外观」样式），选中即预览
            w = ThemeCardGroup(str(current if current is not None else ""))
            w.changed.connect(self._on_settings_style_preview)
            return w
        if wtype == "combo":
            w = QComboBox()
            options, display = self._combo_options(section, key, extra)
            w.addItems([t(display[v]) for v in options])
            val = str(current if current is not None else "")
            if val in options:
                w.setCurrentText(t(display[val]))
            else:
                w.setEditable(True)
                w.setCurrentText(val)
            return w
        if wtype == "billing":
            # 火山「计费方式」：duration/concurrent 两项直选（值为对应版本自己的计费）。
            # 版本选择在右键菜单，豆包 1.0 / 2.0 各有独立下拉互不干扰
            w = QComboBox()
            w.addItems([t("set.combo.volcengine.billing.duration"),
                        t("set.combo.volcengine.billing.concurrent")])
            cur = str(current if current is not None else "")
            w.setCurrentText(
                t("set.combo.volcengine.billing.concurrent") if cur == "concurrent"
                else t("set.combo.volcengine.billing.duration"))
            return w
        if wtype == "device_combo":
            # 麦克风设备选择：DeviceCombo 每次展开下拉时重新枚举（支持热插拔）
            cur = str(current if current is not None else "")
            w = DeviceCombo(cur)
            w.setToolTip(t("set.device.mic_tip"))
            return w
        if wtype == "output_combo":
            # 输出设备选择（语音对话 AI 播报）：同 DeviceCombo，枚举输出通道
            cur = str(current if current is not None else "")
            w = DeviceCombo(cur, inputs=False)
            w.setToolTip(t("set.device.spk_tip"))
            return w
        return None

    def _on_settings_style_preview(self, name: str) -> None:
        """设置界面主题 combo 切换即时预览：仅改内存 palette + 重设 QSS +
        通知所有 ToggleSwitch 重绘，不写 config（点保存才落盘）。
        """
        if name not in SETTINGS_STYLES:
            return
        # 就地更新 cfg.ui.settings_style —— _save 时读到的就是最后预览值
        if hasattr(self.cfg, "ui") and self.cfg.ui is not None:
            try:
                self.cfg.ui.settings_style = name
            except AttributeError:
                logger.debug("同步内存 settings_style 失败（cfg.ui 缺失）", exc_info=True)
        self._palette = _theme_palette(self.cfg)
        self.setStyleSheet(_settings_qss(self._palette))
        for tw in self.findChildren(ToggleSwitch):
            tw.set_palette(self._palette)

    def _read_cfg(self, section: Optional[str], key: str):
        try:
            if section is None:
                return getattr(self.cfg, key)
            node = self.cfg
            for part in section.split("."):
                node = getattr(node, part, None)
                if node is None:
                    return None
            return getattr(node, key)
        except AttributeError:
            return None

    # ---- 保存 ----

    def _save(self) -> None:
        from ..config.loader import update_config_field

        # 自定义短语词表先校验（重名/成对），失败则整体不保存
        # 避免配置已落盘但词表被拦截的半截状态
        phrases, phrase_err = self._collect_phrases()
        if phrase_err:
            self._themed_msg(t("set.phrase.save_err_prefix", error=phrase_err))
            return

        # 数值字段预校验（与短语同策略：先全量校验再落盘，失败整体不保存）：
        # vad_threshold 是自由文本框，可被清空/填非数字；不拦的话 loader 的 float
        # 回退会把它写成带引号字符串且回读"成功"，直到对话启动时 float() 才崩。
        # 另一 provider 隐藏组的字段跳过（见 _is_in_hidden_group）。
        for (section, key), widget in self._widgets.items():
            if (section, key) in _FLOAT_FIELDS and isinstance(widget, QLineEdit):
                if self._is_in_hidden_group(widget):
                    continue
                text_value = widget.text().strip()
                try:
                    parsed = float(text_value)
                except ValueError:
                    parsed = None
                if parsed is None or not math.isfinite(parsed):
                    self._themed_msg(
                        t("set.save.invalid_number",
                          label=t(_field_label_keys().get(
                              (section, key), f"{section}.{key}")),
                          value=text_value))
                    return

        changed = 0
        for (section, key), widget in self._widgets.items():
            if self._is_in_hidden_group(widget):
                continue  # 只保存当前 provider 组的字段（另一家保持原值）
            value_type = "str"
            if isinstance(widget, DeviceCombo):
                # 设备选择（麦克风/语音对话播放设备）：存 itemData
                #（设备名；"系统默认"存空串），不能存显示文本
                value = str(widget.currentData() or "")
                if update_config_field(key, value, section=section, value_type="str"):
                    changed += 1
                continue
            if isinstance(widget, ThemeCardGroup):
                value = str(widget.current_key())
            elif isinstance(widget, QLineEdit):
                value = widget.text().strip()
                if not value and widget.echoMode() == QLineEdit.EchoMode.Password:
                    continue  # 密钥留空不覆盖
            elif isinstance(widget, QTextEdit):
                value = widget.toPlainText().strip()
                # 多行文本一律 JSON 双引号标量写入：换行符在 YAML 加载后
                # 还原（单引号标量会把 \n 当字面量，_write_and_verify 回读
                # 不符会回滚 = 保存静默失败）。QTextEdit 出现在表单里
                # 即代表多行字段（LLM Prompt / 命令模板 / 对话人设等）。
                value_type = "json"
            elif isinstance(widget, SliderSpinBox):
                value = str(widget.value())
                value_type = "int"
            elif isinstance(widget, QSpinBox):
                value = str(widget.value())
                value_type = "int"
            elif isinstance(widget, QCheckBox):
                value = "true" if widget.isChecked() else "false"
                value_type = "bool"
            elif isinstance(widget, QComboBox):
                value = widget.currentText().strip()
                # 友好显示名反查回原始值；整数型字段（如 filter_modal）写裸数字。
                # labels 存 i18n 键、下拉显示 t(键)，故比较对象同样 t() 一次——漏这一步会把
                # 显示用的中文当值写进 config.yaml。
                labels = self._combo_value_keys(section, key)
                value = next((v for v, key2 in labels.items() if t(key2) == value),
                             value)
                if (section, key) in _COMBO_INT_FIELDS:
                    value_type = "int"
            else:
                continue
            if (section, key) in _FLOAT_FIELDS:
                value_type = "float"
            if update_config_field(key, value, section=section, value_type=value_type):
                changed += 1

        # 日志保留天数（非表单字段，单独写入 logging 段）
        try:
            rv = int(self._retention_spin.value())
            if update_config_field("retention_days", str(rv),
                                   section="logging", value_type="int"):
                changed += 1
        except Exception:
            logger.debug("写入 retention_days 失败", exc_info=True)

        # 自定义短语词表（非 config.yaml 字段，写入状态目录 phrases.json；
        # 入口处已校验，这里仅写盘）
        from ..core import custom_phrases
        if not custom_phrases.save(phrases):
            self._themed_msg(t("set.phrase.save_failed"))
            return

        logger.info("设置对话框：已更新 %d 个配置项", changed)
        self.saved_count = changed
        # 保存成功直接关闭；「已保存 N 项」由悬浮条瞬时提示承担
        self.accept()
