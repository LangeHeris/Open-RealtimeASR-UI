"""自绘矢量图标。用 QPainter 生成，与浮动条暗色风格统一。

图标列表：
- gear: 齿轮（配置）
- close: X 形（退出）
- play: 三角形（开始）
- stop: 正方形（停止）
- mic: 麦克风（托盘）
- spinner: 加载中转圈弧线（模型加载期间悬浮条状态点用，可旋转）
- uac_shield: UAC 安全盾牌（蓝黄四象限，设置页永久提权按钮用）
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _blank(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return pixmap


def gear_icon(color: str = "#b8b8c0", size: int = 18) -> QIcon:
    """齿轮图标（配置）。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    cx = cy = size / 2
    body_r = size * 0.30
    tooth_r = size * 0.11
    hole_r = size * 0.13

    body = QPainterPath()
    teeth = 8
    for i in range(teeth):
        angle = 2 * math.pi * i / teeth
        tx = cx + body_r * math.cos(angle)
        ty = cy + body_r * math.sin(angle)
        body.addEllipse(QPointF(tx, ty), tooth_r, tooth_r)
    body.addEllipse(QPointF(cx, cy), body_r, body_r)

    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, cy), hole_r, hole_r)

    painter.fillPath(body.subtracted(hole), QColor(color))
    painter.end()
    return QIcon(pixmap)


def close_icon(color: str = "#b8b8c0", size: int = 18,
               outline: str | None = None) -> QIcon:
    """X 形退出图标。outline 非空时先画粗描边层再画主色层，保证叠在花纹上仍清晰。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    m = size * 0.3
    lines = ((m, m, size - m, size - m), (size - m, m, m, size - m))
    if outline is not None:
        pen = QPen(QColor(outline), 3.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        for x1, y1, x2, y2 in lines:
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    for x1, y1, x2, y2 in lines:
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
    painter.end()
    return QIcon(pixmap)


def play_icon(color: str = "#e8e8e8", size: int = 18) -> QIcon:
    """三角形播放图标（开始）。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    path = QPainterPath()
    path.moveTo(size * 0.3, size * 0.22)
    path.lineTo(size * 0.76, size * 0.5)
    path.lineTo(size * 0.3, size * 0.78)
    path.closeSubpath()
    painter.fillPath(path, QColor(color))
    painter.end()
    return QIcon(pixmap)


