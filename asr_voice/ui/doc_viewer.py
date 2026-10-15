"""内置用户文档：随包分发的 Markdown 说明书 + 离线阅读弹窗。

为什么自带一份而不是只跳 GitHub：丢网/没开浏览器的机器上照样能查到用法，
且包里的文档与当前版本严格对应（在线 main 分支可能比用户手上的版本新）。

为什么自己写 Markdown 渲染而不走 QTextBrowser.setMarkdown：Qt 的内置
Markdown 子集**不支持表格**，而本说明书有 400+ 行全是表格（快捷键表、故障
排查表），用 setMarkdown 会把表格渲染成一堆竖线原文。在线那种完整 CommonMark
规范用不上，够覆盖本仓库两份文档的写法即可。
"""

from __future__ import annotations

import html
import logging
import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

from ..i18n import t

logger = logging.getLogger(__name__)

# 语言 → 文档文件名
DOC_FILES = {
    "zh": "User_Doc_zh.md",
    "en": "User_Doc_en.md",
}
DOC_DIR = "docs"


def doc_path(lang: str | None = None) -> Path | None:
    """定位内置文档：打包版读解包目录，源码版读仓库 docs/。

    返回 None = 这台机器上没有随包文档（干净源码克隆缺文件、或精简包），
    调用方据此退化到「在线文档」按钮。
    """
    from ..i18n import language

    name = DOC_FILES.get(lang or language()) or DOC_FILES["zh"]
    candidates = []
    # PyInstaller（onedir/onefile 都有）把 datas 解到 sys._MEIPASS 下
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / DOC_DIR / name)
    candidates.append(Path(sys.executable).resolve().parent / DOC_DIR / name)
    # 源码运行：asr_voice/ui/<本文件> → parents[2] 是仓库根
    candidates.append(Path(__file__).resolve().parents[2] / DOC_DIR / name)
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def read_doc(lang: str | None = None) -> str | None:
    """读出文档正文；失败/不存在返回 None。"""
    p = doc_path(lang)
    if p is None:
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        logger.exception("内置文档读取失败：%s", p)
        return None


# ---- Markdown → HTML（够用子集）----

_RE_H = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_LI = re.compile(r"^\s*([-*+])\s+(.*)$")
_RE_OLI = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def _inline(text: str) -> str:
    """行内标记：图片 > 代码 > 链接 > 粗体 > 斜体。

    行内代码先占位，避免里面的 * / _ 被当成强调吃到。
    """
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    codes: list[str] = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return re.sub(
        r"\x00(\d+)\x00",
        lambda m: "<code>" + html.escape(codes[int(m.group(1))], quote=False) + "</code>",
        text,
    )


