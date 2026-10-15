"""GitHub 版主题投放清单：凯尔特风格 + 赛博朋克风（像素主题为渠道版独占）。

主题包目录名是中文，`build.bat` 必须保持纯 ASCII（非 ASCII 文本 + chcp 65001
会让 cmd 解析错行），所以 Unicode 目录名的投放动作交给 Python。

`STEAM_EXCLUSIVE_PACKS` 是「已投放过、必须清掉」的清单：构建产物目录是增量的，
不显式 rmtree，渠道版主题的 theme.yaml 会跟着 GitHub 版发出去。

输出只用 ASCII：CI 的 stdout 是 cp1252，中文 print 会 UnicodeEncodeError 崩掉脚本。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# GitHub 开源版投放的主题包（顺序即投放顺序）。
GITHUB_THEME_PACKS: tuple[str, ...] = ("凯尔特风格", "赛博朋克风")

# 渠道版独占的主题包：投放后必须从目标目录清除历史残留。
STEAM_EXCLUSIVE_PACKS: tuple[str, ...] = ("像素",)


def _a(text: object) -> str:
    """把任意动态文本压成 ASCII，避免 CI（stdout=cp1252）下 print 自己抛异常。"""
    return str(text).encode("ascii", "replace").decode("ascii")


def main(dst: str) -> int:
    dst_dir = Path(dst)
    src = _ROOT / "themes"
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for name in GITHUB_THEME_PACKS:
            s, d = src / name, dst_dir / name
            if not s.is_dir():
                print(f"[ERROR] theme pack missing in source tree: themes/{_a(name)}",
                      file=sys.stderr)
                return 1
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d)
        for name in STEAM_EXCLUSIVE_PACKS:
            stale = dst_dir / name
            if stale.exists():
                shutil.rmtree(stale)
                print(f"[note] removed store-exclusive theme pack: {_a(name)}")
        print(f"[ok] deployed {len(GITHUB_THEME_PACKS)} GitHub theme packs -> {_a(dst_dir)}")
        return 0
    except Exception as exc:
        print(f"[ERROR] theme deployment failed: {type(exc).__name__}: {_a(exc)}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/themes"))
