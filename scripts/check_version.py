"""CI 版本校验：tag 与 asr_voice/version.py 不一致即失败（Plan B spec §5）。

用法：
    python scripts/check_version.py v0.1.0     # 一致 → exit 0；否则 exit 1

输出刻意只用 ASCII：CI 的 stdout 在 Windows runner 上是 cp1252，
中文会以 UnicodeEncodeError 把脚本本身搞崩（那是「校验脚本自己失败」，
与版本不一致混在一起极难排查）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from asr_voice.version import __version__  # noqa: E402


def check(tag: str) -> int:
    """返回退出码：0 = tag 与代码内版本一致。"""
    raw = (tag or "").strip()
    if not raw:
        print("[FAIL] tag is empty; expected form vX.Y.Z")
        return 1
    if not raw.startswith("v"):
        print(f"[FAIL] tag {raw!r} must start with 'v' (form vX.Y.Z, "
              f"release.yml triggers on v*)")
        return 1
    if raw[1:] != __version__:
        print(f"[FAIL] tag {raw!r} != asr_voice/version.py __version__ {__version__!r}")
        print("       bump asr_voice/version.py, pyproject.toml and CHANGELOG.md together,")
        print("       then re-tag (see the internal RELEASING runbook).")
        return 1
    print(f"[ok] tag {raw!r} matches __version__ {__version__!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("[FAIL] usage: python scripts/check_version.py <tag>  (e.g. v0.1.0)")
        return 1
    return check(args[0])


if __name__ == "__main__":
    sys.exit(main())
