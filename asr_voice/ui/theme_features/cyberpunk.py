"""赛博朋克风主题特性：切角按钮、HUD 描边、顶部霓虹线与专属样式。

从 floating_bar.py / context_menu.py / bar_widgets.py 迁出的主题专属画法。
精简构建（lite）物理排除本模块（见 .spec）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygon, QRegion

# 录音中停止图标专用警红
STOP_ICON_COLOR = "#FF2E4C"

# 长按准备中（spec §3.2.1）的切角边框色：暗红，与录音警红拉开——准备中可能被
# 撤销（轻点丢弃），用录音色会"红一下又消失"，像出错
ARMED_FRAME_COLOR = "#a03c3c"

# 按钮左下切角尺寸
_MASK_CUT = 4
# 悬浮窗左下切角尺寸
_FRAME_CUT = 10


def apply_icons(bar) -> None:
    """启停按钮图标：播放=主题文字色（霓虹青），停止=警红。"""
    from ..icons import play_icon, stop_icon
    bar._btn_toggle.setIconSize(QSize(18, 18))
    bar._play_icon = play_icon(bar._text_color.name())
    bar._stop_icon = stop_icon(STOP_ICON_COLOR)


def apply_toggle_style(bar) -> None:
    """启停按钮：HUD 风细描边 + 左下切角。"""
    border_color = bar._text_color.name()  # 与悬浮窗统一的霓虹色
    c = QColor(border_color)
    tint = f"rgba({c.red()},{c.green()},{c.blue()},12)"
    tint_hover = f"rgba({c.red()},{c.green()},{c.blue()},32)"
    tint_pressed = f"rgba({c.red()},{c.green()},{c.blue()},58)"
    bar._btn_toggle.setStyleSheet(f"""
        QPushButton {{ background: {tint}; border: 1px solid {border_color}; border-radius: 0px; }}
        QPushButton:hover {{ background: {tint_hover}; border: 1px solid {border_color}; }}
        QPushButton:pressed {{ background: {tint_pressed}; border: 1px solid {border_color}; }}
    """)
    update_mask(bar._btn_toggle)


def update_mask(btn) -> None:
    """按钮左下切角 mask：矩形减去一个 4×4 三角形。"""
    w, h = btn.width(), btn.height()
    if w == 0 or h == 0:
        return
    cut = _MASK_CUT
    rect_region = QRegion(0, 0, w, h)
    # 三角三个点：(0, h-cut), (cut, h), (0, h)
    tri = QPolygon([QPoint(0, h - cut), QPoint(cut, h), QPoint(0, h)])
    btn.setMask(rect_region.subtracted(QRegion(tri)))


def draw_frame(painter, rect, bar) -> None:
    """悬浮窗描边：霓虹细边 + 左下切角；长按准备中改暗红。

    该主题的既有语言就是"边框/霓虹线取色"，准备中沿用同一条边、只换色
    （不新增视觉元素），见 spec §3.2.1。
    """
    # 函数内 import：避开「floating_bar -> 本包 -> floating_bar」的模块级循环
    from ..floating_bar import UI_ARMED
    border_color = QColor(ARMED_FRAME_COLOR if bar._state == UI_ARMED else bar._text_color)
    border_color.setAlpha(170)
    cut = _FRAME_CUT
    path = QPainterPath()
    path.moveTo(rect.left(), rect.top())
    path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom())
    path.lineTo(rect.left() + cut, rect.bottom())
    path.lineTo(rect.left(), rect.bottom() - cut)
    path.closeSubpath()
    painter.setPen(QPen(border_color, 1))
    painter.drawPath(path)


def draw_top_line(painter, bar) -> None:
    """顶部霓虹横线：用 fillRect 而非 QPen（pen 画线端点外扩半笔宽会超界）。
    实线下方 2px 半透明做发光层。"""
    glow = QColor(bar._top_line_color)
    glow.setAlpha(100)
    painter.fillRect(0, 2, bar.width(), 2, glow)
    # 实线层：顶部 2px 不透明，x∈[0,width) 精确贴合
    painter.fillRect(0, 0, bar.width(), 2, bar._top_line_color)


def confirm_fill(bar) -> tuple:
    """确认按钮条填充/文字色：霓虹青实底 + 白字。"""
    return QColor(bar._text_color), QColor("#ffffff")


def menu_qss(bar, font_stack: str) -> str:
    """右键菜单：主题色描边/分隔线 + 强调色选中。"""

    def _rgba(c: QColor, alpha: int) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"

    bg = bar._bg_color.name()
    text = bar._text_color.name()
    accent = bar._accent_color.name()
    top = bar._top_line_color.name()
    menu_bg = _rgba(bar._bg_color, 215)
    return f"""
        QMenu {{
            {font_stack}
            background: {menu_bg}; color: {text};
            border: 1px solid {top}; border-radius: 8px; padding: 6px;
        }}
        QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {accent}; color: {bg}; }}
        QMenu::item:disabled {{ color: {top}; font-weight: bold; font-size: 13px; padding: 8px 12px 2px; }}
        QMenu::separator {{ height: 1px; background: {top}; margin: 4px 8px; }}
    """
