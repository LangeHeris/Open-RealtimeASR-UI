"""悬浮条附属组件：极简主题气泡文本 + 「修正后上屏」确认按钮条。

从 floating_bar.py 拆出：两者都是悬浮条外的独立顶层小窗口，
通过持有 FloatingBar 引用（self._bar）同步位置/配色/回调，与悬浮条
主类之间无继承关系，独立成模块不影响行为。
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QPushButton, QWidget

from .fonts import PIXEL_TEXT_PT, get_font, get_pixel_font
from .icons import spinner_pixmap
from .theme_features import get_feature
from ..core.dialog import DIALOG_UI_STATES, ROLE_AI, ROLE_USER
from ..i18n import t

if TYPE_CHECKING:
    from .floating_bar import FloatingBar

logger = logging.getLogger(__name__)


def bubble_font(widget, size: int, weight=None):
    """气泡/条内文字取字体：像素主题走像素字体，其余走常规字体。

    像素主题下正文用 **10px 点阵规格**字体的 2× 档（PIXEL_TEXT_PT = 15pt /
    20px）。用户逐档试过：12px 规格的 12px「太小」、24px「搞那么大干嘛」；
    8px 规格的 16px 实切一轮又被嫌「太小、想更细」（8px 网格笔画占字身
    ≈14%，反而比 10px 规格的 ≈11% 更粗）→ 回落 20px。层级靠颜色/位置/
    角色色带区分，不靠字号（像素风本来也不宜多档字号）。

    性能：QFontMetrics 每次都会调本函数，像素字体 family 在 fonts 内
    懒加载后缓存，这里的开销只是一次 getattr + QFont 构造。
    """
    bar = getattr(widget, "_bar", None)
    if bar is not None and getattr(bar, "_pixel_mode", False):
        return get_pixel_font(PIXEL_TEXT_PT)
    if weight is not None:
        return get_font(size, weight)
    return get_font(size)

# 角色视觉（两行字幕式气泡/条内共用）：色带颜色 + 标签。
# 与对话态状态点同色系（青绿=聆听你、紫=AI 播报），一眼分得清谁在说；
# batch 1 那种把「你：/AI：」拼进文本的做法已经退场。
ROLE_BAND_COLORS = {ROLE_USER: "#4ec9a0", ROLE_AI: "#b07ce8"}
# 标签存 i18n 键（导入期不取文案，理由见 i18n 模块 docstring）。
# 取用一律经 role_label()。
_ROLE_LABEL_KEYS = {ROLE_USER: "bubble.role.user", ROLE_AI: "bubble.role.ai"}


def role_label(role: str) -> str:
    """角色标签显示名（未知角色返回空串：绘制与拼接都按「无标签」处理）。"""
    key = _ROLE_LABEL_KEYS.get(role)
    return t(key) if key else ""


def reassert_topmost(widget) -> None:
    """Windows：重设 WS_EX_TOPMOST（SWP_NOACTIVATE 不抢焦点）。

    隐藏后重新 show 的窗口会被 Windows 插到置顶组底部，被其他置顶窗口
    压住（置顶标志仍在，视觉上"置顶失效"）；显式 SetWindowPos(HWND_TOPMOST)
    提回顶部。与悬浮条 FloatingBar._assert_topmost 同理，供附属顶层
    小窗（气泡/确认按钮条）复用。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        )
    except Exception:
        logger.debug("附属窗口重设 TOPMOST 失败，忽略", exc_info=True)


# ---- 文本截断（悬浮条与气泡共用单一实现，消除双份漂移） ----
# 用原生 elidedText 单次 O(n) 测量（天然按字素/emoji 代理对边界截断），
# 再把 Qt 返回的 … 替换为 "..."，规避该符号在部分字体下渲染为竖线的问题。
# 宽度预算 = max_width - 省略号宽，保证截断结果总宽不超过 max_width。


def elide_right(metrics, text: str, max_width: int) -> str:
    """文本超出 max_width 时右侧截断，尾部追加 '...'。"""
    if metrics.horizontalAdvance(text) <= max_width:
        return text
    ew = metrics.horizontalAdvance("...")
    base = metrics.elidedText(text, Qt.ElideRight, max_width - ew)  # 单次 O(n)
    return base.rstrip("\u2026") + "..."


def elide_left(metrics, text: str, max_width: int) -> str:
    """文本超出 max_width 时左侧截断，头部追加 '...'。"""
    if metrics.horizontalAdvance(text) <= max_width:
        return text
    ew = metrics.horizontalAdvance("...")
    base = metrics.elidedText(text, Qt.ElideLeft, max_width - ew)  # 单次 O(n)
    return "..." + base.lstrip("\u2026")


def draw_caret_marker(painter: QPainter, x: float, cy: float, text_h: float,
                      visible: bool, color: QColor, width: float = 2.0) -> None:
    """未定稿竖线光标（|）：**非像素主题**用它替代文本尾部的 "..."。

    像素主题有专属的金色 ▼（theme_features.pixel.draw_more_marker，DQ 式），
    其他主题没有主题化标记，就用与文本光标同形的竖线表达"还在说、未定稿"——
    比文字 "..." 更贴近"正在输入"的直觉，也不占用文本宽度预算。
    **语音模式（对话四态）里两种标记都不画**，见 SpeechBubble._cursor_cancelled。
    visible=False 时不画：闪烁由调用方定时器驱动（气泡走自身 _blink_timer、
    悬浮条走 _pulse_timer 的相位），本函数只负责画当前相位。
    width 由调用方给（气泡 1px / 悬浮条条内 2px，见各自调用点）；坐标先取整，
    1px 线落在整像素列上才不会被抗锯齿糊成两列半透明。
    """
    if not visible:
        return
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRect(QRectF(round(x), round(cy - text_h / 2), width,
                            round(text_h)))


def wrap_lines(metrics, text: str, max_w: int) -> list[str]:
    """按像素宽度贪心折行。

    CJK 逐字断行；ASCII 单词内不硬断（回退到最近空格），
    避免 "realtime" 被切成 "realti/me" 这种难读的断法。
    """
    lines: list[str] = []
    cur = ""
    cur_w = 0
    for ch in text:
        w = metrics.horizontalAdvance(ch)
        if cur and cur_w + w > max_w:
            cut = len(cur)
            if ch.isascii() and (ch.isalnum() or ch in "._-/:+"):
                # 断点落在 ASCII 单词中间：回退到最近空格，整词换行
                #（找不到空格说明这个词本身超宽，只能硬断）
                sp = cur.rfind(" ")
                if sp >= 0:
                    cut = sp + 1
            head = cur[:cut].rstrip()
            if head:
                lines.append(head)
            # 断在空格处时不要把空格带到下一行行首（白占宽度）
            cur = cur[cut:].lstrip(" ")
            cur_w = metrics.horizontalAdvance(cur)
        cur += ch
        cur_w += w
    if cur.strip():
        lines.append(cur)
    return lines


def take_tail_lines(metrics, lines: list[str], max_w: int, limit: int) -> list[str]:
    """取最后 limit 行（流式回复：最新说出的字在尾部），首行补前导省略号。"""
    kept = list(lines[-limit:])
    first = kept[0]
    while first and metrics.horizontalAdvance("…" + first) > max_w:
        first = first[1:]
    kept[0] = "…" + first
    return kept


def take_head_lines(metrics, lines: list[str], max_w: int, limit: int) -> list[str]:
    """取最前 limit 行（说完了：开头比结尾更该被看到），末行补尾部省略号。"""
    kept = list(lines[:limit])
    ew = metrics.horizontalAdvance("…")
    kept[-1] = elide_right(metrics, kept[-1], max_w - ew) + "…"
    return kept