def uac_shield_icon(size: int = 16) -> QIcon:
    """Windows UAC 安全盾牌（蓝黄四象限，永久提权按钮左侧用）。

    同时产出 size 与 2x 两档位图，高 DPI 下不糊。
    """
    icon = QIcon()
    for s in (size, size * 2):
        pixmap = _blank(s)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # 盾形：顶边微弧，左右两侧内收曲线汇聚到底部尖角
        shield = QPainterPath()
        shield.moveTo(s * 0.10, s * 0.08)
        shield.quadTo(s * 0.50, s * 0.02, s * 0.90, s * 0.08)
        shield.quadTo(s * 0.88, s * 0.58, s * 0.50, s * 0.95)
        shield.quadTo(s * 0.12, s * 0.58, s * 0.10, s * 0.08)
        shield.closeSubpath()

        # 整盾铺蓝，再裁出右上/左下两象限填黄（经典 UAC 配色）
        painter.fillPath(shield, QColor("#1c62b9"))
        painter.setClipPath(shield)
        painter.fillRect(QRectF(s * 0.5, 0, s * 0.5, s * 0.5), QColor("#ffb900"))
        painter.fillRect(QRectF(0, s * 0.5, s * 0.5, s * 0.5), QColor("#ffb900"))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def stop_icon(color: str = "#e8e8e8", size: int = 18) -> QIcon:
    """正方形停止图标。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    m = size * 0.25
    s = size - 2 * m
    painter.fillRect(QRectF(m, m, s, s), QColor(color))
    painter.end()
    return QIcon(pixmap)


def spinner_pixmap(size: int = 10, color: str = "#5696e8",
                   angle_deg: float = 0.0) -> QPixmap:
    """加载中 spinner（QPixmap，可旋转）。

    size: 输出边长。color: 弧线颜色。angle_deg: 顺时针角度；固定画布绕中心
    旋转，不用 transformed()（会扩画布再压缩，观感缩放）。
    形状是一段约 270° 圆弧 + 圆头笔帽，随角度转起来即"转圈加载"效果。
    """
    # size<4 时弧半径为负或非正，QPainter 会发 begin/参数告警：退回透明空白图
    if size < 4:
        return _blank(max(1, size))
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 线宽约 size 的 1/10，最细保底 1px；弧半径留出线宽一半，避免裁边
    pen_w = max(1.0, size / 10.0)
    pen = QPen(QColor(color), pen_w)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)

    arc_r = size / 2 - pen_w / 2 - 1.0
    painter.translate(size / 2, size / 2)
    painter.rotate(angle_deg)
    # drawArc 角度单位 1/16 度，0° 在 3 点钟方向、正角逆时针
    painter.drawArc(QRectF(-arc_r, -arc_r, arc_r * 2, arc_r * 2), 45 * 16, 270 * 16)
    painter.end()
    return pixmap


# ---- 程序图标 ----
# Open-RealtimeASR-UI 声浪图标（app_icon.png）；exe 用同图生成的 app_icon.ico（见 .spec）

_APP_ICON: QIcon | None = None


def app_icon() -> QIcon:
    """程序图标（托盘、悬浮条、设置对话框等所有窗口共用）。"""
    global _APP_ICON
    if _APP_ICON is None:
        _APP_ICON = QIcon(str(_ASSETS_DIR / "app_icon.png"))
    return _APP_ICON


# ---- 极简（声纹）主题图标 ----
# 原图深灰圆环+波形，背景是导出时烙进像素的近白棋盘格。
# 运行期一次性处理并缓存：亮度转 alpha（亮→透明、暗→不透明），
# 线条提亮成米白，图案包围盒铺满输出边长。

_VOICE_SHAPE: dict[str, tuple[QImage, QRect]] = {}
_VOICE_CACHE: dict[str, QPixmap] = {}

# 棋盘亮档 ≈243，≥此值全透明；线条亮度 48~96，中间线性过渡
_BG_LUM = 243.0
_LINE_SPAN = 150.0


def _voice_source(name: str) -> QPixmap:
    """加载声纹图标原图，缓存单例。"""
    pix = QPixmap(str(_ASSETS_DIR / name))
    if pix.isNull():
        # 资源缺失时回退空白（极端情况，打包完整性保证存在）
        pix = _blank(64)
    return pix


def _extract_art(src: QPixmap) -> tuple[QImage, QRect]:
    """亮度→alpha 剥棋盘底：输出纯白形状图（alpha=形状）+ 图案包围盒。"""
    img = src.toImage().convertToFormat(QImage.Format_ARGB32)
    if img.isNull():
        return QImage(), QRect(0, 0, 0, 0)
    w, h = img.width(), img.height()
    try:
        import numpy as np
        a = np.frombuffer(img.constBits(), np.uint8, count=w * h * 4).reshape(h, w, 4)
        lum = a[:, :, :3].astype(np.float32).mean(axis=2)
        alpha = np.clip((_BG_LUM - lum) * (255.0 / _LINE_SPAN), 0, 255).astype(np.uint8)
        # 棋盘亮档自身噪点（240~242）会产生 alpha 1~3 的残留，统一清零
        alpha[alpha < 20] = 0
        ys, xs = np.nonzero(alpha > 16)
        if len(xs) == 0:
            bbox = QRect(0, 0, w, h)
        else:
            bbox = QRect(int(xs.min()), int(ys.min()),
                         int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        # 内存序 BGRA（ARGB32 小端）
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 0] = 255
        rgba[:, :, 1] = 255
        rgba[:, :, 2] = 255
        rgba[:, :, 3] = alpha
        shape = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format_ARGB32).copy()
        return shape, bbox
    except Exception:
        # numpy 不可用：退回未处理原图（显示退化但不崩溃）
        return img, QRect(0, 0, w, h)


def _voice_shape(recording: bool) -> tuple[QImage, QRect]:
    """取（并缓存）声纹形状图与图案包围盒。"""
    key = "on" if recording else "off"
    cached = _VOICE_SHAPE.get(key)
    if cached is not None:
        return cached
    name = "voice_recording.png" if recording else "voice_idle.png"
    result = _extract_art(_voice_source(name))
    _VOICE_SHAPE[key] = result
    return result


def voice_icon(recording: bool, size: int, color: str = "#eeeeee") -> QPixmap:
    """极简主题声纹图标，按 (状态, 尺寸, 颜色) 缓存。

    录音中用主题色、待机用文字色；图案包围盒铺满输出，圆环即窗缘。
    """
    key = ("on" if recording else "off", size, color)
    cached = _VOICE_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    shape, bbox = _voice_shape(recording)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    if not shape.isNull() and bbox.width() > 0:
        # 重着色：整幅填目标色，形状 alpha 做 DestinationIn 遮罩
        tint = QImage(shape.width(), shape.height(), QImage.Format_ARGB32)
        tint.fill(QColor(color))
        tp = QPainter(tint)
        tp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        tp.drawImage(0, 0, shape)
        tp.end()
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QRect(0, 0, size, size), QPixmap.fromImage(tint), bbox)
        p.end()
    _VOICE_CACHE[key] = out
    return out


def globe_icon(color: str = "#b8b8c0", size: int = 20) -> QPixmap:
    """地球图标（欢迎框「离线引擎仅中文」卡）。返回 QPixmap 供 QLabel 直接贴。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), max(1.2, size * 0.09))
    painter.setPen(pen)
    m = size * 0.12
    painter.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
    # 经线：竖椭圆压窄，与外圆同高
    painter.drawEllipse(QRectF(size / 2 - size * 0.22, m, size * 0.44, size - 2 * m))
    # 纬线：赤道横线
    painter.drawLine(QPointF(m, size / 2), QPointF(size - m, size / 2))
    painter.end()
    return pixmap


def key_icon(color: str = "#b8b8c0", size: int = 20) -> QPixmap:
    """钥匙图标（欢迎框「云端引擎（可选）」卡）。返回 QPixmap 供 QLabel 直接贴。"""
    pixmap = _blank(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), max(1.2, size * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    r = size * 0.17
    cy = size * 0.40
    cx = size * 0.13 + r
    # 钥匙头：左上圆环
    painter.drawEllipse(QRectF(size * 0.13, cy - r, 2 * r, 2 * r))
    # 钥匙杆：水平向右
    painter.drawLine(QPointF(cx + r, cy), QPointF(size * 0.90, cy))
    # 两齿：杆下短竖线
    painter.drawLine(QPointF(size * 0.66, cy), QPointF(size * 0.66, cy + size * 0.20))
    painter.drawLine(QPointF(size * 0.86, cy), QPointF(size * 0.86, cy + size * 0.20))
    painter.end()
    return pixmap
