"""黑胶唱片主题特性（方形窗体）：唱片旋转 + 唱针 + 纹路电平光。

布局与「极简 / 像素」一致：voice_print: true（正方形悬浮窗、整窗启停、文字走上方气泡）。
本模块只负责皮肤绘制：

- 待机：唱片静止、唱针停在臂架；
- 录音/对话：唱片随电平加速旋转、盘面光泽随转动扫过、唱针落盘并随会话时长
  向内圈推进。

构图对齐概念图：唱片居中并尽量贴满方框；唱臂底座是贴在右上角的小黑点，
与悬浮窗的 ✕ 按钮同轴重叠（✕ 是子控件，绘制在底座之上）。
字号与气泡正文沿用应用默认字体；精简构建（lite）排除本模块（见 .spec）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from asr_voice.core.dialog import (
    DIALOG_UI_CONNECTING,
    DIALOG_UI_LISTENING,
    DIALOG_UI_SPEAKING,
    DIALOG_UI_THINKING,
    ROLE_AI,
    ROLE_USER,
)

# 本特性模块对应的 feature 名（悬浮窗按主题的 feature 名认领动画）
FEATURE_NAME = "vinyl"

# 活跃态（唱片旋转、唱针落盘）：录音两态 + 对话四态。
# 字面量与 floating_bar.UI_LISTENING/UI_RECOGNIZING 同值（反向 import 会循环）。
_ACTIVE_STATES = frozenset({
    "listening", "recognizing",
    DIALOG_UI_CONNECTING, DIALOG_UI_LISTENING,
    DIALOG_UI_THINKING, DIALOG_UI_SPEAKING,
})

# 唱片几何（相对方窗边长的比例）：居中、接近贴边
_DISC_R = 0.37
_DISC_CY = 0.50
_LABEL_R = 0.33          # 相对唱片半径

# 唱臂落点（相对窗宽/窗高）：外圈 → 内圈，沿同一射线向内滑
_ARM_OUTER = (0.665, 0.330)
_ARM_INNER = (0.600, 0.385)
# 停靠点：贴着右缘、抬臂离开盘面（长度与落点接近，抬落只摆动不伸缩）
_ARM_PARK = (0.925, 0.415)

# 沟槽：少而深（效果图口径），圈数过多会糊成一片
_GROOVES = (0.42, 0.52, 0.62, 0.72, 0.82, 0.92)

_DISC_FILL = "#141419"
_DISC_EDGE = "#2A2A32"
_GROOVE = "#4A4A56"
_LABEL_ON = "#C0323A"
_LABEL_OFF = "#8E2A2E"
_CREAM = "#E8DFC8"
_SILVER = "#C8CCD2"
# 臂架（小黑点）：面板本身近黑，靠一圈深灰描边读出"圆点"
_HUB_FILL = "#050507"
_HUB_RING = "#43434E"

# 气泡：奶油底唱片套 + 深色油墨（颜色也由 bar_widgets 取用）
_BUBBLE_BG = "#E8DFC8"
_BUBBLE_BORDER = "#C9BDA4"
BUBBLE_INK = "#241C15"
BUBBLE_BANDS = {ROLE_USER: "#217A5C", ROLE_AI: "#7240A8"}

_ACTIVE_MS = 33          # 活跃态刷新间隔（~30fps，旋转才不卡顿）
_IDLE_MS = 100           # 待机/收针间隔
_FULL_SWEEP_S = 90       # 唱针从外圈走到内圈的时间（秒）

_TIMER_ATTR = "_vinyl_timer"


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _active(bar) -> bool:
    return getattr(bar, "_state", "") in _ACTIVE_STATES


def _disc_geom(bar):
    side = min(bar.width(), bar.height())
    r = side * _DISC_R
    return bar.width() / 2, bar.height() * _DISC_CY, r


def _arm_base(bar) -> tuple[float, float]:
    """唱臂底座圆心：与右上角 ✕ 按钮同轴，随窗口尺寸贴住方框角。

    ✕ 是悬浮窗的子控件（画在 paintEvent 之上），底座圆点画在它下面，
    两者同轴即为「臂架压在退出键上」的极限贴合效果。
    """
    btn = getattr(bar, "_btn_exit", None)
    if btn is not None:
        try:
            center = btn.geometry().center()
            return float(center.x()), float(center.y())
        except (AttributeError, RuntimeError):
            pass
    return bar.width() - 13.0, 13.0


def _stylus_tip(bar) -> tuple[QPointF, QPointF]:
    """唱臂根部（底座圆心）与唱针当前位置。

    抬臂 → 落盘：停靠点线性摆到外圈；落盘后按会话时长沿射线向内圈滑。
    唱臂是刚性结构：落盘后针尖不再有任何抖动，只有缓慢向内圈推进。
    """
    w, h = bar.width(), bar.height()
    base = QPointF(*_arm_base(bar))
    park = QPointF(w * _ARM_PARK[0], h * _ARM_PARK[1])
    outer = QPointF(w * _ARM_OUTER[0], h * _ARM_OUTER[1])
    inner = QPointF(w * _ARM_INNER[0], h * _ARM_INNER[1])

    arm = _clamp(getattr(bar, "_vinyl_arm", 0.0))
    prog = _clamp(getattr(bar, "_vinyl_progress", 0.0))
    tip = park + (outer - park) * arm
    if arm > 0:
        tip = tip + (inner - outer) * (arm * prog)
    return base, tip


# ---- 悬浮窗（方窗）：面板 + 唱片 + 唱臂 + 纹路电平光 ----

def draw_panel(painter: QPainter, bar) -> None:
    """黑胶方窗面板：深色圆角底 + 低透明暖色描边（悬停提亮）。"""
    w, h = bar.width(), bar.height()
    if min(w, h) < 20:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor(bar._bg_color)
    if bar._hover:
        bg = bg.lighter(112)
    painter.setPen(Qt.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
    border = QColor(bar._text_color)
    border.setAlpha(46 if bar._hover else 26)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(border, 1))
    painter.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 8, 8)


def _draw_sheen(painter: QPainter, bar, cx: float, cy: float, r: float) -> None:
    """盘面光泽：两瓣柔光随唱片转角扫过。

    真实黑胶的沟槽是各向异性的，反光会随盘面转动在盘上扫动：
    宽淡的暖色光晕打底，窄亮的白色反光核心叠在同一个角度，转速越快扫得越快。
    """
    spin = float(getattr(bar, "_vinyl_spin", 0.0) or 0.0) % 360.0
    tint = QColor(bar._text_color)

    def stop(alpha: int) -> QColor:
        c = QColor(tint)
        c.setAlpha(alpha)
        return c

    clear = stop(0)
    # 宽柔光：一瓣约 90° 的渐变，正反两瓣（唱片两侧同时吃光）
    wide = QConicalGradient(cx, cy, spin)
    wide.setColorAt(0.0, clear)
    wide.setColorAt(0.10, clear)
    wide.setColorAt(0.25, stop(46))
    wide.setColorAt(0.40, clear)
    wide.setColorAt(0.60, clear)
    wide.setColorAt(0.75, stop(46))
    wide.setColorAt(0.90, clear)
    wide.setColorAt(1.0, clear)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(wide))
    painter.drawEllipse(QPointF(cx, cy), r, r)

    # 窄核心：同一个角度再压一层高光，边缘更锐，读作"镜面反光"
    core = QConicalGradient(cx, cy, spin)
    core.setColorAt(0.0, clear)
    core.setColorAt(0.205, clear)
    core.setColorAt(0.25, stop(36))
    core.setColorAt(0.295, clear)
    core.setColorAt(0.705, clear)
    core.setColorAt(0.75, stop(36))
    core.setColorAt(0.795, clear)
    core.setColorAt(1.0, clear)
    painter.setBrush(QBrush(core))
    painter.drawEllipse(QPointF(cx, cy), r, r)


def _draw_disc(painter: QPainter, bar, cx: float, cy: float, r: float) -> None:
    """唱片本体：盘面、纹路、反光；彩标上的刻度线随录音旋转。"""
    painter.setPen(QPen(QColor(_DISC_EDGE), 1))
    painter.setBrush(QColor(_DISC_FILL))
    painter.drawEllipse(QPointF(cx, cy), r, r)

    # 沟槽：少而深，一圈一圈读得出来（不再靠低透明叠密度）
    groove = QColor(_GROOVE)
    painter.setBrush(Qt.NoBrush)
    width = max(1.0, r * 0.04)
    for i, f in enumerate(_GROOVES):
        groove.setAlpha(175 if i % 2 == 0 else 130)
        painter.setPen(QPen(groove, width))
        rr = r * f
        painter.drawEllipse(QPointF(cx, cy), rr, rr)

    # 盘面光泽：两瓣柔光随唱片转角扫过，模拟真实黑胶的反光
    _draw_sheen(painter, bar, cx, cy, r)

    active = _active(bar)
    lr = r * _LABEL_R
    # 彩标：一块干净的实色圆 + 中心轴孔，不再叠同心环（叠多了像靶心）
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(_LABEL_ON if active else _LABEL_OFF))
    painter.drawEllipse(QPointF(cx, cy), lr, lr)
    hole = max(1.4, lr * 0.16)
    painter.setBrush(QColor("#0B0B0E"))
    painter.drawEllipse(QPointF(cx, cy), hole, hole)


def _draw_arm(painter: QPainter, bar) -> None:
    """唱臂：贴角的黑色臂架圆点 + 斜臂 + 唱头。"""
    w, h = bar.width(), bar.height()
    side = min(w, h)
    if side < 32:
        return
    base, tip = _stylus_tip(bar)

    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(_SILVER), max(1.6, side * 0.026),
                        Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(base, tip)

    # 唱头：落盘时用强调色，待机银灰
    head = QColor(bar._accent_color) if _active(bar) else QColor(_CREAM)
    painter.setPen(Qt.NoPen)
    painter.setBrush(head)
    painter.drawEllipse(tip, side * 0.028, side * 0.028)

    # 臂架：贴在 ✕ 上的黑色圆点（画在臂杆之后，盖住根部）
    hub_r = max(3.8, side * 0.074)
    painter.setPen(QPen(QColor(_HUB_RING), 1))
    painter.setBrush(QColor(_HUB_FILL))
    painter.drawEllipse(base, hub_r, hub_r)


def _draw_led(painter: QPainter, bar) -> None:
    """左下角状态灯：颜色复用状态点映射（红=录音、青绿=聆听…）。"""
    side = min(bar.width(), bar.height())
    if side < 40:
        return
    color = bar._status_color()
    halo = QColor(color)
    halo.setAlpha(50)
    cx, cy = bar.width() * 0.14, bar.height() * 0.88
    painter.setPen(Qt.NoPen)
    painter.setBrush(halo)
    painter.drawEllipse(QPointF(cx, cy), side * 0.05, side * 0.05)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(cx, cy), side * 0.026, side * 0.026)


def draw_pattern(painter: QPainter, bar) -> None:
    """中央唱片图案（面板由 draw_panel 负责）。"""
    side = min(bar.width(), bar.height())
    if side < 24:
        return
    cx, cy, r = _disc_geom(bar)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _draw_disc(painter, bar, cx, cy, r)
    _draw_arm(painter, bar)
    _draw_led(painter, bar)


# ---- 气泡：奶油唱片套 + 深色油墨的三角尾巴 ----

def draw_bubble(bubble, painter: QPainter) -> None:
    """黑胶气泡：奶油套面 + 暖灰描边，尾巴沿用默认 12×7 三角。"""
    w, h = bubble.width(), bubble.height()
    tail_h = bubble._tail_h_px
    top = tail_h if bubble._tail_up else 0
    body_h = h - tail_h
    cx = w / 2
    tw = bubble._tail_w_px / 2

    body = QRectF(0.5, top + 0.5, w - 1, body_h - 1)
    path = QPainterPath()
    path.addRoundedRect(body, 8, 8)
    tail = QPainterPath()
    if bubble._tail_up:
        tail.moveTo(cx - tw, top + 1)
        tail.lineTo(cx + tw, top + 1)
        tail.lineTo(cx, 1)
    else:
        tail.moveTo(cx - tw, top + body_h - 1)
        tail.lineTo(cx + tw, top + body_h - 1)
        tail.lineTo(cx, h - 1)
    tail.closeSubpath()
    path = path.united(tail)

    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(_BUBBLE_BG))
    painter.drawPath(path)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(_BUBBLE_BORDER), 1))
    painter.drawPath(path)


# ---- 动画：唱片旋转 + 唱针起落 ----

def _ensure_timer(bar):
    timer = getattr(bar, _TIMER_ATTR, None)
    if timer is not None:
        return timer
    timer = QTimer(bar)
    timer.setInterval(_ACTIVE_MS)
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(lambda: _on_tick(bar))
    setattr(bar, _TIMER_ATTR, timer)
    return timer


def _should_animate(bar) -> bool:
    # 只有当前主题仍认领本特性时才跑（切主题/精简构建下自然停表）
    if getattr(bar, "_theme_feature_name", "") != FEATURE_NAME:
        return False
    try:
        return bar.isVisible()
    except Exception:
        return True


def _stop(bar) -> None:
    timer = getattr(bar, _TIMER_ATTR, None)
    if timer is not None and timer.isActive():
        timer.stop()


def stop_animation(bar) -> None:
    """公开入口：停表（切离黑胶主题/隐藏窗口时调用）。"""
    _stop(bar)


def sync_animation(bar) -> None:
    """按主题/状态/可见性启停节拍器；非黑胶主题或不可见时停表。"""
    if not _should_animate(bar):
        _stop(bar)
        return
    active = _active(bar)
    # 新一轮录音从外圈重新开始，不继承上一轮的唱针位置
    if active and not getattr(bar, "_vinyl_was_active", False):
        setattr(bar, "_vinyl_progress", 0.0)
    setattr(bar, "_vinyl_was_active", active)
    timer = _ensure_timer(bar)
    timer.setInterval(_ACTIVE_MS if active else _IDLE_MS)
    arm = float(getattr(bar, "_vinyl_arm", 0.0) or 0.0)
    # 待机且唱针已归位：只补一次重绘（主题刚切入时），不必常驻定时器
    if active or arm > 0.01:
        if not timer.isActive():
            timer.start()
    else:
        bar.update()


def _on_tick(bar) -> None:
    if not _should_animate(bar):
        _stop(bar)
        return
    active = _active(bar)
    target = 1.0 if active else 0.0
    arm = float(getattr(bar, "_vinyl_arm", 0.0) or 0.0)
    arm += (target - arm) * 0.28
    if abs(target - arm) < 0.01:
        arm = target
    setattr(bar, "_vinyl_arm", arm)

    if active:
        level = _clamp(getattr(bar, "_level", 0.0))
        dt = max(1, _ensure_timer(bar).interval()) / 1000.0
        spin = float(getattr(bar, "_vinyl_spin", 0.0) or 0.0)
        setattr(bar, "_vinyl_spin", (spin + (90 + 270 * level) * dt) % 360)
        prog = float(getattr(bar, "_vinyl_progress", 0.0) or 0.0)
        setattr(bar, "_vinyl_progress", min(1.0, prog + dt / _FULL_SWEEP_S))
        bar.update()
    elif arm > 0:
        bar.update()
    else:
        setattr(bar, "_vinyl_progress", 0.0)
        bar.update()
        _stop(bar)