class SpeechBubble(QWidget):
    """声纹主题的识别文本气泡：悬浮窗正上方弹出，仅录音时显示。

    独立顶层窗口（不抢焦点、不吃输入），颜色由 FloatingBar 注入；底部小三角指向
    悬浮窗，宽度随文本自适应（超出省略）。
    """

    TAIL_H = 7       # 底部小三角高度
    TAIL_W = 12      # 小三角底边宽
    PAD_X = 14
    PAD_Y = 10
    MAX_W = 420      # 单行模式宽度上限（录音/提示/确认文本）
    MIN_W = 96
    LOADING_SPINNER_D = 14   # loading 模式前导转圈直径
    LOADING_GAP = 8          # 转圈与文字间距
    CARET_EXTRA = 16         # 非像素主题的竖线光标（|）尾部预留宽度：
                             # 8px 间距 + 线宽 + 余量；取 16 与像素主题的
                             # MARKER_EXTRA（8+8）同值，两种标记的右侧留白一致
    CARET_W = 1              # 气泡里竖线光标的宽度（用户诉求「细一点」：
                             # 初版 2px；悬浮条条内那条仍是 2px，见 floating_bar）
    CARET_H_RATIO = 0.6      # 竖线光标高度 = 字高的 60%（「小一点」→「再小一点」：
                             # 初版取整行字高、次版 70%；随字号自动缩放）
    # 卡片模式（语音模式）：贴悬浮窗宽度的多行气泡
    CARD_MIN_W = 160
    CARD_MAX_W = 560         # H61：用户诉求①「太小」—— 420 放宽到 560（仍 ≤ 条宽）
    CARD_W_RATIO = 2.8       # 相对悬浮窗宽的倍数（方窗默认 70px -> 196px，见 CARD_MIN_AVAIL_W 保底）
    # H71-W1：极简主题是 56~150px 的小方条，按 2.8 倍算只有 157~420px（用户真机
    # 77px 条 ⇒ 卡只 215px，同一段文本因此省掉 25~34 行）。小方条下不再让卡片被
    # 条宽拖窄：按**可用宽度**取宽，保底 CARD_MIN_AVAIL_W（屏够的话）。
    CARD_MIN_AVAIL_W = 420   # 小方条下的保底卡片宽度
    CARD_SCREEN_MARGIN = 8   # 卡片距屏幕左右边缘的最小留白
    CARD_ABS_MIN_W = 64      # 绝对下限（屏比 ~80px 还窄才碰得到，现实不可达）
    CARD_MAX_LINES = 3       # **收起态**当前轮最多几行（点击收起后回到这个值）
    PREV_MAX_LINES = 3       # 收起态上一轮最多几行（压暗退到背景）
    EXPAND_MAX_LINES = 14    # 兼容保留：旧调用方/测试引用；自动展开态不再用它
    AUTO_MIN_LINES = 3       # 自动展开的下限（放得下就全显，放不下也不少于 3 行）
    # H71-M2：旧名 AUTO_HARD_CAP，注释写"单段 24 行"、实际只被 *2 当两段合计预算用，
    # 单段当前轮实测可达 47 行 —— 名实不符（H61 评审 M-2）。改名 + **真收口**：
    # 这个数字现在就是**单段**（上一轮/当前轮各自）的自动展开行数硬上限，_relayout_card
    # 逐段夹它；两段合计仍是 2×它（=48，与旧行为同一量级，不会把大屏文本变少）。
    AUTO_MAX_LINES = 24
    EXTRA_LINE_H = 26        # note / 思考行这类独立小字行的行高预算（行距+2*ROW_PAD_Y）
    # H71-W2：思考态在卡片内的独立提示行（仅极简主题）。
    # 只存 i18n 键（导入期不取文案，理由见 i18n 模块 docstring）；文案在调用点 t() 取。
    THINKING_TEXT_KEY = "bubble.thinking"
    ROW_PAD_X = 12           # 行内左右留白（角色色带外侧）
    ROW_PAD_Y = 5            # 行内上下留白
    BAND_W = 3               # 角色色带宽度
    BAND_GAP = 6
    WHO_W = 20               # 角色标签列宽（下限；实际按字体实测取 max，见 _who_w）
    WHO_GAP = 6
    SEP_H = 1                # 两行之间的分隔线高度

    def __init__(self, bar: "FloatingBar"):
        # 独立顶层窗口（parent=None）：随悬浮窗定位但不嵌入其几何
        super().__init__(None)
        self._bar = bar
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus   # 纯展示，永不抢输入焦点
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._elided = ""
        # loading 专用内容标记：悬浮条收起 loading 气泡前据此判断是否会吞其他提示
        self._loading = False
        self._spinner_angle = 0.0
        self._text_color = QColor("#eeeeee")
        self._bg = QColor(28, 28, 32, 235)
        self._border = QColor(255, 255, 255, 30)
        # 跑马灯状态：长确认文本在气泡内循环滚动（宽度不足时）
        self._marquee_timer: QTimer | None = None
        self._marquee_phase = 0.0
        self._marquee = False
        # 截断方向："right" 保留头部（录音累积全文，从头读）；
        # "left" 保留尾部（语音模式流式回复，新字在尾部）
        self._elide_mode = "right"
        # 卡片模式（语音模式）：两行字幕式（上=上一轮，角色不限；下=当前轮）
        self._card = False
        self._rows_spec: list[dict] = []   # 输入：role/text/struck/current/final
        self._rows: list[dict] = []        # 排版结果：lines/lh/h/size/...
        self._expanded = False             # 展开全文
        self._streaming = False            # 当前轮还在增长（留尾部）
        self._toast = ""                   # 卡片底部一行小字（临时提示）
        self._note = ""                    # 「已省略 N 行」标记（H61：截断不再静默）
        self._note_size = 11
        self._note_h = 0
        # H71-W2：「思考中…」（仅极简主题）。与 _note 一样是**独立字段**，
        # 绝不进 _rows —— 「最后一行 = 当前轮」是浮条与多份用例依赖的契约。
        self._thinking = ""
        self._thinking_size = 11
        self._thinking_h = 0
        # 尾巴方向：气泡在悬浮条上方时为 False（尾巴朝下指向悬浮条）；
        # 上方放不下翻到下方时 True（尾巴朝上）
        self._tail_up = False
        # 气泡内滚动（长回复看全文）：当前轮向上跳过的行数，0 = 停在最新内容。
        # 由 wheelEvent 调整，_relayout_card 按它切显示窗口。
        self._scroll_skip = 0
        # 上/下各还有几行没显示（滚动提示文案 + 滚动范围钳制用）
        self._scroll_above = 0
        self._scroll_below = 0
        # 尾巴几何随主题（像素主题的四级金字塔比默认小三角更高更宽），
        # 布局（_relayout*）与绘制（paintEvent）共用，set_colors 时同步
        self._tail_w_px = self.TAIL_W
        self._tail_h_px = self.TAIL_H
        # 单击 = 展开/收起，双击 = 复制本轮：先等一个双击间隔再判定单击
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        # 像素主题：DQ 式 ▼「还有下文」标记的闪烁（600ms 硬切，像素风无渐变）
        self._blink_on = True
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(600)
        self._blink_timer.timeout.connect(self._on_blink_tick)
        self.hide()

    def set_colors(self, bg: QColor, text: QColor) -> None:
        """跟随主题换色（背景沿用悬浮条底色含透明度；描边按明暗派生）。"""
        self._sync_tail_metrics()
        self._bg = QColor(bg)
        light = self._bar._is_light(self._bg.name())
        self._border = QColor(0, 0, 0, 45) if light else QColor(255, 255, 255, 50)
        # 卡片正文/标签都取 _text_color，必须随主题更新（旧实现在"已有文本"
        # 时不更新，是给单行模式留的；两行卡片会因此停在旧主题色）
        self._text_color = QColor(text)
        self.update()

    def _sync_tail_metrics(self) -> None:
        """按主题取尾巴几何（宽/高）：像素主题的像素金字塔与默认小三角不同尺寸。"""
        feat = get_feature("pixel") if getattr(self._bar, "_pixel_mode", False) else None
        if feat is not None:
            self._tail_w_px, self._tail_h_px = int(feat.TAIL_W), int(feat.TAIL_H)
        else:
            self._tail_w_px, self._tail_h_px = self.TAIL_W, self.TAIL_H

    def _on_blink_tick(self) -> None:
        """▼ 标记翻转（硬切）：不可见时不驱动重绘，timer 由 _sync_blink 收停。"""
        if not self.isVisible():
            return
        self._blink_on = not self._blink_on
        self.update()

    def _cursor_cancelled(self) -> bool:
        """语音模式（对话四态）里不画「未定稿」光标。

        用户诉求：「语音模式下可以直接取消光标」——对话内容一直在流式变化、又常年
        停在屏幕上，光标既没有信息量（"未定稿"是对话的常态），多行卡片里还会跟着
        文本乱跑。所以对话期间金色 ▼ / 竖线光标一律不画，宽度也不为它预留；
        录音模式（非对话）语义不变："还在说、未定稿"仍然靠它表达。
        """
        return getattr(self._bar, "_state", "") in DIALOG_UI_STATES

    def _sync_blink(self) -> None:
        """气泡可见且有内容、且该画光标时跑闪烁（像素主题金色 ▼ / 其他主题竖线）。

        两个条件都要满足：光标被取消（语音模式）时不必为空转的闪烁跑重绘，
        气泡隐藏/无文本同理（气泡只在方窗布局出现，不外溢到条内主题）。
        """
        want = (self.isVisible() and bool(self._text)
                and not self._cursor_cancelled())
        if want and not self._blink_timer.isActive():
            self._blink_on = True
            self._blink_timer.start()
        elif not want and self._blink_timer.isActive():
            self._blink_timer.stop()

    def show_message(self, text: str, color: QColor, marquee: bool = False,
                     elide: str = "right") -> None:
        """更新内容并显示（同步位置到悬浮窗正上方）。常规内容，清除 loading 标记。

        marquee=True：文本超长时在气泡内循环滚动（用于长确认文本），
        否则按 elide 方向静态省略："right" 保留头部（录音累积全文，默认），
        "left" 保留尾部（语音模式流式回复，最新说出的话在尾部）。"""
        self._loading = False
        self._marquee = marquee
        self._elide_mode = "left" if str(elide).lower() == "left" else "right"
        self._card = False
        self._rows_spec = []
        self._rows = []
        self._toast = ""
        self._note = ""
        self._thinking = ""     # H71-W2：离开卡片态就不该留思考行
        self._show(text, color)

    def show_loading(self, text: str, color: QColor, angle: float) -> None:
        """loading 模式：前导转圈 + 提示文字（angle 由悬浮条 tick 驱动旋转）。"""
        self._loading = True
        self._elide_mode = "right"   # loading 提示短且从头读，固定右截断
        self._card = False
        self._rows_spec = []
        self._rows = []
        self._toast = ""
        self._note = ""
        self._thinking = ""
        self._spinner_angle = angle
        self._show(text, color)

    def show_dialog(self, rows: list[dict], expanded: bool = False,
                    streaming: bool = False, toast: str = "",
                    thinking: bool = False) -> None:
        """语音模式多行字幕式卡片（上=上一轮，下=当前轮）。

        rows = [{"role","text","struck","current","final"}]，最多两行：上行是上一轮
        （压暗、小一号，角色不限），下行是当前轮（角色色带 + 角色标签）。宽度按
        悬浮条形态取（见 _card_width，H71-W1 起小方条不再把卡片拖窄），超长自动
        折行——不再单行截断，也不再靠「你：/AI：」前缀区分说话人。

        **展开语义（H61 起，与旧版相反）**：expanded=True 是**默认的自动展开**态 ——
        行数上限由屏幕可用高度反算，放得下就全显，真放不下才截断并在底部加
        「已省略 N 行」标记；expanded=False 只表示用户**点击收起**后的紧凑视图
        （当前轮 CARD_MAX_LINES 行 / 上一轮 PREV_MAX_LINES 行）。

        **溢出取舍（H61-B，同样与旧版相反）**：当前轮一律保留**尾部**（最新说出的
        字），不论 streaming=True 还是已定稿；上一轮永远保留开头（它是"刚才那句"）。
        旧实现"已定稿且不在播报中则回到头部"会让用户正读的尾部字当场消失，已废除。

        toast（临时提示，如"已复制本轮"/"外放可能有回声"）与 thinking（H71-W2，
        极简主题的「思考中…」）都画成卡片底部的**独立行**，分别存在 _toast /
        _thinking 字段里，**不进 _rows**：_rows 的「最后一行 = 当前轮」是浮条与
        多份用例依赖的契约。对话内容也不该被一条瞬时提示清屏。
        """
        self._loading = False
        self._marquee = False
        self._card = True
        self._elide_mode = "right"
        self._rows_spec = [dict(r) for r in rows]
        self._expanded = bool(expanded)
        self._streaming = bool(streaming)
        self._toast = str(toast or "")
        # H71-W2：思考行，独立字段（非极简主题的调用方传 False ⇒ 行为不变）
        self._thinking = t(SpeechBubble.THINKING_TEXT_KEY) if thinking else ""
        # _text 只作为"内容变了"的判定与复制兜底，卡片排版不走它
        self._text = "\n".join(str(r.get("text", "")) for r in self._rows_spec)
        self._relayout()
        self._sync_pos()
        if not self.isVisible():
            self.show()
        reassert_topmost(self)
        self._force_full_repaint()
        self._sync_blink()      # 像素主题：有内容即启动 ▼ 闪烁

    def set_spinner_angle(self, angle: float) -> None:
        """悬浮条 tick 推送转圈角度：存字段并触发重绘（旋转的唯一更新通道）。

        paintEvent 读的是气泡自己的 _spinner_angle，仅靠 update() 重绘角度不变，
        必须显式推角度进来才会转。"""
        self._spinner_angle = angle
        if self._loading:
            self.update()

    def is_loading(self) -> bool:
        """当前内容是否 loading 专用（悬浮条据此决定收起是否会吞其他提示）。"""
        return self._loading

    def hide(self) -> None:
        """收起气泡：loading 内容随收起作废（is_loading() 归 False）。

        主题切换收起、悬浮条隐藏（hideEvent）、_sync_bubble 末尾收起等
        全部显式隐藏都经由此方法，在此统一清标志，避免气泡已隐藏而
        loading 标志残留、后续误判吞其他提示；显示流程
        （show_loading/show_message -> _show）不经过 hide()，不受影响。
        """
        self._loading = False
        self._scroll_skip = 0      # 收起后下次显示回到最新内容
        self._stop_marquee()
        self._blink_timer.stop()
        super().hide()

    def _show(self, text: str, color: QColor) -> None:
        """更新内容并显示（同步位置到悬浮窗正上方）。"""
        # 内容变了才重置滚动相位——_sync_bubble 反复调用同一段文字时
        # 不重置，跑马灯才能平滑连续滚动而不跳回开头
        if text != self._text:
            self._marquee_phase = 0.0
        self._text = text
        self._text_color = QColor(color)
        self._relayout()
        self._sync_pos()
        if not self.isVisible():
            self.show()
        reassert_topmost(self)  # 重新弹出/内容更新时提回置顶组顶部
        self._force_full_repaint()
        self._sync_blink()      # 像素主题：有内容即启动 ▼ 闪烁

    def _force_full_repaint(self) -> None:
        """H71-W3：内容/尺寸变化后**强制整窗重绘**。

        真机反馈「有一部分被砍了一半文字显示不完整」在离屏环境复现不了（H61-D
        的残影是同一个盲区：离屏渲染没有合成器，每次 grab 都是全量重绘）。能做的
        加固只有一条：把"内容变了"这件事明确翻译成一次整窗重画。

        为什么是 repaint() 而不是 update(self.rect())：Qt 的 update() 默认就是
        update(rect())（整个窗口），两者完全等价，只是把整窗标脏、等事件循环合并；
        而这里的嫌疑现场正是**顶层 + WA_TranslucentBackground** 的窗口在
        resize/重排之后只补画了脏区。repaint() 是同步的整窗重画，立即把新内容
        落到底层 surface，不给"只重画了一部分"留窗口期。
        代价是同步绘制（不合并、可能多画几帧）——气泡很小，且只在内容/尺寸变化时
        调用（流式逐字更新走同一条路，量级不变），值得。
        """
        if not self.isVisible():
            return
        self.repaint()

    def _relayout(self) -> None:
        """按文本长度自适应宽度（超出 MAX_W 省略），重算窗口尺寸。

        跑马灯模式：保留完整文本、窗口固定 MAX_W，靠滚动读全；
        否则右侧静态省略。卡片模式（语音模式）走多行折行分支。"""
        size = int(getattr(self._bar.cfg, "font_size_bar", 13) or 13)
        font = bubble_font(self, size)
        metrics = QFontMetrics(font)
        if self._card:
            self._relayout_card()      # 卡片每行字号可能不同，内部自取 metrics
            return
        # loading 模式前导转圈占用宽度
        extra = (self.LOADING_SPINNER_D + self.LOADING_GAP) if self._loading else 0
        # 文本尾部未定稿标记的宽度预留：像素主题是金色 ▼（用主题自带的
        # MARKER_EXTRA），其他主题是竖线光标（|，同值预留）。**两者都必须预留**
        # ——否则气泡宽度正好包住文字、标记永远放不下（极简主题曾因此在真机上
        # 看不到光标：宽度算出来只有文本宽）。
        # 语音模式不画光标（见 _cursor_cancelled），自然也一分宽度都不预留。
        feat_pixel = get_feature("pixel") if getattr(self._bar, "_pixel_mode", False) else None
        if self._loading or self._cursor_cancelled():
            marker_extra = 0
        elif feat_pixel is not None:
            marker_extra = feat_pixel.MARKER_EXTRA
        else:
            marker_extra = self.CARET_EXTRA
        max_text_w = self.MAX_W - 2 * self.PAD_X - extra - marker_extra
        full_tw = metrics.horizontalAdvance(self._text)
        use_marquee = (
            self._marquee and not self._loading and full_tw > max_text_w
        )
        if use_marquee:
            # 跑马灯：完整文本，窗口固定到 MAX_W
            self._elided = self._text
            w = self.MAX_W
        else:
            # 与悬浮条共用截断 helper：单次 O(n) + "..." 替代 elidedText 的 …
            elide = elide_left if self._elide_mode == "left" else elide_right
            self._elided = elide(metrics, self._text, max_text_w)
            tw = metrics.horizontalAdvance(self._elided)
            w = min(self.MAX_W, max(self.MIN_W,
                                    tw + 2 * self.PAD_X + extra + marker_extra))
        h = metrics.height() + 2 * self.PAD_Y + self._tail_h_px
        self.resize(w, h)
        if use_marquee:
            self._start_marquee()
        else:
            self._stop_marquee()

    # ---- 卡片模式（语音模式）：贴窗多行 ----

    def _card_width_ceiling(self) -> int:
        """卡片宽度上限（H71-W1）：min(CARD_MAX_W, 屏宽 - 2*边距)。

        旧实现只按 CARD_MAX_W 夹，屏宽 < 560 时卡片比屏还宽 —— 横向钳制
        min(max(x, left), left + width - self.width()) 会把 x 压到屏幕左外，
        卡片左半截出屏（H61 评审 M-6 记的边界）。拿不到屏幕时退回 CARD_MAX_W。
        """
        avail_w = 0
        try:
            screen = self._bar.screen()
            if screen is not None:
                avail_w = int(screen.availableGeometry().width())
        except Exception:
            avail_w = 0
        if avail_w <= 0:
            return self.CARD_MAX_W
        return min(self.CARD_MAX_W, avail_w - 2 * self.CARD_SCREEN_MARGIN)

    def _card_width(self) -> int:
        """卡片宽度跟随悬浮窗的形态，用户拖动改变尺寸时同步变化。

        - 条形悬浮窗（宽）：与条同宽，上下对齐，看着像条"长出来"的一块；
          条内文本区只有约 210px，气泡才是真正承载内容的地方；
        - 小方条（极简声纹窗，56~150px）：H71-W1 起**不再**按 2.8 倍被条宽拖窄
          —— 用户真机 77px 条 ⇒ 卡只 215px，同一段文本省掉 25~34 行，正是「太窄」
          的根。改为按**可用宽度**取宽：max(2.8*条宽, CARD_MIN_AVAIL_W)，屏够宽时
          保底 420（只会向上放宽，不会把任何既有情形变窄）。

        两条路径的结果最终都被 _card_width_ceiling() 夹到屏内：卡片**永远不比
        屏幕宽**（屏宽 < 2*边距+64 这种现实不可达的屏除外，见 CARD_ABS_MIN_W）。
        """
        try:
            bar_w = int(self._bar.width())
        except Exception:
            bar_w = self.CARD_MIN_W
        if bar_w >= self.CARD_MIN_W * 2:        # 条形悬浮窗：与条同宽
            target = bar_w
        else:                                   # 小方条：按可用宽度取宽
            target = max(int(bar_w * self.CARD_W_RATIO), self.CARD_MIN_AVAIL_W)
        return max(self.CARD_ABS_MIN_W, min(self._card_width_ceiling(), target))

    def _keep_tail(self, current: bool, final: bool) -> bool:
        """当前行溢出时是否保留尾部（最新说出的字）。

        两个独立理由，任一成立就保尾：

        - `self._streaming`：状态是 dialog_speaking，AI 正在播报，新字在尾部；
        - `not final`：这一行还没定稿（用户的识别中间结果），新字同样在尾部。

        只认 `_streaming` 会让用户说话时走"留开头"分支 —— 用户说话时状态是
        dialog_listening，字数一超过行数上限，刚说的几个字就从屏上消失，
        正是 P1「看不清自己的识别文本」在未定稿行上的残余。
        上一轮（current=False）永远留开头：它是"刚才那句"，要从头读。
        """
        # H61-B：**当前行一律留尾，定稿不再翻面**。原实现是「streaming 或未定稿 →
        # 留尾；定稿且非播报 → 留头」：用户正读着尾部字（识别中间结果），句子一定稿
        # 就跳到开头，那 12 个字当场消失（Step 0 实测：89 字文本，中间态可见尾部 47 字，
        # 定稿瞬间换成另一段）。自动展开后截断本就少见，真超预算时留尾 + 明确的省略
        # 标记，比"窗口悄悄搬家"诚实。上一轮（current=False）仍留开头：它是"刚才那句"。
        return bool(current)

    def _who_w(self) -> int:
        """角色标签列宽：按当前字体实测「你 / AI」的最大宽度，不小于 WHO_W。

        原先是固定 20px，常规字体 13pt 下「AI」约 17px 够用；但像素主题走
        2× 档（18pt=24px）时标签宽 24px，硬编码 20 会把 "AI" 裁成 "A]"。
        布局与绘制都从本方法取值，改字号不会再裁标签。
        字体缺失/测量失败时退回 WHO_W。
        """
        try:
            fm = QFontMetrics(bubble_font(self, self._row_base_size()))
        except Exception:
            return self.WHO_W
        return max(self.WHO_W,
                   max(fm.horizontalAdvance(role_label(r)) for r in _ROLE_LABEL_KEYS))

    def _row_base_size(self) -> int:
        """两行字幕的基准字号（配置 font_size_bar，默认 13）。"""
        return int(getattr(self._bar.cfg, "font_size_bar", 13) or 13)

    def _height_budget(self) -> int:
        """卡片可用的最大像素高度（取条上方/下方**较大**的一侧）。

        H61-C 的根：行数预算必须先由屏幕可用高度反算，布局才不会越界。旧实现先按
        固定 14 行排版、再在 _sync_pos 里"放不下就钳制" —— Step 0 实测极端屏
        （1920x360 + 字号 20）下 420x888 的卡片有 **59% 掉在屏外**，就是那些「吞行」。
        拿不到屏幕（离屏/异常）时退回保守值，保证仍有卡片可显示。
        """
        bar = self._bar
        try:
            avail = bar.screen().availableGeometry()
        except Exception:
            return 480
        gap = 6
        above = bar.y() - avail.top() - gap
        below = (avail.top() + avail.height()) - (bar.y() + bar.height()) - gap
        # 再扣掉内边距与"已省略 N 行"标记那一行的高度：预算必须把标记本身也算进去，
        # 否则截断时卡片会比屏幕高出几十像素（新用例当场抓到的第二种越屏）。
        reserve = 2 * self.PAD_Y + self.EXTRA_LINE_H
        if self._thinking:
            # H71-W2：思考行也是卡片里实打实的一行，同样要占预算。这里按**真实字体
            # 度量**算（大字号下 note/思考行比 EXTRA_LINE_H 高，26 只是小字号的近似），
            # 否则思考态 + 大字号会把卡片顶出可用高度。
            base = int(getattr(self._bar.cfg, "font_size_bar", 13) or 13)
            kfm = QFontMetrics(bubble_font(self, max(8, base - 2)))
            reserve += int(kfm.lineSpacing()) + 2 * self.ROW_PAD_Y
        return max(120, max(above, below) - self._tail_h_px - reserve)

    def _relayout_card(self) -> None:
        """两行字幕式排版：逐行折行 + **按屏高反算行数** + 高度自适应。

        H61-A/C：自动展开 —— 行数上限来自屏幕可用高度（当前轮优先，上一轮至少保住
        1 行），放得下就全显；真放不下才截断，并在卡片底部加一行「已省略 N 行」标记
        （以前的静默截断正是用户说的「文字被清掉」）。
        self._expanded 只表示**手动收起/再展开**：False = 紧凑的 3 行视图（点击收起），
        True = 自动预算（默认就是这个状态）。

        H71-M2：每段的行数上限 = min(屏高反算的预算, AUTO_MAX_LINES)，合计不超过
        2*AUTO_MAX_LINES —— 旧实现只夹合计、单段能排到 47 行，与常量名/注释不符。
        """
        base = int(getattr(self._bar.cfg, "font_size_bar", 13) or 13)
        width = self._card_width()
        # 像素主题：当前轮末行尾部本来要画 ▼，正文折行宽度预留标记位。
        # 语音模式（卡片只在这种模式下出现）已取消光标 ⇒ 不再预留。
        feat_pixel = get_feature("pixel") if getattr(self._bar, "_pixel_mode", False) else None
        marker_extra = (feat_pixel.MARKER_EXTRA
                        if (feat_pixel is not None and not self._cursor_cancelled())
                        else 0)
        text_w = max(24, width - 2 * self.ROW_PAD_X - self.BAND_W
                     - self.BAND_GAP - self._who_w() - self.WHO_GAP - marker_extra)
        cur_fm = QFontMetrics(bubble_font(self, base))
        prev_fm = QFontMetrics(bubble_font(self, max(8, base - 1)))
        budget = int((self._height_budget() - 2 * self.PAD_Y)
                     / max(1.0, cur_fm.lineSpacing()))
        budget = min(max(self.AUTO_MIN_LINES, budget), self.AUTO_MAX_LINES * 2)
        measured = []
        for spec in self._rows_spec:
            current = bool(spec.get("current"))
            fm = cur_fm if current else prev_fm
            measured.append((spec, current, fm,
                             wrap_lines(fm, str(spec.get("text", "")), text_w)))
        need_cur = sum(len(l) for _s, c, _f, l in measured if c)
        # 逐段硬上限（H71-M2）：当前轮优先拿预算，但两段谁都不超过 AUTO_MAX_LINES
        cur_limit = min(need_cur, max(self.AUTO_MIN_LINES, budget - 1), self.AUTO_MAX_LINES)
        prev_limit = min(max(1, budget - cur_limit), self.AUTO_MAX_LINES)
        built: list[dict] = []
        total = 2 * self.PAD_Y
        omitted_total = 0
        for i, (spec, current, fm, lines) in enumerate(measured):
            limit = ((self.CARD_MAX_LINES if current else self.PREV_MAX_LINES)
                     if not self._expanded
                     else (cur_limit if current else prev_limit))
            n_lines = len(lines)
            omitted = max(0, n_lines - limit)
            if omitted and current and self._expanded:
                # 当前轮可滚（长回复看全文）：先跳 _scroll_skip 行，再取 limit 行。
                # skip=0 即显示最新尾部（跟随流式更新），向上滚看更早的内容
                skip = min(max(0, self._scroll_skip), omitted)
                self._scroll_skip = skip
                start = n_lines - limit - skip
                lines = list(lines[start:start + limit])
                self._scroll_above = start      # 上方还有 start 行
                self._scroll_below = skip       # 下方还有 skip 行（更靠后的内容）
                omitted_total += omitted
            elif omitted:
                omitted_total += omitted
                self._scroll_above = 0
                self._scroll_below = 0
                lines = (take_tail_lines(fm, lines, text_w, limit)
                         if self._keep_tail(current, bool(spec.get("final", True)))
                         else take_head_lines(fm, lines, text_w, limit))
            else:
                self._scroll_above = 0
                self._scroll_below = 0
            lh = fm.lineSpacing()
            row_h = int(len(lines) * lh) + 2 * self.ROW_PAD_Y
            built.append({
                "lines": lines, "lh": lh, "h": row_h,
                "size": base if current else max(8, base - 1),
                "struck": bool(spec.get("struck")),
                "role": str(spec.get("role", "")),
                "current": current,
                "final": bool(spec.get("final", True)),
            })
            total += row_h
            if i:
                total += self.SEP_H
        # 明确告诉用户"这里少了几行"：静默截断＝用户口中的「文字被清掉」。
        # 单独存字段、不塞进 _rows：_rows 的「最后一行 = 当前轮」是既有契约
        #（浮条与多份用例都按它取行），塞一行会把契约改掉。
        self._note = ""
        if omitted_total:
            if self._scroll_above or self._scroll_below:
                # 可滚动：报"往哪边还有多少行"，而不是"屏幕放不下"——内容并没丢，
                # 只是这一屏装不下（滚轮翻看）。
                parts = []
                if self._scroll_above:
                    parts.append(t("bubble.scroll.up", count=self._scroll_above))
                if self._scroll_below:
                    parts.append(t("bubble.scroll.down", count=self._scroll_below))
                self._note = " · ".join(parts) + t("bubble.scroll.suffix")
            elif self._expanded:
                self._note = t("bubble.omitted.no_room", count=omitted_total)
            else:
                self._note = t("bubble.omitted.expand", count=omitted_total)
        if self._toast:
            tsize = max(8, base - 2)
            tfm = QFontMetrics(bubble_font(self, tsize))
            t_text = elide_right(tfm, self._toast, text_w + self._who_w() + self.WHO_GAP)
            t_lh = tfm.lineSpacing()
            built.append({
                "lines": [t_text], "lh": t_lh,
                "h": int(t_lh) + 2 * self.ROW_PAD_Y, "size": tsize,
                "struck": False, "role": "", "current": False, "final": True,
                "toast": True,
            })
            total += built[-1]["h"] + self.SEP_H
        # H71-W2：思考行（独立字段，不进 _rows）。排在正文之后、省略标记之前 ——
        # 「思考中…」是当下正在发生的事，比"少了几行"的说明更该先被看到。
        self._thinking_size = max(8, base - 2)
        self._thinking_h = 0
        if self._thinking:
            kfm = QFontMetrics(bubble_font(self, self._thinking_size))
            self._thinking_h = int(kfm.lineSpacing()) + 2 * self.ROW_PAD_Y
            total += self._thinking_h + (self.SEP_H if built else 0)
        self._note_size = max(8, base - 2)
        self._note_h = 0
        if self._note:
            nfm = QFontMetrics(bubble_font(self, self._note_size))
            self._note_h = int(nfm.lineSpacing()) + 2 * self.ROW_PAD_Y
            total += self._note_h + (self.SEP_H if (built or self._thinking) else 0)
        self._rows = built
        self.resize(width, total + self._tail_h_px)
        self._stop_marquee()

    def _sync_pos(self) -> None:
        """定位到悬浮窗正上方居中；上方放不下时翻到下方（尾巴朝上）。

        多行卡片比原来的单行气泡高得多，悬浮条贴近屏幕上沿时更容易顶出去，
        故这里补一条翻转分支（原来只做 max(0, ...) 钳制，会压住悬浮条）。
        """
        # H61-C：行数预算已按「较大的一侧」算过（_height_budget），这里只需挑那一侧；
        # 不再出现"上下都放不下就钳制"——那条分支正是 Step 0 量到的 59% 出屏。卡片
        # 比整屏还高（被 AUTO_MAX_LINES 挡住，理论上不会）时贴屏顶，不越上边界。
        bar = self._bar
        x = bar.x() + (bar.width() - self.width()) // 2
        screen = bar.screen().availableGeometry() if bar.screen() else None
        top = screen.top() if screen is not None else 0
        bottom = (screen.top() + screen.height()) if screen is not None else 10 ** 6
        # 横向也要钳制：气泡比条宽时"相对条居中"会把它推到屏幕外（新用例抓到 x=-98）
        if screen is not None:
            x = min(max(x, screen.left()), screen.left() + screen.width() - self.width())
        above = bar.y() - self.height() - 6
        below = bar.y() + bar.height() + 6
        if above >= top:
            self._tail_up = False
            self.move(x, above)
        elif below + self.height() <= bottom:
            self._tail_up = True
            self.move(x, below)
        else:
            self._tail_up = False
            self.move(x, max(top, above))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # H72（H71 真机残留加固）：**显式清屏**。本控件是顶层 + WA_TranslucentBackground，
        # 而 paintEvent 以前从不主动清除背景，只画圆角路径 + 尾巴 —— 一旦本帧落笔区域
        # 没盖住上一帧的字（内容变短/重排/只补画了脏区），旧像素就留在窗口 surface 上，
        # 真机表现正是「字叠在一起 / 半个字」。这里先用 Source 合成模式把整块控件矩形
        # 填成透明，把上一帧擦干净，再**立刻**恢复 SourceOver：不恢复的话后续圆角、描边、
        # 文字、尾巴都会按 Source 覆盖写（抗锯齿边缘被硬写、卡片半透明底色被抹平），外观会变。
        # 选 fillRect(..., Qt.transparent) 而不是 eraseRect()：eraseRect 依赖控件背景
        #（autoFillBackground / 背景角色），在半透明顶层窗口上各平台语义不一致；
        # Source 填透明是同一件事的显式写法，并且保证 alpha 也被真正清零。
        # 注意：**没有**连带关闭 Qt 自身的背景擦除（WA_OpaquePaintEvent 未设），这里只是
        # 在 Qt 之外再补一次整窗透明化，防止局部重绘把上一帧留在窗口缓冲里。
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        w, h = self.width(), self.height()
        # 尾巴朝上（气泡翻到悬浮条下方）时，气泡体从尾巴高度起算
        top = self._tail_h_px if self._tail_up else 0
        body_h = h - self._tail_h_px
        # 像素主题：阶梯边框体 + 四级金字塔尾巴（关闭抗锯齿的硬边像素观感）
        feat_pixel = get_feature("pixel") if getattr(self._bar, "_pixel_mode", False) else None
        if feat_pixel is not None:
            feat_pixel.draw_bubble(self, painter)
        else:
            # 气泡体（圆角矩形 + 描边）
            rect = QRectF(0.5, top + 0.5, w - 1, body_h - 1)
            path = QPainterPath()
            path.addRoundedRect(rect, 10, 10)
            # 小三角：默认朝下指向悬浮窗；翻到下方时朝上
            cx = w / 2
            tail = QPainterPath()
            if self._tail_up:
                tail.moveTo(cx - self._tail_w_px / 2, top + 1)
                tail.lineTo(cx + self._tail_w_px / 2, top + 1)
                tail.lineTo(cx, 1)
            else:
                tail.moveTo(cx - self._tail_w_px / 2, top + body_h - 1)
                tail.lineTo(cx + self._tail_w_px / 2, top + body_h - 1)
                tail.lineTo(cx, h - 1)
            tail.closeSubpath()
            path = path.united(tail)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._bg)
            painter.drawPath(path)
            painter.setPen(QPen(self._border, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        size = int(getattr(self._bar.cfg, "font_size_bar", 13) or 13)
        # 卡片模式（语音模式）：逐行绘制，打断时整段划线
        if self._card and self._rows:
            self._paint_card(painter, top)
            painter.end()
            return

        # 文本（单行，垂直居中于气泡体）；loading 模式前置转圈随 tick 旋转
        painter.setFont(bubble_font(self, size))
        painter.setPen(self._text_color)
        text_x = self.PAD_X
        if self._loading:
            pix = spinner_pixmap(
                self.LOADING_SPINNER_D, self._text_color.name(), self._spinner_angle
            )
            painter.drawPixmap(
                self.PAD_X, int(top + (body_h - self.LOADING_SPINNER_D) / 2), pix
            )
            text_x = self.PAD_X + self.LOADING_SPINNER_D + self.LOADING_GAP
        # 文本绘制：跑马灯模式裁剪到气泡体后循环滚动，否则静态单行
        if self._marquee and not self._loading:
            painter.save()
            clip_left = text_x
            clip_w = w - text_x - self.PAD_X
            painter.setClipRect(int(clip_left), int(top), int(clip_w), int(body_h))
            metrics = QFontMetrics(painter.font())
            tw = metrics.horizontalAdvance(self._elided)
            gap = 28
            period = tw + gap
            offset = int(self._marquee_phase) % period
            x = text_x - offset
            for dx in (x - period, x, x + period):
                painter.drawText(
                    int(dx), int(top), tw, int(body_h),
                    Qt.AlignVCenter | Qt.AlignLeft, self._elided,
                )
            painter.restore()
        else:
            painter.drawText(
                text_x, int(top), w - text_x - self.PAD_X, int(body_h),
                Qt.AlignVCenter | Qt.AlignLeft, self._elided,
            )
            # 文本尾部的未定稿标记（loading 转圈/跑马灯滚动时不画）：
            #   像素主题 → DQ 式金色 ▼；其他主题 → 竖线光标（|）。
            # 两者都由 _blink_timer 翻转 _blink_on 驱动闪烁，语义一致：
            # "这段文字还没定稿"。此前非像素主题用文字尾部 "..."，
            # 用户反馈「其他主题的话，尾部可以改成竖线光标闪烁的那种」。
            # **语音模式（对话四态）整体不画**：见 _cursor_cancelled。
            if (not self._loading and self._elided
                    and not self._cursor_cancelled()):
                fm_mark = QFontMetrics(painter.font())
                tw = fm_mark.horizontalAdvance(self._elided)
                bx = text_x + tw + 8
                if bx + 8 <= w - self.PAD_X:
                    if feat_pixel is not None:
                        feat_pixel.draw_more_marker(
                            painter, self, bx, top + body_h / 2, self._blink_on)
                    else:
                        # 高度取字高的 CARET_H_RATIO、宽度取 CARET_W（用户诉求
                        # 「小一点」→「再小一点 细一点」）：与气泡体高度再取一次
                        # min，极小字号/极扁气泡下不越界
                        draw_caret_marker(
                            painter, bx, top + body_h / 2,
                            min(round(fm_mark.height() * self.CARET_H_RATIO),
                                body_h - 6),
                            self._blink_on, painter.pen().color(),
                            width=self.CARET_W)
        painter.end()

    def _paint_card(self, painter: QPainter, top: int) -> None:
        """两行字幕式卡片：角色色带 + 角色标签 + 正文（打断整行划线）。

        上一轮整体压暗（色带 110、文字 120）表示"这是刚才那句"；当前轮中间
        结果半透明、定稿全不透明，与录音态同一套视觉语言。

        **不画未定稿光标**：卡片只出现在语音模式里，而语音模式已整体取消光标
        （见 _cursor_cancelled）——从前挂在当前轮末行的金色 ▼ 已移除。
        """
        w = self.width()
        y = top + self.PAD_Y
        feat_pixel = get_feature("pixel") if getattr(self._bar, "_pixel_mode", False) else None
        for i, row in enumerate(self._rows):
            if i:
                painter.setPen(QPen(self._border, 1))
                painter.drawLine(self.ROW_PAD_X, int(y), w - self.ROW_PAD_X, int(y))
                y += self.SEP_H
            # 角色色带（像素主题画直角，其余 1.5px 圆角）
            band = QColor(ROLE_BAND_COLORS.get(row["role"], "#8a8a94"))
            if not row["current"]:
                band.setAlpha(110)
            painter.setPen(Qt.NoPen)
            painter.setBrush(band)
            band_rect = QRectF(self.ROW_PAD_X, y + 4, self.BAND_W,
                               max(4, row["h"] - 8))
            if feat_pixel is not None:
                painter.drawRect(band_rect)
            else:
                painter.drawRoundedRect(band_rect, 1.5, 1.5)
            if row.get("toast"):
                # 临时提示行：右对齐小字，不占角色列
                painter.setFont(bubble_font(self, row["size"]))
                tc = QColor(self._text_color)
                tc.setAlpha(170)
                painter.setPen(tc)
                painter.drawText(self.ROW_PAD_X, int(y),
                                 w - 2 * self.ROW_PAD_X, int(row["h"]),
                                 Qt.AlignRight | Qt.AlignVCenter,
                                 row["lines"][0])
                y += row["h"]
                continue
            band_x = self.ROW_PAD_X + self.BAND_W + self.BAND_GAP
            # 角色标签
            painter.setFont(bubble_font(self, max(8, row["size"] - 2)))
            who = QColor(self._text_color)
            who.setAlpha(150)
            painter.setPen(who)
            painter.drawText(band_x, int(y), self._who_w(), int(row["h"]),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             role_label(row["role"]))
            # 正文
            color = QColor(self._text_color)
            if not row["current"]:
                color.setAlpha(120)          # 上一轮压暗
            elif not row["final"]:
                color.setAlpha(160)          # 中间结果半透明
            font = bubble_font(self, row["size"])
            font.setStrikeOut(row["struck"])  # 被打断的那条整行划线
            painter.setFont(font)
            painter.setPen(color)
            tx = band_x + self._who_w() + self.WHO_GAP
            ty = y + self.ROW_PAD_Y
            for line in row["lines"]:
                painter.drawText(int(tx), int(ty), int(w - tx - self.ROW_PAD_X),
                                 int(row["lh"]),
                                 Qt.AlignLeft | Qt.AlignVCenter, line)
                ty += row["lh"]
            y += row["h"]
        if self._thinking:
            # H71-W2：思考行。左对齐（跟正文同一起读线），字号同 note、略亮一点，
            # 位置在正文之后、省略标记之前。窄卡片时不省略（"思考中…" 只有 4 字，
            # 且宽度已由 CARD_MIN_AVAIL_W 保底）。
            if self._rows:
                y += self.SEP_H
            painter.setFont(bubble_font(self, self._thinking_size))
            kc = QColor(self._text_color)
            kc.setAlpha(200)
            painter.setPen(kc)
            painter.drawText(self.ROW_PAD_X, int(y), w - 2 * self.ROW_PAD_X,
                             int(self._thinking_h),
                             Qt.AlignLeft | Qt.AlignVCenter, self._thinking)
            y += self._thinking_h + self.SEP_H
        if self._note:
            # H61：截断不再静默 —— 卡片底部一行小字说明少了几行、怎么看到全部
            if self._rows and not self._thinking:
                y += self.SEP_H
            painter.setFont(bubble_font(self, self._note_size))
            tc = QColor(self._text_color)
            tc.setAlpha(170)
            painter.setPen(tc)
            painter.drawText(self.ROW_PAD_X, int(y), w - 2 * self.ROW_PAD_X,
                             int(self._note_h),
                             Qt.AlignRight | Qt.AlignVCenter, self._note)

    # ---- 气泡交互（语音模式卡片）----

    def _current_text(self) -> str:
        """当前这一轮的完整文本（不含上一轮）。"""
        for spec in reversed(self._rows_spec):
            if spec.get("current"):
                return str(spec.get("text", ""))
        return ""

    def _all_text(self) -> str:
        """两行拼成一段（带角色标签），便于贴到别处。"""
        parts = []
        for spec in self._rows_spec:
            label = role_label(str(spec.get("role", "")))
            parts.append((label + t("bubble.role.sep") if label else "")
                         + str(spec.get("text", "")))
        return "\n".join(parts)

    def _copy(self, text: str, hint: str) -> None:
        if not text:
            return
        QApplication.clipboard().setText(text)
        try:
            self._bar.signals.transient_hint.emit(hint, 1.2)
        except Exception:
            logger.debug("复制提示 emit 失败，忽略", exc_info=True)

    def _on_single_click(self) -> None:
        """单击 = 展开/收起全文（纯显示动作，无副作用）。"""
        if not self._card:
            return
        try:
            self._bar.toggle_dialog_expand()
        except Exception:
            logger.debug("切换气泡展开态失败，忽略", exc_info=True)

    def wheelEvent(self, event) -> None:
        """滚轮翻阅当前轮的长回复（每格 3 行）。

        只在「自动展开的卡片」里有意义：收起态是刻意的紧凑视图，非卡片态根本没有
        多行可滚。越界由 _relayout_card 按实际行数钳制（滚不出去）。
        """
        if not (self._card and self._expanded):
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        # 向上滚 = 看更早的内容（skip 增大），向下滚 = 往最新回（skip 减小）
        self._scroll_skip = max(0, self._scroll_skip + (3 if delta > 0 else -3))
        self._relayout()
        self._sync_pos()
        # 必须**整窗**重画，不能用 update()（= 标脏等合并的局部重绘）：滚动会让整块
        # 内容平移，而本控件是顶层 + WA_TranslucentBackground，只补画脏区时会留下
        # "文字被砍了一半"的残影——H71-W3 真机反馈过同一现象（见 _force_full_repaint
        # 的 docstring：离屏环境复现不了，只有真机合成器才暴露）。
        self._force_full_repaint()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if self._card and event.button() == Qt.LeftButton:
            # 先等一个双击间隔：双击是"复制本轮"，不能顺带把展开态也翻掉
            self._click_timer.start(QApplication.doubleClickInterval())
        elif self._card and event.button() == Qt.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._card and event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._copy(self._current_text(), t("bubble.copied.turn"))
        super().mouseDoubleClickEvent(event)

    def _show_menu(self, pos) -> None:
        """气泡右键菜单（复制/展开）。纯展示窗，不提供任何破坏性操作。"""
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:%s;color:%s;border:1px solid %s;padding:4px;}"
            "QMenu::item{padding:5px 18px;border-radius:4px;}"
            "QMenu::item:selected{background:%s;}"
            % (self._bg.name(), self._text_color.name(), self._border.name(),
               self._border.name())
        )
        act_copy = menu.addAction(t("bubble.menu.copy_turn"))
        act_all = menu.addAction(t("bubble.menu.copy_all"))
        menu.addSeparator()
        act_expand = menu.addAction(t("bubble.menu.collapse") if self._expanded
                                    else t("bubble.menu.expand"))
        chosen = menu.exec(pos)
        if chosen is act_copy:
            self._copy(self._current_text(), t("bubble.copied.turn"))
        elif chosen is act_all:
            self._copy(self._all_text(), t("bubble.copied.all"))
        elif chosen is act_expand:
            self._on_single_click()

    # ---- 气泡跑马灯（长确认文本宽度不足时循环滚动） ----

    def _start_marquee(self) -> None:
        """启动滚动定时器（30ms/帧，仅文字溢出且非 loading 时运行）。"""
        if self._marquee_timer is None:
            self._marquee_timer = QTimer(self)
            self._marquee_timer.setInterval(30)
            self._marquee_timer.timeout.connect(self._on_marquee_tick)
        if not self._marquee_timer.isActive():
            self._marquee_timer.start()

    def _stop_marquee(self) -> None:
        if self._marquee_timer is not None and self._marquee_timer.isActive():
            self._marquee_timer.stop()

    def _on_marquee_tick(self) -> None:
        self._marquee_phase += 1.5  # 每 30ms 前进 1.5px ≈ 50px/s
        self.update()


