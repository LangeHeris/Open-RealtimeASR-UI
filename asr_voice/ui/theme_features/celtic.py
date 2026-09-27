"""凯尔特风格主题特性：凯尔特结图标 + 链纹边框的绘制。

素材随主题包分发（themes/凯尔特风格/assets/），路径由注册表在加载主题包时
通过 set_pack_dir 注入；素材缺失一律回退透明空图，不崩溃。

从 icons.py 迁出的绘制引擎 + 从 floating_bar.py 迁出的图标/旋转/边框集成。
精简构建（lite）物理排除本模块（见 .spec）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

logger = logging.getLogger(__name__)

# 主题包素材目录（注册表加载凯尔特包时注入）；None = 素材不可用
_ASSETS_DIR: Path | None = None


def set_pack_dir(pack_dir: Path) -> None:
    """注册表加载主题包时注入素材目录。"""
    global _ASSETS_DIR
    _ASSETS_DIR = Path(pack_dir) / "assets"


def _assets_dir() -> Path | None:
    return _ASSETS_DIR


def _blank(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return pixmap


# ---- 凯尔特结图标（可旋转） ----

_KNOT_SOURCE: QPixmap | None = None


def _knot_source() -> QPixmap:
    """加载凯尔特结原图，缓存单例。"""
    global _KNOT_SOURCE
    if _KNOT_SOURCE is not None and not _KNOT_SOURCE.isNull():
        return _KNOT_SOURCE
    assets = _assets_dir()
    pix = QPixmap(str(assets / "knot_icon.png")) if assets else QPixmap()
    if pix.isNull():
        # 资源缺失时回退一个空透明图，避免崩溃
        pix = _blank(64)
    _KNOT_SOURCE = pix
    return pix


def knot_pixmap(size: int = 24, color: str | None = None,
                angle_deg: float = 0.0) -> QPixmap:
    """凯尔特结图标（QPixmap，可旋转）。

    size: 输出边长，旋转不缩放。color: 原图白底黑线，亮度 < 128 的像素填目标色、其余透明。
    angle_deg: 顺时针角度；固定画布中心旋转，不用 transformed()（会扩画布再压缩，观感缩放）。
    """
    src = _knot_source()
    # 先着色再旋转
    scaled = src.scaled(
        size, size,
        Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    if color is not None:
        img = scaled.toImage().convertToFormat(QImage.Format_ARGB32)
        tgt = QColor(color)
        tgt_r, tgt_g, tgt_b = tgt.red(), tgt.green(), tgt.blue()
        w, h = img.width(), img.height()
        for y in range(h):
            scan = img.scanLine(y)  # memoryview，覆盖整行字节
            for x in range(w):
                off = x * 4
                b, g, r = scan[off], scan[off + 1], scan[off + 2]
                # 黑白分明：硬阈值，无半透明过渡
                if (r + g + b) // 3 < 128:
                    scan[off] = tgt_b
                    scan[off + 1] = tgt_g
                    scan[off + 2] = tgt_r
                    scan[off + 3] = 255
                else:
                    scan[off + 3] = 0
        scaled = QPixmap.fromImage(img)
    if angle_deg:
        # 固定画布绕中心旋转（transformed() 会扩画布，弃用）
        out = QPixmap(size, size)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(size / 2, size / 2)
        painter.rotate(angle_deg)
        painter.drawPixmap(-scaled.width() / 2, -scaled.height() / 2, scaled)
        painter.end()
        # 旋转混叠会引入半透明，再二值化一次：alpha > 128 -> 255
        img2 = out.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(size):
            scan = img2.scanLine(y)
            for x in range(size):
                off = x * 4
                scan[off + 3] = 255 if scan[off + 3] > 128 else 0
        return QPixmap.fromImage(img2)
    return scaled


def knot_icon(color: str | None = None, size: int = 18) -> QIcon:
    """返回 QIcon 形式的凯尔特结（用于非动画场景，录音中请用 pixmap）。"""
    return QIcon(knot_pixmap(size=size, color=color))


# ---- 链纹边框（九宫格直角版） ----
# 素材白底黑线，亮度转 alpha 后按需着色。
# 切片按编辫周期对齐（横向 53px、纵向 50px），四角与边条相位连续；
# 边条按周期平铺、超出回绕，任意尺寸下编辫密度不变。

_KNOT_PARTS: dict[str, QImage] = {}
_KNOT_RECOLORED: dict[tuple[str, str], QImage] = {}

# 链带显示带宽 px，素材厚 73px，缩放比 s = KNOT_BAND/73。13px 为实测调细后的值。
KNOT_BAND = 13.0
_KNOT_SRC_BAND = 73.0    # 素材链带厚度（实测）
_KNOT_PERIOD_H = 53.0    # 横带编辫周期（源图 px，实测）
_KNOT_PERIOD_V = 50.0    # 竖带编辫周期（源图 px，实测）


def _knot_part(name: str) -> QImage:
    """加载九宫格部件（白色内容 + alpha 遮罩），缓存单例。"""
    img = _KNOT_PARTS.get(name)
    if img is None:
        assets = _assets_dir()
        pix = QPixmap(str(assets / f"knot_{name}.png")) if assets else QPixmap()
        img = (pix.toImage().convertToFormat(QImage.Format_ARGB32)
               if not pix.isNull() else QImage())
        _KNOT_PARTS[name] = img
    return img


def _knot_tinted(name: str, color: str) -> QImage:
    """部件重着色：整幅填目标色，再用 alpha 做 DestinationIn 遮罩，按 (name, color) 缓存。"""
    key = (name, color)
    img = _KNOT_RECOLORED.get(key)
    if img is not None:
        return img
    src = _knot_part(name)
    if src.isNull():
        return src
    img = QImage(src.width(), src.height(), QImage.Format_ARGB32)
    img.fill(QColor(color))
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, src)
    p.end()
    _KNOT_RECOLORED[key] = img
    return img


def knot_border_metrics() -> tuple[float, float]:
    """(上, 下) 链带显示厚度，供悬浮条算内容区最小高度。"""
    s = KNOT_BAND / _KNOT_SRC_BAND
    top = _knot_part("top")
    bottom = _knot_part("bottom")
    t = top.height() * s if not top.isNull() else KNOT_BAND
    b = bottom.height() * s if not bottom.isNull() else KNOT_BAND
    return t, b


def knot_border_pixmap(w: int, h: int, color: str = "#ffffff") -> QPixmap:
    """凯尔特链纹边框：四角结饰 + 四边编辫平铺，贴窗口边缘。

    边条按周期分段、超出回绕，零头用微拉伸吸收，任意尺寸编辫密度不变；
    窗高不足时上下角在中线对切（正常最小高度不会触发）。
    """
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    s = KNOT_BAND / _KNOT_SRC_BAND

    def size(name: str) -> tuple[float, float]:
        img = _knot_tinted(name, color)
        return (0.0, 0.0) if img.isNull() else (img.width() * s, img.height() * s)

    def draw(name: str, dx: float, dy: float, dw: float, dh: float,
             sx: float = 0.0, sy: float = 0.0,
             sw: float = -1.0, sh: float = -1.0) -> None:
        img = _knot_tinted(name, color)
        if img.isNull() or dw <= 0 or dh <= 0:
            return
        if sw < 0:
            sw, sh = img.width(), img.height()
        p.drawImage(QRectF(dx, dy, dw, dh), img, QRectF(sx, sy, sw, sh))

    tl_w, tl_h = size("tl")
    tr_w, tr_h = size("tr")
    bl_w, bl_h = size("bl")
    br_w, br_h = size("br")

    # 垂直空间分配：两角放得下就完整显示，放不下则上下角在中线对切
    if max(tl_h + bl_h, tr_h + br_h) <= h:
        tl_vis_h, tr_vis_h, bl_vis_h, br_vis_h = tl_h, tr_h, bl_h, br_h
    else:
        tl_vis_h = tr_vis_h = bl_vis_h = br_vis_h = h / 2
    top_vis = max(tl_vis_h, tr_vis_h)   # 左右边条起点 y
    bot_vis = max(bl_vis_h, br_vis_h)   # 下角可见高度（锚底绘制）

    # 上/下边条：x ∈ [tl_w, w-tr_w]，按周期分段平铺（相位对齐）
    top_img = _knot_tinted("top", color)
    bot_img = _knot_tinted("bottom", color)
    bt = top_img.height() * s if not top_img.isNull() else 0.0
    bb = bot_img.height() * s if not bot_img.isNull() else 0.0
    avail_h = w - tl_w - tr_w
    if avail_h > 2 and not top_img.isNull():
        reps_h = max(1, int(top_img.width() // _KNOT_PERIOD_H))
        n = max(1, round(avail_h / (_KNOT_PERIOD_H * s)))
        seg = avail_h / n
        for i in range(n):
            sx = (i % reps_h) * _KNOT_PERIOD_H
            x = tl_w + i * seg
            draw("top", x, 0, seg, bt, sx, 0, _KNOT_PERIOD_H, top_img.height())
            draw("bottom", x, h - bb, seg, bb,
                 sx, 0, _KNOT_PERIOD_H, bot_img.height())

    # 左/右边条：y ∈ [top_vis, h-bot_vis]，按周期分段平铺
    left_img = _knot_tinted("left", color)
    right_img = _knot_tinted("right", color)
    lw = left_img.width() * s if not left_img.isNull() else 0.0
    rw = right_img.width() * s if not right_img.isNull() else 0.0
    avail_v = h - top_vis - bot_vis
    if avail_v > 2 and not left_img.isNull():
        reps_v = max(1, int(left_img.height() // _KNOT_PERIOD_V))
        m = max(1, round(avail_v / (_KNOT_PERIOD_V * s)))
        segv = avail_v / m
        for i in range(m):
            sy = (i % reps_v) * _KNOT_PERIOD_V
            y = top_vis + i * segv
            draw("left", 0, y, lw, segv, 0, sy, left_img.width(), _KNOT_PERIOD_V)
            draw("right", w - rw, y, rw, segv,
                 0, sy, right_img.width(), _KNOT_PERIOD_V)

    # 四角最后画（压住边条端头）。对切时：上角裁掉下半（保结头），
    # 下角裁掉上半（保结头），源图按显示高度反推裁剪行
    draw("tl", 0, 0, tl_w, tl_vis_h, 0, 0, tl_w / s, tl_vis_h / s)
    draw("tr", w - tr_w, 0, tr_w, tr_vis_h, 0, 0, tr_w / s, tr_vis_h / s)
    draw("bl", 0, h - bl_vis_h, bl_w, bl_vis_h,
         0, bl_h / s - bl_vis_h / s, bl_w / s, bl_vis_h / s)
    draw("br", w - br_w, h - br_vis_h, br_w, br_vis_h,
         0, br_h / s - br_vis_h / s, br_w / s, br_vis_h / s)
    p.end()
    return out


# ---- 悬浮条集成（原 floating_bar.py 凯尔特分支） ----

# 两态共用一张凯尔特结，颜色/旋转由状态控制；图标放大到 40px 突出细节
ICON_SIZE = 40
TOGGLE_QSS = "QPushButton { background: transparent; border: none; }"

# 长按准备中（spec §3.2.1）的结图色：暗红。准备中可能被撤销（轻点丢弃），
# 既不能用录音的强调色，也不能借用录音的旋转动画
ARMED_ICON_COLOR = "#a03c3c"


def apply_icons(bar) -> None:
    """启停按钮图标：两态都是凯尔特结（待机=文字色，录音=强调色）。"""
    bar._btn_toggle.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    bar._play_icon = build_icon(bar, rotating=False, recording=False)
    bar._stop_icon = build_icon(bar, rotating=False, recording=True)


def apply_armed_icon(bar) -> None:
    """长按准备中：结图换暗红、不旋转（沿用结图取色这条既有语言）。

    松手/越过阈值后由 floating_bar._apply_state 的 IDLE/LISTENING 分支复位，
    这里不需要自己收尾。
    """
    bar._btn_toggle.setIcon(
        QIcon(knot_pixmap(size=ICON_SIZE, color=ARMED_ICON_COLOR)))


def build_icon(bar, rotating: bool, recording: bool) -> QIcon:
    """生成凯尔特结图标。

    rotating: True 时使用当前 bar._knot_angle 角度（录音中）
    recording: True=录音中（强调色），False=待机（主文字色）
    """
    size = ICON_SIZE  # 按钮 52×44，图标尽量放大突出图案
    color = bar._accent_color.name() if recording else bar._text_color.name()
    angle = bar._knot_angle if rotating else 0.0
    return QIcon(knot_pixmap(size=size, color=color, angle_deg=angle))


def close_icon():
    """退出按钮：叠在白角饰上，恒用白 X+黑描边。"""
    from ..icons import close_icon as _close_icon
    return _close_icon("#ffffff", outline="#000000")


def min_height() -> int:
    """最小高度：上下链带厚度 + 启停按钮高 + 上下各 ~4px 呼吸空间，
    保证播放按钮完整落在链带内侧，不压编辫。"""
    top_t, bot_t = knot_border_metrics()
    return int(top_t + bot_t + 44 + 8 + 0.999)


def draw_border(bar, painter) -> None:
    """绘制链纹边框（缓存 QPixmap 在 bar 上，尺寸/主题色变化才重建）。"""
    w, h = bar.width(), bar.height()
    color_name = bar._text_color.name()
    key = (w, h, color_name)
    if bar._knot_border_cache is None or bar._knot_border_cache[0] != key:
        bar._knot_border_cache = (key, knot_border_pixmap(w, h, color_name))
    painter.drawPixmap(0, 0, bar._knot_border_cache[1])


def start_rotation(bar) -> None:
    if bar._knot_rotate_timer is not None:
        return
    timer = QTimer(bar)
    timer.setInterval(50)  # 20fps，肉眼顺滑且 CPU 可忽略
    timer.timeout.connect(lambda: _on_rotate_tick(bar))
    bar._knot_rotate_timer = timer
    timer.start()


def _on_rotate_tick(bar) -> None:
    """录音中每 50ms 旋转 18°，约 1 秒一圈。"""
    bar._knot_angle = (bar._knot_angle + 18.0) % 360.0
    bar._btn_toggle.setIcon(build_icon(bar, rotating=True, recording=True))


def stop_rotation(bar) -> None:
    if bar._knot_rotate_timer is not None:
        bar._knot_rotate_timer.stop()
        bar._knot_rotate_timer.deleteLater()
        bar._knot_rotate_timer = None
    bar._knot_angle = 0.0
    # 复位到 0° 待机图
    bar._btn_toggle.setIcon(build_icon(bar, rotating=False, recording=False))


def menu_qss(bar, font_stack: str) -> str:
    """右键菜单：纯黑底 + 纯白文字 + 白色选中（灰阶，无彩色）。"""

    def _rgba(c: QColor, alpha: int) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"

    text = bar._text_color.name()
    accent = bar._accent_color.name()
    a = QColor(accent)
    sel_bg = f"rgba({a.red()},{a.green()},{a.blue()},52)"
    menu_bg = _rgba(bar._bg_color, 215)
    return f"""
        QMenu {{
            {font_stack}
            background: {menu_bg}; color: {text};
            border: 1px solid #4a4a4a; border-radius: 0px; padding: 6px;
        }}
        QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 0px; }}
        QMenu::item:selected {{ background: {sel_bg}; color: {accent}; }}
        QMenu::item:disabled {{ color: #8a8a8a; font-weight: bold; font-size: 13px; padding: 8px 12px 2px; }}
        QMenu::separator {{ height: 1px; background: #3a3a3a; margin: 4px 8px; }}
    """
