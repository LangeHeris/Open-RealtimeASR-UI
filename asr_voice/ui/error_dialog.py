"""启动期致命错误弹窗：Fluent 风格圆角卡片，与欢迎页/设置界面同视觉血统。

替代 Win32 原生 MessageBoxW（系统标题栏 + 灰底 + 老式按钮，与应用自身的
Win11 风格观感割裂）。无边框圆角卡片 + 严重度图标 + 主按钮；明暗跟随设置
界面主题（ui.settings_style），配置不可用时（本弹窗常用于配置加载失败这类
启动期致命错误）回退默认主题。

文案全部走 i18n 词条或由调用方传入，本模块不含硬编码用户可见文本。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from .settings_styles import _error_qss, _theme_palette


class _SeverityIcon(QWidget):
    """严重度图标：红底白叉，QPainter 自绘（不引入额外资源文件）。"""

    def __init__(self, side: int = 38, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(side, side)

    def paintEvent(self, event) -> None:  # noqa: N802  Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d13438"))
        p.drawEllipse(0, 0, side, side)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(max(2.0, side * 0.10))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = side * 0.33
        p.drawLine(int(m), int(m), int(side - m), int(side - m))
        p.drawLine(int(side - m), int(m), int(m), int(side - m))


class ErrorDialog(QDialog):
    """模态错误卡片：图标 + 标题 + 正文 + 主按钮；Esc / 回车等价于确认。"""

    def __init__(self, message: str, title: str = "Open-RealtimeASR-UI",
                 cfg=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errDlg")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog
                            | Qt.WindowType.Tool)
        self.setModal(True)
        self.setStyleSheet(_error_qss(_theme_palette(cfg)))

        panel = QWidget(self)
        panel.setObjectName("errPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        v = QVBoxLayout(panel)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(_SeverityIcon(38))
        title_label = QLabel(title)
        title_label.setObjectName("errTitle")
        head.addWidget(title_label, 1)
        v.addLayout(head)
        v.addSpacing(12)

        body = QLabel(message)
        body.setObjectName("errBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(body)
        v.addSpacing(20)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton(t("common.def.ok"))
        ok.setObjectName("errOk")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setFixedSize(104, 34)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(ok)
        v.addLayout(btns)

        self.setMinimumWidth(440)
        self.setMaximumWidth(560)

    def keyPressEvent(self, event) -> None:  # noqa: N802  Qt 命名
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)
