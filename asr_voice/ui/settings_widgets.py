"""设置对话框专用自定义控件。

从 settings_dialog.py 拆出：主题分段卡片、可展开卡片、拨动开关、
扁平滑块、热键捕获输入框、滑块+数字框组合，以及主题卡片图标绘制
与热键格式转换辅助。控件之间仅 FlatSlider 被 SliderSpinBox 复用，
与对话框布局逻辑无耦合。
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from .settings_styles import SETTINGS_STYLES, _DEFAULT_STYLE


def _theme_card_icon(kind: str, color: str) -> QIcon:
    """主题分段卡片的图标：sun=太阳（浅色）/ moon=月牙（深色）。"""
    pm = QPixmap(40, 40)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == "moon":
        # 大小两圆 OddEven 差集 = 月牙
        path = QPainterPath()
        path.addEllipse(QRectF(9.5, 9.5, 21, 21))
        path.addEllipse(QRectF(15.5, 7.5, 19, 19))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(path)
    else:  # sun
        pen = QPen(QColor(color))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(14.6, 14.6, 10.8, 10.8))
        for i in range(8):
            a = math.radians(i * 45)
            x1, y1 = 20 + 9.6 * math.cos(a), 20 + 9.6 * math.sin(a)
            x2, y2 = 20 + 13.2 * math.cos(a), 20 + 13.2 * math.sin(a)
            p.drawLine(int(round(x1)), int(round(y1)),
                       int(round(x2)), int(round(y2)))
    p.end()
    return QIcon(pm)


class ThemeCardGroup(QWidget):
    """主题分段卡片选择（参考图「外观」样式）：图标+文字卡片，选中白描边。

    key 即 SETTINGS_STYLES 主题名；current_key() 供保存；
    changed(key) 信号供即时预览换肤。
    """

    changed = Signal(str)

    # (内部键, 显示名 i18n 键, 图标 kind)；默认主题放最前。
    # 显示名必须留到 __init__ 里再 t()：类属性在导入期就求值，那一刻连
    # «用户说的哪种语言» 都还没定（A5 的 apply_configured_language 要等
    # main 起来才跑），此处写死等于把中文永久焊在界面上。
    _CARDS: list[tuple[str, str, str]] = [
        ("minimal_dark", "set.style.minimal_dark", "moon"),
        ("fluent_light", "set.style.fluent_light", "sun"),
    ]

    def __init__(self, current: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = current if current in SETTINGS_STYLES else _DEFAULT_STYLE
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self._buttons: dict[str, QToolButton] = {}
        for key, label_key, icon_kind in self._CARDS:
            b = QToolButton()
            b.setObjectName("themeCard")
            b.setText(t(label_key))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIcon(_theme_card_icon(icon_kind, "#b8b8c0"))
            b.setIconSize(QSize(26, 26))
            b.setMinimumHeight(78)
            # 通栏三等分：QToolButton 默认尺寸策略不随 stretch 扩张，
            # 显式 Expanding 让三张卡片铺满整行（参考图「外观」同款）
            b.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
            b.setChecked(key == self._key)
            b.clicked.connect(lambda _=False, k=key: self._on_click(k))
            lay.addWidget(b, 1)
            self._buttons[key] = b

    def _on_click(self, key: str) -> None:
        if key == self._key:
            return
        self._key = key
        for k, b in self._buttons.items():
            b.setChecked(k == key)
        self.changed.emit(key)

    def current_key(self) -> str:
        return self._key


class AccordionCard(QWidget):
    """可展开卡片（复刻参考图「插件配置」样式）：标题+说明在左、箭头在右，
    点击头部展开/收起内容区。

    展开状态不持久化（每次打开设置回默认）；默认由调用方指定
    （当前引擎的卡片展开，其余收起，保持列表干净）。
    """

    def __init__(self, title: str, desc: str = "", expanded: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("accordionCard")
        # QWidget 背景 QSS 需要显式开启 styled background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 13, 18, 13)
        outer.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("accordionHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setMinimumHeight(44)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(2)
        # 局部名不用 t：本模块 import 了 i18n 的 t()，函数内绑定 t 会让
        # 整个函数作用域的 t 变成局部名，同函数内 t("key") 会 UnboundLocalError。
        name_lbl = QLabel(title)
        name_lbl.setObjectName("accordionTitle")
        left.addWidget(name_lbl)
        if desc:
            d = QLabel(desc)
            d.setObjectName("accordionDesc")
            d.setWordWrap(True)
            left.addWidget(d)
        hl.addLayout(left, 1)
        self._chev = QLabel("▾")
        self._chev.setObjectName("accordionChevron")
        self._chev.setCursor(Qt.CursorShape.PointingHandCursor)
        hl.addWidget(self._chev)
        outer.addWidget(self._header)

        # 标题/说明实例引用：供 set_content_enabled 置灰、调用方更新文案
        self._title_lbl = name_lbl
        self._desc_lbl = d if desc else None

        self._body = QWidget()
        self._body.setObjectName("accordionBody")
        outer.addWidget(self._body)

        self._expanded = False
        self._header.mousePressEvent = self._on_header_click
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        self._expanded = bool(on)
        self._body.setVisible(self._expanded)
        self._chev.setText("▴" if self._expanded else "▾")

    def _on_header_click(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(not self._expanded)
            ev.accept()

    def body_container(self) -> QWidget:
        return self._body

    def is_expanded(self) -> bool:
        return self._expanded

    def set_desc(self, text: str) -> None:
        """更新说明文案（引擎排序：随模型显隐动态刷新"已隐藏"计数）。"""
        if self._desc_lbl is not None:
            self._desc_lbl.setText(text)

    def add_header_widget(self, widget: QWidget) -> None:
        """在头部右侧（箭头之前）插入自定义控件（如显隐开关/排序按钮）。

        箭头始终保持在最右；点击这些控件由控件自身消费事件，
        不会触发头部的展开/收起。
        """
        lay = self._header.layout()
        lay.insertWidget(max(lay.count() - 1, 0), widget)

    def set_content_enabled(self, on: bool) -> None:
        """标题与说明置灰/恢复（配合"厂商总开关"整体隐藏时的视觉反馈）。"""
        self._title_lbl.setEnabled(on)
        if self._desc_lbl is not None:
            self._desc_lbl.setEnabled(on)


class _DisclosureTriangle(QWidget):
    """自绘 disclosure 小三角（macOS 式）：收起右指、展开下指。

    内置普惠体对 ▸/▾/▶ 等几何三角字形覆盖不全（实测仅 ▲▼ 可渲染，
    QFontMetrics.inFont 报告不可靠），字体方案太脆弱——矢量自绘
    一次到位，且边缘抗锯齿比小字号字形更干净。
    """

    def __init__(self, color: QColor, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = color
        self._down = False
        self.setFixedSize(14, 14)

    def set_down(self, on: bool) -> None:
        if self._down != bool(on):
            self._down = bool(on)
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        w, h = self.width(), self.height()
        if self._down:  # 下指：顶边两点 + 底中点
            pts = [QPointF(3.5, 5), QPointF(w - 3.5, 5), QPointF(w / 2, h - 4)]
        else:           # 右指：左边两点 + 右中点
            pts = [QPointF(5, 3.5), QPointF(5, h - 3.5), QPointF(w - 4, h / 2)]
        p.drawPolygon(QPolygonF(pts))
        p.end()


class EngineOrderRow(QWidget):
    """引擎排序专属行块（苹果「分组列表」风：iOS/macOS 设置的 inset grouped
    list）——透明行块，视觉分组由外层 QWidget#engineOrderGroup 大圆角组承担。

    一个行块 = 厂商头行（▸ + 名称/副标题两行式 + 右侧控件位）
    + 可展开模型区 + 底部 inset 分隔线（末行由 set_trailing_sep_visible
    隐藏）。与上一版"独立卡片"设计的差异：
    - 不再有单卡背景/边框，行与行之间只用 inset 发丝分隔线（左缩进
      对齐文本、右到边，iOS 标志性细节），整组是一个大圆角容器
    - 排序控件是 iOS UIStepper 式实底胶囊分段（横排 ▲|▼，填充底+细描边
      +中缝），显眼且可点感强；头行两行式：主标题 + 灰色小字副标题
      （"N 个模型"），右侧开关固定最右（iOS 惯例），步进器居其左
    """

    def __init__(self, title: str, desc: str = "", expanded: bool = False,
                 badge: str = "", palette: Optional[dict] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("eoVendorBlock")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # —— 头行：三角 + (名称 + badge / 副标题) + 右侧控件位 ——
        # 左 14 + 三角 14 + 间距 8 → 文本起始 x=36，模型行与分隔线同缩进对齐
        self._header = QWidget()
        self._header.setObjectName("eoVendorHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setMinimumHeight(52)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(14, 6, 12, 6)
        hl.setSpacing(8)
        p = palette or {}
        self._chev = _DisclosureTriangle(QColor(p.get("secondary", "#a5a5ac")))
        hl.addWidget(self._chev, 0, Qt.AlignmentFlag.AlignVCenter)

        left = QVBoxLayout()
        left.setSpacing(1)
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        # 局部名不用 t（见本文件 accordion 段落同款说明）
        vendor_lbl = QLabel(title)
        vendor_lbl.setObjectName("eoVendorName")
        r1.addWidget(vendor_lbl)
        self._badge: Optional[QLabel] = None
        if badge:
            b = QLabel(badge)
            b.setObjectName("eoBadge")
            r1.addWidget(b)
            self._badge = b
        r1.addStretch(1)
        left.addLayout(r1)
        d = QLabel(desc)
        d.setObjectName("eoVendorMeta")
        left.addWidget(d)
        hl.addLayout(left, 1)
        outer.addWidget(self._header)

        # 展开区顶部分隔线（随展开显隐）+ 模型区 + 块底分隔线（末行隐藏）
        self._body_sep = self._make_sep()
        outer.addWidget(self._body_sep)
        self._body = QWidget()
        self._body.setObjectName("eoModels")
        outer.addWidget(self._body)
        self._trailing_sep = self._make_sep()
        outer.addWidget(self._trailing_sep)

        # 实例引用：置灰联动 / 文案刷新 / 步进器状态
        self._title_lbl = vendor_lbl
        self._meta_lbl = d
        self._up_btn: Optional[QPushButton] = None
        self._down_btn: Optional[QPushButton] = None

        self._expanded = False
        self._header.mousePressEvent = self._on_header_click
        self.set_expanded(expanded)

    @staticmethod
    def _make_sep() -> QFrame:
        """inset 发丝分隔线（QSS margin-left 缩进对齐文本，右到边）。"""
        s = QFrame()
        s.setObjectName("eoSep")
        s.setFrameShape(QFrame.Shape.HLine)
        s.setFixedHeight(1)
        return s

    def set_expanded(self, on: bool) -> None:
        self._expanded = bool(on)
        self._body.setVisible(on)
        self._body_sep.setVisible(on)
        self._chev.set_down(on)

    def _on_header_click(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(not self._expanded)
            ev.accept()

    def body_container(self) -> QWidget:
        return self._body

    def is_expanded(self) -> bool:
        return self._expanded

    def set_desc(self, text: str) -> None:
        """更新副标题（随模型显隐动态刷新"已隐藏"计数）。"""
        self._meta_lbl.setText(text)

    def add_header_widget(self, widget: QWidget) -> None:
        """头行右侧末尾追加控件（先步进器后开关 → 开关最右，iOS 惯例）。

        点击这些控件由控件自身消费事件，不会触发头部的展开/收起。
        """
        self._header.layout().addWidget(widget)

    def set_content_enabled(self, on: bool) -> None:
        """名称/副标题/badge 置灰或恢复（厂商总开关关闭时的视觉反馈）。"""
        self._title_lbl.setEnabled(on)
        self._meta_lbl.setEnabled(on)
        if self._badge is not None:
            self._badge.setEnabled(on)

    def set_trailing_sep_visible(self, on: bool) -> None:
        """块底分隔线显隐：列表末行隐藏（重排后由调用方统一刷新）。"""
        self._trailing_sep.setVisible(on)

    def make_stepper(self, on_up, on_down) -> QWidget:
        """生成横排 ↑↓ 步进器（iOS UIStepper 式实底胶囊分段）。

        填充底 + 细描边 + 中缝竖分隔线把两个方向"装"成一个控件，
        箭头用正文色——远看也读得出这是可点的排序件。
        悬停以 btn_hover 加深该半区、按压以 selection_bg 强反馈；
        首/尾方向由 set_step_enabled 禁用（仅禁点击，无差异配色，
        两端箭头与其余保持同色）。
        """
        stepper = QFrame()
        stepper.setObjectName("eoStepper")
        sv = QHBoxLayout(stepper)
        sv.setContentsMargins(1, 1, 1, 1)
        sv.setSpacing(0)
        up = QPushButton("↑")
        down = QPushButton("↓")
        for b in (up, down):
            b.setObjectName("eoStepBtn")
            b.setFixedSize(26, 18)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        mid = QFrame()
        mid.setObjectName("eoStepperSep")
        mid.setFrameShape(QFrame.Shape.VLine)
        mid.setFixedWidth(1)
        up.setToolTip(t("set.btn.move_up"))
        down.setToolTip(t("set.btn.move_down"))
        up.clicked.connect(on_up)
        down.clicked.connect(on_down)
        sv.addWidget(up)
        sv.addWidget(mid)
        sv.addWidget(down)
        self._up_btn = up
        self._down_btn = down
        return stepper

    def set_step_enabled(self, up_ok: bool, down_ok: bool) -> None:
        """列表首行禁用 ▲、末行禁用 ▼（仅拦截点击，无差异配色：
        两端箭头与其余保持同色，避免"坏掉了一样"的视觉噪声）。"""
        if self._up_btn is not None:
            self._up_btn.setEnabled(up_ok)
        if self._down_btn is not None:
            self._down_btn.setEnabled(down_ok)


class ToggleSwitch(QCheckBox):
    """iOS 风格拨动开关：自绘胶囊轨道 + 圆点，带 140ms 滑动动画。

    继承 QCheckBox 以复用 toggled 信号与勾选状态，保存逻辑无需改动。
    """

    def __init__(self, palette: Optional[dict] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # 4 色取自 palette（两套预设各有 toggle_track_on/knob_on 等），
        # 缺省回退默认深色配色以保持向后兼容
        p = palette or {}
        self._track_off = QColor(p.get("toggle_track_off", "#3d3d3d"))
        self._track_on = QColor(p.get("toggle_track_on", "#e4e4e4"))
        self._knob_off = QColor(p.get("toggle_knob_off", "#ffffff"))
        self._knob_on = QColor(p.get("toggle_knob_on", "#161616"))
        self._knob = 0.0  # 圆点位置 0.0(关)~1.0(开)
        # 控件固定为轨道实际大小（46x24）：布局不会把它拉宽成
        # "左侧小轨道 + 右侧大片空白"的怪异命中区
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._on_toggled)

    def hitButton(self, pos) -> bool:
        """整条轨道都可点击（QCheckBox 默认只有左侧指示器 ~13px 命中，
        轨道其余部分点了没反应）。"""
        return self.rect().contains(pos)

    def set_palette(self, palette: dict) -> None:
        """运行时换色（主题 combo 即时预览用）。自绘控件不受 setStyleSheet 影响，
        需要逐个 set_palette + update 重绘。"""
        self._track_off = QColor(palette.get("toggle_track_off", "#3d3d3d"))
        self._track_on = QColor(palette.get("toggle_track_on", "#e4e4e4"))
        self._knob_off = QColor(palette.get("toggle_knob_off", "#ffffff"))
        self._knob_on = QColor(palette.get("toggle_knob_on", "#161616"))
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, v: float) -> None:
        self._knob = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def sizeHint(self):
        return QSize(46, 24)

    @staticmethod
    def _lerp(a: QColor, b: QColor, ratio: float) -> QColor:
        # 参数名不用 t（见本文件 accordion 段落同款说明）
        return QColor(
            int(a.red() + (b.red() - a.red()) * ratio),
            int(a.green() + (b.green() - a.green()) * ratio),
            int(a.blue() + (b.blue() - a.blue()) * ratio),
        )

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = 46, 24
        y = (self.height() - h) // 2
        ratio = self._knob
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._lerp(self._track_off, self._track_on, ratio))
        p.drawRoundedRect(0, y, w, h, h / 2, h / 2)
        d = h - 6
        x = 3 + ratio * (w - d - 6)
        p.setBrush(self._lerp(self._knob_off, self._knob_on, ratio))
        p.drawEllipse(int(x), y + 3, d, d)
        p.end()


class FlatSlider(QSlider):
    """整条轨道可点击/拖动的扁平滑块。

    原生 QSlider 只有把手（白点）能拖，4px 细轨道点上去没反应——
    重写鼠标事件：轨道任意位置按下即跳到该值并进入拖动，同时把
    控件最小高度提到 24px（轨道上下 10px 内的点击都命中）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumHeight(24)
        self._dragging = False

    def _value_at_x(self, x: int) -> int:
        """把横向像素坐标换算成滑块值（考虑把手宽度占用的两端）。"""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, self)
        handle_w = max(1, handle.width())
        span = max(1, self.width() - handle_w)
        x = max(0, min(x - handle_w // 2, span))
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, span,
            opt.upsideDown)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setValue(self._value_at_x(int(ev.position().x())))
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._dragging and ev.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at_x(int(ev.position().x())))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        self._dragging = False
        super().mouseReleaseEvent(ev)