class ConfirmButtons(QWidget):
    """「修正后上屏」确认按钮条：悬浮在悬浮条下方居中并随其移动，配色按主题适配，点按钮回调悬浮条发信号。"""

    def __init__(self, bar: "FloatingBar"):
        super().__init__(None)
        self._bar = bar
        self._bg = QColor(28, 28, 32, 235)
        self._border = QColor(255, 255, 255, 50)
        self._text = QColor("#eeeeee")
        self.setObjectName("confirmBtns")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus  # 纯按钮条，不抢输入焦点
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(6)
        self._btn_yes = QPushButton(t("common.confirm"), self)
        self._btn_no = QPushButton(t("common.not_now"), self)
        self._btn_yes.clicked.connect(lambda: self._bar._confirm_answered(True))
        self._btn_no.clicked.connect(lambda: self._bar._confirm_answered(False))
        lay.addWidget(self._btn_yes)
        lay.addWidget(self._btn_no)
        self.hide()

    def set_labels(self, yes: str, no: str) -> None:
        """按钮文字（开启/暂不 或 确认/取消…）。"""
        self._btn_yes.setText(yes)
        self._btn_no.setText(no)

    def show(self) -> None:
        """显示并重断言置顶（隐藏再显示会掉到置顶组底部，见 reassert_topmost）。"""
        super().show()
        reassert_topmost(self)

    def set_colors(self, bg: QColor, text: QColor) -> None:
        """按当前主题上色：容器沿用悬浮条底色、描边按明暗派生；
        按钮统一近白实底深色字，赛博朋克保留霓虹青实底（委托特性模块）。"""
        self._bg = QColor(bg)
        light = self._bar._is_light(self._bg.name())
        self._border = QColor(0, 0, 0, 45) if light else QColor(255, 255, 255, 50)
        self._text = QColor(text)
        self.setStyleSheet(
            f"QWidget#confirmBtns{{background:{self._bg.name(QColor.HexArgb)};"
            f" border:1px solid {self._border.name(QColor.HexArgb)};"
            f" border-radius:8px;}}"
        )
        # 中性主题统一近白实底+深色字；赛博朋克（top_line 标记）走专属填充
        from .theme_features import get_feature
        feat = (get_feature("cyberpunk")
                if getattr(self._bar, "_top_line_color", None) is not None else None)
        if feat is not None:
            fill, btn_fg = feat.confirm_fill(self._bar)
            fill_hover = fill.lighter(115)
        else:
            fill = QColor("#ededef")   # 近白实底
            btn_fg = QColor("#161616")  # 深色加粗字
            fill_hover = fill.darker(110)
        fill_pressed = fill.darker(116)
        for b in (self._btn_yes, self._btn_no):
            b.setStyleSheet(
                f"QPushButton{{background:{fill.name()};"
                f" border:1px solid {fill.darker(88).name()}; border-radius:5px;"
                f" color:{btn_fg.name()}; font-weight:bold;"
                f" padding:3px 16px; font-size:12px;}}"
                f"QPushButton:hover{{background:{fill_hover.name()};}}"
                f"QPushButton:pressed{{background:{fill_pressed.name()};}}"
            )
        self.update()

    def _sync_pos(self) -> None:
        """锚定悬浮条正下方居中；贴屏幕底部时改放到上方。"""
        bar = self._bar
        self.adjustSize()
        x = bar.x() + (bar.width() - self.width()) // 2
        y = bar.y() + bar.height() + 6
        screen = bar.screen().availableGeometry() if bar.screen() else None
        if screen is not None and y + self.height() > screen.bottom():
            y = bar.y() - self.height() - 6
        self.move(x, y)
