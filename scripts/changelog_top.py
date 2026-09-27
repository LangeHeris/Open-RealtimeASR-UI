"""提取 CHANGELOG.md 最新版本段，作为 GitHub Release 说明（Plan B spec §5）。

为什么需要它：CHANGELOG.md 是双渠道公告的唯一来源（spec §5），但 Release 的正文
只该是「本次版本」那一段。把整个文件当 body（`body_path: CHANGELOG.md`）会把历史
版本一路带上——首发时看着还行，第二个版本起就是噪音。

顺带做一件 check_version.py 做不到的事：它只比 tag 与代码版本，从不看 changelog。
「忘了写 CHANGELOG 段」于是能一路过 CI，直到发布说明空掉才被发现。`--check` 把顶部
段与 asr_voice/version.py 对齐，把这个检查提前到 CI。

用法：
    python scripts\\changelog_top.py --check               # 顶部段 == 代码版本 → exit 0
    python scripts\\changelog_top.py --out dist\\notes.md  # 写该段落到文件（UTF-8 + LF）
    python scripts\\changelog_top.py                       # 写 stdout

编码：CI 的 stdout 在 Windows runner 上是 cp1252（与 check_version 同因），而
CHANGELOG 正文是中文。所以「写 stdout」走 sys.stdout.buffer 直接落 UTF-8 字节，
绕开文本层编解码器；打印给人看的只有 ASCII 的 [ok]/[FAIL] 行，插值一律过 _a()。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from asr_voice.version import __version__  # noqa: E402

DEFAULT_CHANGELOG = _ROOT / "CHANGELOG.md"

# 版本段 = H2 标题（`## 0.1.0 - 2026-09-20` / `## [0.1.0] - date` / `## 0.1.0`）。
# 刻意只认 H2：H1 是文件标题，H3 是版本段内部的「新增 / 变更」分类，两者都不是段边界。
_HEADING = re.compile(
    r"^##[ \t]+\[?(?P<ver>[^\]\s]+)\]?[ \t]*(?:[-–—][ \t]*(?P<date>.*?))?[ \t]*$",
    re.MULTILINE,
)


def _a(text: object) -> str:
    """把任意文本压成纯 ASCII，供日志输出（见模块 docstring 的编码说明）。"""
    return str(text).encode("ascii", "replace").decode("ascii")


def split_sections(text: str) -> list[tuple[str, str]]:
    """按文件顺序返回 `[(版本号, 该段原文), ...]`；段原文**含**标题行。"""
    matches = list(_HEADING.finditer(text))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group("ver"), text[m.start():end].strip()))
    return sections


def top_version(text: str) -> str | None:
    """最新版本号（文件里第一个版本段）。文件还没有版本段时返回 None。"""
    sections = split_sections(text)
    return sections[0][0] if sections else None


def _strip_heading(section: str) -> str:
    return "\n".join(section.splitlines()[1:]).strip()


def section_body(text: str, version: str | None = None) -> str | None:
    """某版本的正文（标题行已去掉）。`version=None` 取最新段；找不到返回 None。

    标题行去掉是因为 Release 自带的 tag 与日期已经把这两条信息显示出来了，
    正文再抄一遍只会显得重复。
    """
    sections = split_sections(text)
    if version is None:
        return _strip_heading(sections[0][1]) if sections else None
    for ver, section in sections:
        if ver == version:
            return _strip_heading(section)
    return None


def check(text: str, version: str | None = None) -> int:
    """返回退出码：0 = 顶部版本段与 `version`（默认代码内版本）一致。"""
    ver = version or __version__
    top = top_version(text)
    if top is None:
        print("[FAIL] CHANGELOG.md has no '## <version>' section")
        print(f"       add a '## {_a(ver)} - YYYY-MM-DD' section at the top "
              f"(see the internal RELEASING runbook).")
        return 1
    if top != ver:
        print(f"[FAIL] CHANGELOG top section {_a(top)!r} != __version__ {_a(ver)!r}")
        print(f"       add a '## {_a(ver)} - YYYY-MM-DD' section at the top "
              f"(see the internal RELEASING runbook).")
        return 1
    print(f"[ok] CHANGELOG top section {_a(top)} matches __version__")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract the newest CHANGELOG.md section (GitHub Release body)")
    parser.add_argument("--check", action="store_true",
                        help="verify the top section matches asr_voice/version.py")
    parser.add_argument("--out", default=None,
                        help="write the section here (UTF-8, LF); default: stdout")
    parser.add_argument("--changelog", default=str(DEFAULT_CHANGELOG),
                        help="changelog path (default: <repo>/CHANGELOG.md)")
    args = parser.parse_args(argv)

    path = Path(args.changelog)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # 只打印文件名与异常类别：路径可能含非 ASCII，异常消息里也带着它。
        print(f"[FAIL] cannot read {_a(path.name)}: {exc.__class__.__name__}")
        return 1

    if args.check:
        return check(text)

    body = section_body(text)
    if body is None:
        print("[FAIL] CHANGELOG.md has no '## <version>' section")
        return 1

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n"：Windows 默认会把 \n 翻成 \r\n，Release 正文会带 CRLF
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body + "\n")
        print(f"[ok] release notes written: {_a(out.name)} ({len(body)} chars)")
        return 0

    sys.stdout.buffer.write((body + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