# ---- 热键捕获 ----

# Qt 修饰键按下标志 -> keyboard 库键名（event.modifiers() 是 KeyboardModifier）
_HOTKEY_MOD_FLAGS = {
    Qt.KeyboardModifier.ControlModifier: "ctrl",
    Qt.KeyboardModifier.AltModifier: "alt",
    Qt.KeyboardModifier.ShiftModifier: "shift",
    Qt.KeyboardModifier.MetaModifier: "win",
}
# Qt 主键（event.key() 是 Qt.Key）-> keyboard 库键名；键表用于"只按了修饰键"判断
_HOTKEY_MOD_KEYS = {
    Qt.Key_Control: "ctrl",
    Qt.Key_Alt: "alt",
    Qt.Key_Shift: "shift",
    Qt.Key_Meta: "win",
}
# Qt 特殊键 -> keyboard 库键名
_HOTKEY_SPECIAL_KEYS = {
    Qt.Key_Space: "space",
    Qt.Key_Return: "enter",
    Qt.Key_Enter: "enter",
    Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Delete: "delete",
    Qt.Key_Insert: "insert",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "page up",
    Qt.Key_PageDown: "page down",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_CapsLock: "caps lock",
    Qt.Key_NumLock: "num lock",
    Qt.Key_Print: "print screen",
    Qt.Key_Pause: "pause",
}
# 无修饰键也允许单独录制的键（功能键/方向键等，避免误触的普通字母数字除外）
_HOTKEY_BARE_OK = set(_HOTKEY_SPECIAL_KEYS) | {
    Qt.Key_F1 + i for i in range(24)
}


