"""商店版首次运行欢迎对话框（--hidden 静默启动不弹）。

首启标记与「一次性引导」标记共用 `state_dir()/steam_state.json`：
- `welcome_shown`：欢迎框只弹一次
- `no_key_guided`：未配置密钥首次拒绝录音时的一次性引导

标记走独立文件而非 config.yaml：config.yaml 是用户会手改、会随版本迁移的
东西，往里塞程序内部状态容易在升级时丢失或被误删；而这里的状态丢了最多
只是"再弹一次"，不该有别的副作用。读取失败一律按「没看过」处理——
宁可多弹一次，也不能把用户永久卡在"已看过"的状态里。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QRectF, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QFont,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config.loader import _atomic_write
from ..i18n import t
from ..paths import state_dir
from .icons import app_icon, close_icon, globe_icon, key_icon
from .settings_styles import _theme_palette, _welcome_qss

logger = logging.getLogger(__name__)

# 隐私政策链接：仓库内 docs/PRIVACY_POLICY.md。
# 上架前替换为商店页链接。
PRIVACY_URL = (
    "https://github.com/Open-RealtimeASR-UI/Open-RealtimeASR-UI/"
    "blob/main/docs/PRIVACY_POLICY.md"
)

# 无密钥引导的标记键（与 welcome_shown 同文件、互不影响）
NO_KEY_GUIDE_KEY = "no_key_guided"


def _flag_file() -> Path:
    """标记文件路径。做成函数而非模块常量：state_dir() 依赖安装方式
    （打包版与源码版不同），且调用点可能早于目录初始化。"""
    return state_dir() / "steam_state.json"


def _read_state() -> dict:
    try:
        p = _flag_file()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("首启标记读取失败，按未看过处理", exc_info=True)
        return {}


def _mark(key: str) -> None:
    """写入一个标记。**必须走 loader 的原子写**：这个文件里有多把钥匙
    （welcome_shown / no_key_guided），裸 write_text 一旦被并发读者顶掉
    或写半截，_read_state 会退回 {}，下一次 _mark 就把另一把钥匙冲掉了。
    loader._atomic_write 是 tmp + os.replace，且已经带 Windows 上
    FILE_SHARE_DELETE 缺失导致的 WinError 5 重试（见 e529089）。"""
    try:
        p = _flag_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _read_state()
        data[key] = True
        _atomic_write(p, json.dumps(data, ensure_ascii=False))
    except Exception:
        # 写不进去不影响本次使用，下次启动会再弹一次
        logger.warning("首启标记写入失败（下次启动会再弹一次）", exc_info=True)


def should_show_welcome(steam: bool, hidden: bool) -> bool:
    """欢迎框该不该弹：仅 商店版，且非 --hidden 静默启动。

    抽成纯函数是因为这两条门禁写在 run() 里就完全不可测（要造整个
    VoiceApp），而「开机自启时不弹」恰恰是最容易被改坏的一条：自启
    路径和主路径共用 run()，改错就会让用户每次开机都弹一次。
    """
    return bool(steam) and not bool(hidden)


def is_first_run() -> bool:
    """欢迎框是否还没弹过。"""
    return not bool(_read_state().get("welcome_shown"))


def mark_shown() -> None:
    _mark("welcome_shown")


def is_guided(key: str) -> bool:
    """某个一次性引导是否已弹过。"""
    return bool(_read_state().get(key))


def mark_guided(key: str) -> None:
    _mark(key)


def _rounded_pixmap(icon, size: int, radius: int):
    """图标裁成圆角瓷砖（hero 区用），手法同设置弹窗关于卡。"""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, icon.pixmap(size, size))
    p.end()
    return out


def _pretty_hotkey(raw: str) -> str:
    """热键原文（CTRL+G）转键帽显示形（Ctrl+G）：逐段首字母大写。"""
    pretty = "+".join(part.capitalize() for part in raw.split("+") if part)
    return pretty or raw


class WelcomeDialog(QDialog):
    """首启欢迎框：无边框主题弹窗 + hero + 三张引导卡 + 隐私政策/主次按钮。

    视觉与设置弹窗同源：_theme_palette 按 ui.settings_style 选配色（默认
    minimal_dark），_welcome_qss 生成样式表；字体栈与 pt 字号/字重层级约定
    同 _settings_qss。窗口不透明——透明表面会禁 ClearType 致文字发糊，
    Win11 圆角由 DWM 裁切、Win10 方角，均与设置弹窗一致。

    标题栏只做拖动区 + 关闭钮（标题文字由 hero 区承担）；隐私政策用次级色
    纯文本按钮而非富文本链接——链接蓝色 QSS 管不住。
    """

    def __init__(self, parent=None, on_open_settings: Optional[Callable[[], None]] = None,
                 hotkey: str = "", cfg=None):
        super().__init__(parent)
        self._on_open_settings = on_open_settings
        self._drag_pos = None

        self.setWindowTitle(t("welcome.title"))
        # 必须与悬浮条同级置顶：bar 带 WindowStaysOnTopHint，普通 Dialog 会被它
        # 盖住（2026-09-26 首启实测：欢迎窗整窗藏在 bar 后面）。同为 topmost 时
        # 后 show 者在上——bar 先 show、本窗后 show/exec，稳压器在上一层。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog
                            | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(500)

        # 字体：与设置弹窗同一固定栈（不跟随用户自定义字体——单字重用户字体
        # 撑不起 400/500/700 层级），基础 10.5pt
        font = QFont()
        font.setFamilies(["Alibaba PuHuiTi 3.0", "Segoe UI Variable Text",
                          "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        font.setPointSizeF(10.5)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(font)

        self._palette = _theme_palette(cfg)
        self.setStyleSheet(_welcome_qss(self._palette))

        root = QWidget(self)
        root.setObjectName("welcomeRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 标题栏：拖动区 + 关闭钮
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(38)
        bar.setCursor(Qt.OpenHandCursor)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 4, 8, 0)
        bl.setSpacing(0)
        bl.addStretch(1)
        close = QPushButton()
        close.setObjectName("btnClose")
        close.setFixedSize(30, 30)
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(close_icon(self._palette["secondary"], 14))
        close.clicked.connect(self.reject)
        bl.addWidget(close)
        lay.addWidget(bar)

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

        bar.mousePressEvent = _title_mouse_press
        bar.mouseMoveEvent = _title_mouse_move
        bar.mouseReleaseEvent = _title_mouse_release

        # hero：圆角应用图标 + 主标题 + 副标题
        hero = QVBoxLayout()
        hero.setContentsMargins(0, 2, 0, 6)
        hero.setSpacing(6)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(_rounded_pixmap(app_icon(), 52, 12))
        hero.addWidget(icon, 0, Qt.AlignCenter)
        title = QLabel(t("welcome.title"))
        title.setObjectName("heroTitle")
        title.setAlignment(Qt.AlignCenter)
        hero.addWidget(title)
        sub = QLabel(t("welcome.subtitle"))
        sub.setObjectName("heroSub")
        sub.setAlignment(Qt.AlignCenter)
        hero.addWidget(sub)
        lay.addLayout(hero)

        # 三张引导卡：热键 keycap / 地球 / 钥匙 + 小标题 + 说明
        cards = QVBoxLayout()
        cards.setContentsMargins(24, 8, 24, 4)
        cards.setSpacing(10)
        specs = (
            ("keycap", "welcome.card_hotkey_title", "welcome.card_hotkey_desc"),
            ("globe", "welcome.card_lang_title", "welcome.card_lang_desc"),
            ("key", "welcome.card_key_title", "welcome.card_key_desc"),
        )
        for kind, title_key, desc_key in specs:
            card = QFrame()
            card.setObjectName("card")
            cl = QHBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(12)
            if kind == "keycap":
                tile = QLabel(_pretty_hotkey(hotkey))
                tile.setObjectName("tileKey")
                tile.setFixedSize(52, 36)
                tile.setAlignment(Qt.AlignCenter)
            else:
                tile = QLabel()
                tile.setObjectName("tile")
                tile.setFixedSize(36, 36)
                tile.setAlignment(Qt.AlignCenter)
                paint = globe_icon if kind == "globe" else key_icon
                tile.setPixmap(paint(self._palette["secondary"]))
            cl.addWidget(tile, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(3)
            ctitle = QLabel(t(title_key))
            ctitle.setObjectName("cardTitle")
            cdesc = QLabel(t(desc_key))
            cdesc.setObjectName("cardDesc")
            cdesc.setWordWrap(True)
            col.addWidget(ctitle)
            col.addWidget(cdesc)
            cl.addLayout(col, 1)
            cards.addWidget(card)
        lay.addLayout(cards)

        # 底栏：隐私政策（次级色文本按钮）+ 次按钮 + 主按钮
        foot = QHBoxLayout()
        foot.setContentsMargins(24, 10, 20, 18)
        foot.setSpacing(10)
        privacy = QPushButton(t("welcome.privacy"))
        privacy.setObjectName("privacyLink")
        privacy.setCursor(Qt.PointingHandCursor)
        privacy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PRIVACY_URL)))
        foot.addWidget(privacy, 0, Qt.AlignVCenter)
        foot.addStretch(1)
        settings = QPushButton(t("welcome.go_settings"))
        settings.setObjectName("btnGhost")
        settings.setCursor(Qt.PointingHandCursor)
        settings.clicked.connect(self._open_settings)
        start = QPushButton(t("welcome.start"))
        start.setObjectName("btnPrimary")
        start.setDefault(True)
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self.accept)
        foot.addWidget(settings)
        foot.addWidget(start)
        lay.addLayout(foot)

    def _open_settings(self) -> None:
        if self._on_open_settings is not None:
            self._on_open_settings()
        self.accept()