def _split_row(line: str) -> list[str]:
    """拆表格行：去首尾竖线，按竖线切分（行尾缺竖线也兼容）。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def markdown_to_html(md: str) -> str:
    """把 Markdown 转成 HTML 片段（交 QTextBrowser.setHtml 渲染）。

    支持：ATX 标题、围栏代码块、无序/有序列表、GFM 表格、引用、水平线、
    段落（软换行转 <br>）。不支持的写法按纯文本输出，不丢内容。
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0
    n = len(lines)

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    while i < n:
        stripped = lines[i].strip()

        if stripped.startswith("```"):
            flush_para()
            lang_tag = stripped[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过收尾围栏
            body = html.escape("\n".join(buf), quote=False)
            out.append(f"<pre><code>{body}</code></pre>")
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        m = _RE_H.match(stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # GFM 表格：本行含 |，紧随其一是分隔行
        if "|" in stripped and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            flush_para()
            header = _split_row(stripped)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>")
            out.extend(f"<th>{_inline(c)}</th>" for c in header)
            out.append("</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>")
                for idx in range(len(header)):
                    cell = r[idx] if idx < len(r) else ""
                    out.append(f"<td>{_inline(cell)}</td>")
                out.append("</tr>")
            out.append("</tbody></table>")
            continue

        if stripped in ("---", "***", "___"):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>"
                       + "<br>".join(_inline(x) for x in buf)
                       + "</blockquote>")
            continue

        if _RE_OLI.match(stripped):
            flush_para()
            items = []
            while i < n:
                mm = _RE_OLI.match(lines[i])
                if not mm:
                    break
                items.append(_inline(mm.group(2).strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>")
            continue

        if _RE_LI.match(stripped):
            flush_para()
            items = []
            while i < n:
                mm = _RE_LI.match(lines[i])
                if not mm:
                    break
                items.append(_inline(mm.group(2).strip()))
                i += 1
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out)


# ---- 阅读弹窗 ----

_FALLBACK_PALETTE = {
    "text": "#ececef", "secondary": "#a5a5ac", "page": "#1c1c1f",
    "panel": "#242428", "input_bg": "#2b2b31", "input_border": "#494952",
    "border": "#3a3a41", "btn_bg": "#2b2b31", "btn_hover": "#3a3a41",
}


def _palette(cfg) -> dict:
    """沿用设置界面主题的配色，弹窗与设置界面观感一致。"""
    try:
        from .settings_styles import _theme_palette
        return _theme_palette(cfg)
    except Exception:
        return dict(_FALLBACK_PALETTE)


class DocViewerDialog(QDialog):
    """离线文档阅读窗：顶部搜索条 + 正文浏览区。

    正文用 QTextBrowser：自带滚动/选中复制，setOpenExternalLinks(True) 让文档
    里的外链走系统浏览器（说明书里有大量仓库/控制台链接）。
    """

    def __init__(self, parent=None, cfg=None, lang: str | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle(t("set.about.doc_title"))
        self.resize(960, 680)

        pal = _palette(cfg)
        self.setStyleSheet(
            "QDialog { background: " + pal.get("page", "#1c1c1f") + "; }\n"
            "QTextBrowser { background: " + pal.get("panel", "#242428") + ";\n"
            "  color: " + pal.get("text", "#ececef") + ";\n"
            "  border: 1px solid " + pal.get("border", "#3a3a41") + ";\n"
            "  border-radius: 8px; padding: 10px 14px; }\n"
            "QLineEdit { background: " + pal.get("input_bg", "#2b2b31") + ";\n"
            "  color: " + pal.get("text", "#ececef") + ";\n"
            "  border: 1px solid " + pal.get("input_border", "#494952") + ";\n"
            "  border-radius: 6px; padding: 5px 9px; }\n"
            "QPushButton { background: " + pal.get("btn_bg", "#2b2b31") + ";\n"
            "  color: " + pal.get("text", "#ececef") + ";\n"
            "  border: none; border-radius: 6px; padding: 6px 14px; }\n"
            "QPushButton:hover { background: "
            + pal.get("btn_hover", "#3a3a41") + "; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._find = QLineEdit()
        self._find.setPlaceholderText(t("set.about.doc_find"))
        self._find.returnPressed.connect(self._find_next)
        bar.addWidget(self._find, 1)
        next_btn = QPushButton(t("set.about.doc_find_next"))
        next_btn.clicked.connect(self._find_next)
        bar.addWidget(next_btn)
        root.addLayout(bar)

        self._browser = QTextBrowser()
        self._browser.setReadOnly(True)
        self._browser.setOpenExternalLinks(True)
        # 右键换应用主题菜单（默认 win11 菜单带系统图标，与界面不符）；
        # 浏览器形态只留复制/全选，与 Qt 默认菜单一致
        from .settings_dialog import _install_text_menu
        _install_text_menu(self._browser, pal)
        _install_text_menu(self._find, pal)
        root.addWidget(self._browser, 1)

        try:
            self._browser.document().setDefaultStyleSheet(
                "body { font-size: 10pt; }\n"
                "h1 { font-size: 18pt; margin: 14px 0 8px; }\n"
                "h2 { font-size: 15pt; margin: 16px 0 8px; }\n"
                "h3 { font-size: 12pt; margin: 12px 0 6px; }\n"
                "table { border-collapse: collapse; }\n"
                "th, td { border: 1px solid rgba(128,128,128,0.45);\n"
                "  padding: 5px 8px; font-size: 9pt; }\n"
                "th { background: rgba(128,128,128,0.16); }\n"
                "code { background: rgba(128,128,128,0.14); }\n"
                "pre { background: rgba(128,128,128,0.12); margin: 6px 0; }")
        except Exception:
            logger.debug("文档窗字体样式设置失败", exc_info=True)
        self._browser.document().setDocumentMargin(12)

    def load_text(self, markdown: str) -> None:
        self._browser.setHtml(markdown_to_html(markdown))
        self._browser.moveCursor(QTextCursor.MoveOperation.Start)

    def load_file(self, path: Path) -> bool:
        try:
            self.load_text(path.read_text(encoding="utf-8"))
            return True
        except Exception:
            logger.exception("内置文档读取失败：%s", path)
            return False

    def _find_next(self) -> None:
        kw = self._find.text().strip()
        if not kw:
            return
        if not self._browser.find(kw):
            # 找不到就从开头回绕再找一次：文档从头看的用户不该手动滚回去
            self._browser.moveCursor(QTextCursor.MoveOperation.Start)
            self._browser.find(kw)


def open_user_doc(parent=None, cfg=None, lang: str | None = None
                  ) -> DocViewerDialog | None:
    """打开内置使用文档；失败返回 None（由调用方给提示）。

    当前语言档缺失时回落中文版，而不是直接打不开。
    """
    from ..i18n import language

    key = lang or language()
    text = read_doc(key)
    if text is None and key != "zh":
        text = read_doc("zh")
    if text is None:
        logger.warning("未找到内置用户文档")
        return None
    dlg = DocViewerDialog(parent=parent, cfg=cfg, lang=key)
    dlg.load_text(text)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dlg.show()
    return dlg
