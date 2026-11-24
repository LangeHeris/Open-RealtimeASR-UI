"""启动期致命错误弹窗：Fluent 风格圆角卡片，与欢迎页/设置界面同视觉血统。

替代 Win32 原生 MessageBoxW（系统标题栏 + 灰底 + 老式按钮，与应用自身的
Win11 风格观感割裂）。无边框圆角卡片 + 严重度图标 + 主按钮；明暗跟随设置
界面主题（ui.settings_style），配置不可用时（本弹窗常用于配置加载失败这类
启动期致命错误）回退默认主题。

文案全部走 i18n 词条或由调用方传入，本模块不含硬编码用户可见文本。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
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
    """严重度图标：主题红调圆角瓷砖 + 感叹号，QPainter 自绘（不引入资源文件）。"""

    def __init__(self, side: int = 36, color: QColor | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color) if color is not None else QColor("#d13438")
        self.setFixedSize(side, side)

    def paintEvent(self, event) -> None:  # noqa: N802 Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        # 淡色底（同色 14%）：比大红实心块柔和，靠形状+符号表达严重度
        bg = QColor(self._color)
        bg.setAlphaF(0.14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(0, 0, side, side, 11, 11)
        # 感叹号：上竖线 + 下圆点
        pen = QPen(self._color)
        pen.setWidthF(max(2.0, side * 0.09))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cx = side / 2.0
        p.drawLine(int(cx), int(side * 0.30), int(cx), int(side * 0.58))
        r = max(1.5, side * 0.05)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(cx - r, side * 0.72 - r, r * 2, r * 2))


class ErrorDialog(QDialog):
    """模态错误卡片：图标 + 标题 + 原因 + 操作提示 + 主按钮；Esc / 回车等价于确认。

    message 以空行分为两段时，第一段是原因（正文），其余作为操作提示
    渲染成独立的浅色小卡——与设置界面的字段说明块同一视觉语言。
    """

    def __init__(self, message: str, title: str = "Open-RealtimeASR-UI",
                 cfg=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errDlg")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog
                            | Qt.WindowType.Tool)
        self.setModal(True)
        palette = _theme_palette(cfg)
        self.setStyleSheet(_error_qss(palette))

        panel = QWidget(self)
        panel.setObjectName("errPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        v = QVBoxLayout(panel)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(_SeverityIcon(36, QColor(palette["danger"])))
        title_label = QLabel(title)
        title_label.setObjectName("errTitle")
        head.addWidget(title_label, 1)
        v.addLayout(head)
        v.addSpacing(12)

        parts = [s.strip() for s in message.split("\n\n") if s.strip()]
        body = QLabel(parts[0] if parts else message)
        body.setObjectName("errBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(body)

        hint_text = "\n\n".join(parts[1:])
        if hint_text:
            v.addSpacing(10)
            hint_card = QWidget()
            hint_card.setObjectName("errHintCard")
            hint_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            hv = QVBoxLayout(hint_card)
            hv.setContentsMargins(12, 10, 12, 10)
            hint = QLabel(hint_text)
            hint.setObjectName("errHint")
            hint.setWordWrap(True)
            hint.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            hv.addWidget(hint)
            v.addWidget(hint_card)

        v.addSpacing(20)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton(t("common.def.ok"))
        ok.setObjectName("errOk")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setFixedHeight(34)
        ok.setMinimumWidth(88)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(ok)
        v.addLayout(btns)

        self.setMinimumWidth(440)
        self.setMaximumWidth(560)

    def keyPressEvent(self, event) -> None:  # noqa: N802 Qt 命名
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)


class HotkeyConflictDialog(QDialog):
    """启动期热键被占用：在弹窗内直接录制新组合并重试注册。

    retry(new_hotkey) 由调用方提供：返回 None 表示注册成功；返回错误文案
    则就地提示、留在弹窗里继续改。成功前不写配置，避免把不可用的热键落盘。
    """

    def __init__(self, hotkey: str, retry,
                 cfg=None, parent: QWidget | None = None) -> None:
        from .settings_widgets import HotkeyCaptureEdit, _hotkey_kb_to_display

        super().__init__(parent)
        self._retry = retry
        self.setObjectName("errDlg")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog
                            | Qt.WindowType.Tool)
        self.setModal(True)
        palette = _theme_palette(cfg)
        self.setStyleSheet(_error_qss(palette))

        panel = QWidget(self)
        panel.setObjectName("errPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        v = QVBoxLayout(panel)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(_SeverityIcon(36, QColor(palette["danger"])))
        title_label = QLabel(t("app.err.hotkey_conflict_title"))
        title_label.setObjectName("errTitle")
        head.addWidget(title_label, 1)
        v.addLayout(head)
        v.addSpacing(12)

        body = QLabel(t("app.err.hotkey_conflict_body",
                        hotkey=_hotkey_kb_to_display(hotkey)))
        body.setObjectName("errBody")
        body.setWordWrap(True)
        v.addWidget(body)
        v.addSpacing(18)

        field_label = QLabel(t("app.err.hotkey_field"))
        field_label.setObjectName("errFieldLabel")
        v.addWidget(field_label)
        v.addSpacing(6)

        self._edit = HotkeyCaptureEdit(hotkey)
        self._edit.setFixedHeight(38)
        v.addWidget(self._edit)
        v.addSpacing(6)

        hint = QLabel(t("app.err.hotkey_input_hint"))
        hint.setObjectName("errHintLine")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._inline = QLabel()
        self._inline.setObjectName("errInline")
        self._inline.setWordWrap(True)
        self._inline.setVisible(False)
        v.addSpacing(8)
        v.addWidget(self._inline)
        v.addSpacing(18)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        cancel = QPushButton(t("app.err.quit"))
        cancel.setObjectName("errGhost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setFixedHeight(34)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch(1)
        self._ok = QPushButton(t("app.err.hotkey_retry"))
        self._ok.setObjectName("errOk")
        self._ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok.setFixedHeight(34)
        self._ok.setMinimumWidth(104)
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._on_retry)
        btns.addWidget(self._ok)
        v.addLayout(btns)

        self.setMinimumWidth(480)
        self.setMaximumWidth(600)
        self._edit.setFocus()

    def hotkey_value(self) -> str:
        return self._edit.text().strip().lower()

    def _show_inline(self, message: str) -> None:
        self._inline.setText(message)
        self._inline.setVisible(True)
        self._edit.setFocus()
        self.adjustSize()

    def _on_retry(self) -> None:
        value = self.hotkey_value()
        if not value:
            self._show_inline(t("app.err.hotkey_empty"))
            return
        self._inline.setVisible(False)
        self._ok.setEnabled(False)
        self._ok.setText(t("app.err.hotkey_retrying"))
        error = ""
        try:
            error = self._retry(value) or ""
        except Exception as exc:
            error = str(exc)
        finally:
            self._ok.setEnabled(True)
            self._ok.setText(t("app.err.hotkey_retry"))
        if error:
            self._show_inline(error)
            return
        self.accept()
