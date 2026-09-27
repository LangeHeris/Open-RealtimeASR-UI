"""历史记录独立窗口：透明圆角玻璃，深色中性配色。

设计要点：
- 窗口即圆角玻璃：无边框 + 半透明底色 + 12px 圆角，四角透明，
  整个窗口统一圆角（不做亚克力——DWM 亚克力铺满矩形区域会让
  外缘变方角，与内层圆角不统一）
- 配色固定深色中性色（不随悬浮条主题明暗变化）：深色玻璃面板 +
  浅色文字，选中条目用白系灰底（不盖文字），任何主题下都通用、可读
- 交互：单击条目即复制全文（无需额外按钮）；标题栏可拖动、
  边缘 8px 自由缩放、右上角关闭按钮；清空历史直接执行无需确认

功能与右键菜单互补：菜单只展示最近 15 条（截断显示），窗口完整显示
全部条目（多行换行看全文）、可搜索定位，新增条目经 core.history
变化回调实时刷新。
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import history as _history
from ..i18n import t
from .icons import app_icon

logger = logging.getLogger(__name__)

_RADIUS = 12        # 窗口圆角
_RESIZE_MARGIN = 8  # 无边框窗口边缘缩放命中带


def _theme_from_cfg(cfg) -> dict:
    """历史窗口固定深色中性配色：通用、可读，不随悬浮条主题明暗变化。

    cfg 仅保留参数位（扩展点），当前不读取任何主题色。
    """
    text = QColor(238, 238, 238)
    secondary = "#9d9da5"
    hover = "rgba(255, 255, 255, 0.08)"
    input_bg = "rgba(255, 255, 255, 0.06)"
    input_border = "rgba(255, 255, 255, 0.14)"
    # 选中条：白系灰底 + 浅色文字（绝不出现系统白条覆盖文字）
    sel = "rgba(255, 255, 255, 0.14)"
    sel_border = "rgba(255, 255, 255, 0.38)"  # 搜索框聚焦描边（中性灰）
    return {
        "text": text, "tint": QColor(28, 28, 32, 238),
        "border": QColor(255, 255, 255, 42),
        "secondary": secondary, "hover": hover, "input_bg": input_bg,
        "input_border": input_border,
        "sel": sel, "sel_border": sel_border,
    }


def _build_qss(palette: dict) -> str:
    """窗口 QSS：全部色值来自深色主题 dict。"""
    sel = palette["sel"]
    sel_border = palette["sel_border"]
    font_stack = ("'Segoe UI Variable Text', 'Segoe UI', "
                  "'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
    return f"""
    QLabel {{ color: {palette['text'].name()}; background: transparent; }}
    QLabel#dialogTitle {{
        color: {palette['text'].name()}; font-size: 11pt;
        font-family: {font_stack}; font-weight: 700; background: transparent;
    }}
    QLabel#countLabel {{
        color: {palette['secondary']}; font-size: 9.5pt; background: transparent;
    }}
    /* 右上角关闭按钮：平时淡色，悬停红底红字 */
    QPushButton#btnClose {{
        background: transparent; border: none; border-radius: 6px;
        color: {palette['secondary']}; font-size: 11pt; padding: 0px;
    }}
    QPushButton#btnClose:hover {{
        background: rgba(200, 60, 60, 0.22); color: #ff6b6b;
    }}
    QPushButton#btnClose:pressed {{ background: rgba(200, 60, 60, 0.32); }}
    /* 搜索框：胶囊圆角，聚焦换主题强调色描边 */
    QLineEdit#searchBox {{
        background: {palette['input_bg']}; border: 1px solid {palette['input_border']};
        border-radius: 15px; padding: 7px 14px; color: {palette['text'].name()};
        selection-background-color: {sel};
        font-family: {font_stack}; font-size: 10.5pt;
    }}
    QLineEdit#searchBox:hover {{ background: {palette['hover']}; }}
    QLineEdit#searchBox:focus {{
        background: {palette['input_bg']}; border: 1px solid {sel_border};
    }}
    /* 列表：透明无边框，圆角条目；选中白系灰底 + 浅色文字（多状态兜底，防系统白条覆盖） */
    QListWidget#historyList {{
        background: transparent; border: none; padding: 2px; outline: none;
        selection-background-color: {sel};
        selection-color: {palette['text'].name()};
        font-family: {font_stack}; font-size: 10.5pt;
    }}
    QListWidget#historyList::item {{
        color: {palette['text'].name()}; border-radius: 8px; padding: 8px 10px;
        margin: 2px 0; background: transparent;
    }}
    QListWidget#historyList::item:hover {{ background: {palette['hover']}; }}
    QListWidget#historyList::item:selected {{
        background: {sel}; color: {palette['text'].name()};
    }}
    QListWidget#historyList::item:selected:active {{
        background: {sel}; color: {palette['text'].name()};
    }}
    QListWidget#historyList::item:selected:!active {{
        background: {sel}; color: {palette['text'].name()};
    }}
    QListWidget#historyList::item:selected:hover {{ background: {sel}; }}
    /* 底部按钮 */
    QPushButton#btnDanger {{
        background: {palette['input_bg']}; border: 1px solid {palette['input_border']};
        border-radius: 13px; padding: 6px 16px; color: {palette['secondary']};
        font-family: {font_stack}; font-size: 10pt;
    }}
    QPushButton#btnDanger:hover {{
        background: rgba(200, 60, 60, 0.22); color: #ff6b6b;
        border-color: rgba(200, 60, 60, 0.4);
    }}
    QPushButton#btnDanger:pressed {{ background: rgba(200, 60, 60, 0.32); }}
    /* 滚动条：细、低调 */
    QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {palette['input_border']}; border-radius: 3px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette['secondary']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{
        background: {palette['input_bg']}; color: {palette['text'].name()};
        border: 1px solid {palette['input_border']}; padding: 6px 10px;
        border-radius: 6px; font-family: {font_stack}; font-size: 10pt;
    }}
    """


class GlassRoot(QWidget):
    """圆角玻璃容器：承载半透明底色 + 细描边（四角透明由窗口提供）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tint = QColor(28, 28, 32, 238)
        self._border = QColor(255, 255, 255, 42)

    def set_glass(self, tint: QColor, border: QColor) -> None:
        self._tint = tint
        self._border = border
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)
        p.fillPath(path, self._tint)
        p.setPen(QPen(self._border, 1))
        p.drawPath(path)