def _hotkey_kb_to_display(kb: str) -> str:
    """keyboard 格式（win+h）-> 显示格式（Win+H）。"""
    parts = [p for p in (kb or "").split("+") if p]
    out = []
    for p in parts:
        if len(p) == 1:
            out.append(p.upper())
        elif p.startswith("f") and p[1:].isdigit():
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:])
    return "+".join(out)


class HotkeyCaptureEdit(QLineEdit):
    """热键按键捕获输入框：点击聚焦后直接按键录制，无需手输。

    显示友好格式（Win+H），text() 返回 keyboard 库格式（win+h）供保存。
    - 组合键：修饰键（Ctrl/Alt/Shift/Win）+ 主键
    - 功能键/方向键可单独录制；普通字母数字必须带修饰键（防打字误触）
    - Esc 取消本次捕获恢复原值；Backspace 清空
    """

    def __init__(self, value: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._kb_value = ""
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setPlaceholderText(t("hint.hotkey_capture"))
        self.set_value(value)

    def set_value(self, kb: str) -> None:
        """设置当前值（keyboard 格式），刷新显示。"""
        self._kb_value = (kb or "").strip().lower()
        super().setText(_hotkey_kb_to_display(self._kb_value))

    def text(self) -> str:
        """覆盖：保存侧读取 keyboard 格式（win+h），而非显示文本。"""
        return self._kb_value

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            # 取消本次录制，恢复原值
            super().setText(_hotkey_kb_to_display(self._kb_value))
            event.accept()
            return
        if key == Qt.Key_Backspace:
            # 清空（恢复"未设置"）
            self._kb_value = ""
            super().setText("")
            event.accept()
            return
        kb = self._compose(event)
        if kb:
            self._kb_value = kb
            super().setText(_hotkey_kb_to_display(kb))
            event.accept()
        else:
            # 无修饰的普通字母/数字：忽略并提示
            self.setPlaceholderText(t("hint.hotkey_need_modifier"))
            event.accept()

    @staticmethod
    def _compose(event) -> str:
        """从按键事件组合 keyboard 格式键名；无效返回空串。"""
        mods = event.modifiers()
        key = event.key()
        # 只按了修饰键本身（等主键）或主键是修饰键
        if key in _HOTKEY_MOD_KEYS:
            return ""
        names = []
        for flag, name in _HOTKEY_MOD_FLAGS.items():
            if mods & flag:
                names.append(name)
        # 主键名
        if key in _HOTKEY_SPECIAL_KEYS:
            name = _HOTKEY_SPECIAL_KEYS[key]
        elif Qt.Key_A <= key <= Qt.Key_Z:
            name = chr(key - Qt.Key_A + ord("a"))
        elif Qt.Key_0 <= key <= Qt.Key_9:
            name = chr(key - Qt.Key_0 + ord("0"))
        elif Qt.Key_F1 <= key <= Qt.Key_F24:
            name = f"f{key - Qt.Key_F1 + 1}"
        else:
            return ""
        # 无修饰键的普通字母/数字拒绝（防打字误触），功能键等允许
        if not names and key not in _HOTKEY_BARE_OK:
            return ""
        # 稳定排序：ctrl, alt, shift, win
        order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
        names.sort(key=lambda n: order.get(n, 9))
        return "+".join(names + [name])


class SliderSpinBox(QWidget):
    """滑块 + 数字框组合：拖滑块快速调，数字框精调，双向同步。

    suffix 为单位后缀（如 " 秒" / " 毫秒" / "%"），跟随数字显示——
    标签因此不再写"（秒）"之类的括注，视觉更接近专业软件的取值控件。
    """

    def __init__(self, lo: int, hi: int, step: int, suffix: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._spin = QSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._spin.setSuffix(suffix)
        # 宽度按后缀自适应（中文单位较宽），至少保持 72px 可读
        self._spin.setMinimumWidth(max(72, self._spin.sizeHint().width() + 6))
        slider = FlatSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setSingleStep(step)
        slider.setPageStep(max(step, (hi - lo) // 20))
        slider.valueChanged.connect(self._spin.setValue)
        self._spin.valueChanged.connect(slider.setValue)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(slider, 1)
        lay.addWidget(self._spin)

    def value(self) -> int:
        return self._spin.value()

    def setValue(self, v) -> None:
        self._spin.setValue(int(v))