class HistoryWindow(QDialog):
    """历史记录独立窗口（单例复用，由 app 懒创建）。"""

    # core.history 变化回调可能在 ASR/LLM 后台线程触发：
    # 用信号桥接，AutoConnection 跨线程自动排队到 UI 线程再刷新
    _changed = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle(t("hist.title"))
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(340, 320)

        font = QFont()
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI",
                          "Microsoft YaHei UI", "Microsoft YaHei"])
        font.setPointSizeF(10.5)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(font)

        # ---- 圆角玻璃容器（铺满窗口，四角透明） ----
        self._root = GlassRoot(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._root)
        root = QVBoxLayout(self._root)
        root.setContentsMargins(14, 8, 14, 12)
        root.setSpacing(10)

        # ---- 标题栏：左侧标题 + 右上关闭按钮（可拖动） ----
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        title_label = QLabel(t("hist.title"))
        title_label.setObjectName("dialogTitle")
        btn_close = QPushButton("×")
        btn_close.setObjectName("btnClose")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip(t("hist.close"))
        btn_close.clicked.connect(self.close)
        tb.addWidget(title_label)
        tb.addStretch(1)
        tb.addWidget(btn_close)
        root.addWidget(title_bar)

        def _title_press(ev) -> None:
            if ev.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = (
                    ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )

        def _title_move(ev) -> None:
            if self._drag_pos is not None and ev.buttons() & Qt.MouseButton.LeftButton:
                self.move(ev.globalPosition().toPoint() - self._drag_pos)

        def _title_release(ev) -> None:
            self._drag_pos = None

        title_bar.mousePressEvent = _title_press
        title_bar.mouseMoveEvent = _title_move
        title_bar.mouseReleaseEvent = _title_release
        title_bar.setMouseTracking(True)
        self._drag_pos = None

        # ---- 搜索过滤框 ----
        self._search = QLineEdit()
        self._search.setObjectName("searchBox")
        self._search.setPlaceholderText(t("hist.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh)
        root.addWidget(self._search)

        # ---- 条目列表：多行全文显示，单击即复制 ----
        self._list = QListWidget()
        self._list.setObjectName("historyList")
        self._list.setWordWrap(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemClicked.connect(lambda item: self._copy(item.text()))
        self._list.itemActivated.connect(lambda item: self._copy(item.text()))
        root.addWidget(self._list, 1)

        # ---- 底部：计数 + 清空 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._count = QLabel(t("hist.count", count=0))
        self._count.setObjectName("countLabel")
        btn_clear = QPushButton(t("hist.clear"))
        btn_clear.setObjectName("btnDanger")
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(self._on_clear)
        bottom.addWidget(self._count)
        bottom.addStretch(1)
        bottom.addWidget(btn_clear)
        root.addLayout(bottom)

        # 复制反馈：临时改计数标签文案，1.2s 后还原
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._restore_count)

        # 无边框自由拉伸：边缘 8px 命中带 + 系统级缩放（复刻设置弹窗方案）
        self._resize_margin = _RESIZE_MARGIN
        self.setMouseTracking(True)
        self.installEventFilter(self)
        self._root.setMouseTracking(True)
        self._root.installEventFilter(self)
        title_bar.installEventFilter(self)

        self._changed.connect(self._refresh)
        self._on_history_changed = self._changed.emit
        self._apply_theme()
        _history.add_listener(self._on_history_changed)
        self._refresh()

    # ---- 主题 ----

    def _apply_theme(self) -> None:
        """应用深色中性配色（固定，不随悬浮条主题明暗变化）。"""
        # 局部名不用 t：本模块 import 了 i18n 的 t()，函数内绑定 t 会让
        # 整个函数作用域的 t 变成局部名，同函数内 t("key") 会 UnboundLocalError。
        palette = _theme_from_cfg(self._cfg)
        self._t = palette
        self.setStyleSheet(_build_qss(palette))
        self._root.set_glass(palette["tint"], palette["border"])

    # ---- 生命周期 ----

    def showEvent(self, ev) -> None:
        """每次打开都重新注册监听、重读主题并加载最新数据。"""
        super().showEvent(ev)
        _history.add_listener(self._on_history_changed)
        self._apply_theme()
        self._refresh()

    def closeEvent(self, ev) -> None:
        _history.remove_listener(self._on_history_changed)
        super().closeEvent(ev)

    # ---- 无边框窗口：缩放 ----

    def eventFilter(self, obj, ev) -> bool:
        """边缘缩放过滤器：悬停显示方向箭头，左键按住走系统级缩放。

        与设置弹窗同方案：命中边缘时吞掉按下事件（返回 True），
        标题栏拖动不与顶部边缘拉伸冲突。
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
        """窗口坐标命中哪条边缘（角优先）；不贴边返回 None。"""
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
        """边缘（含角组合）→ 对应方向的双向箭头光标。"""
        if edge in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        if edge in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edge in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                    Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    # ---- 列表刷新 ----

    def _visible_texts(self) -> list[str]:
        """当前应显示的条目（搜索过滤后，最新在前）。"""
        q = self._search.text().strip().lower()
        items = _history.entries()
        if q:
            items = [entry for entry in items if q in entry.lower()]
        return items

    def _item_size(self, text: str) -> QSize:
        """按当前视口宽度计算条目高度（多行全文显示）。"""
        w = max(self._list.viewport().width() - 28, 80)
        rect = self._list.fontMetrics().boundingRect(
            0, 0, w, 0, Qt.TextFlag.TextWordWrap, text
        )
        return QSize(w, max(rect.height() + 18, 34))

    def _refresh(self) -> None:
        """重载列表：尽量保持选中行与滚动位置（新条目插最前会自然偏移）。"""
        texts = self._visible_texts()
        current = self._list.currentRow()
        scroll = self._list.verticalScrollBar().value()
        self._list.clear()
        for text in texts:
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setSizeHint(self._item_size(text))
            self._list.addItem(item)
        if texts:
            self._list.setCurrentRow(min(current, len(texts) - 1))
            self._list.verticalScrollBar().setValue(scroll)
        self._count.setText(t("hist.count", count=len(texts)))

    def _restore_count(self) -> None:
        """复制提示结束后还原计数文案。"""
        self._count.setText(t("hist.count", count=len(self._visible_texts())))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setSizeHint(self._item_size(item.text()))

    # ---- 交互 ----

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self._count.setText(t("hist.copied"))
        self._hint_timer.start(1200)

    def _on_clear(self) -> None:
        """清空历史：直接执行，无需确认（列表与计数即时刷新）。"""
        _history.clear()
