"""从 asr_voice/version.py 生成 PyInstaller 用的 version_info.txt。

为什么不用 PyInstaller 的 VSVersionInfo 对象序列化：那是二进制风格的
repr 落盘，diff 不可读、字段顺序由库决定。这里用文本模板 + 版本号替换，
生成结果人可读、可 diff，且「生成器输出 == 已提交文件」可被断言
（tests/test_version.py::GenVersionInfoTest.test_committed_version_info_matches_generator）。

用法：
    python scripts/gen_version_info.py                # 写仓库根 version_info.txt
    python scripts/gen_version_info.py --out <path>   # 写指定路径

注意：写文件固定 LF + 无尾换行（与仓库现存 version_info.txt 字节形态一致）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from asr_voice.version import __version__  # noqa: E402

DEFAULT_OUT = _ROOT / "version_info.txt"

# 占位符：{VERSION} / {FILEVERS}。刻意不用 str.format()——PyInstaller 资源
# 模板里早晚会出现别的花括号，format 会静默吞掉它们。
_TEMPLATE = """# UTF-8
#
# Windows file properties (Details tab) resource for the packaged exe.
# ProductName carries the full project name; the exe file itself keeps
# the short name ORI.exe (see APP_NAME in openrealtimeasr-ui.spec) and
# OriginalFilename / InternalName stay in sync with it.
# FileDescription 是系统界面（任务栏悬停、麦克风使用指示器等）显示的
# 应用名，须保持与 ProductName 一致的产品全名。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({FILEVERS}),
    prodvers=({FILEVERS}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u''),
        StringStruct(u'FileDescription', u'Open-RealtimeASR-UI'),
        StringStruct(u'FileVersion', u'{VERSION}'),
        StringStruct(u'InternalName', u'ORI'),
        StringStruct(u'LegalCopyright', u''),
        StringStruct(u'OriginalFilename', u'ORI.exe'),
        StringStruct(u'ProductName', u'Open-RealtimeASR-UI'),
        StringStruct(u'ProductVersion', u'{VERSION}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)"""


def normalize(version_str: object) -> str:
    """归一化为 `X.Y.Z`：去前导 `v`，并要求恰好三段纯数字。

    刻意不接受四段（`1.2.3.4`）与预发布后缀（`0.1.0-beta`）：法文资源里
    filevers 恒四段、末段由本模块补 0，四段输入必是调用方搞错了来源，
    静默丢一段会生成一个「版本号看着对、其实被截断」的坏资源。
    """
    raw = str(version_str if version_str is not None else "").strip().lstrip("vV")
    parts = raw.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(
            f"version must be three numeric segments (form X.Y.Z, "
            f"optional leading 'v'), got {version_str!r}")
    return raw


def filevers(version_str: str) -> str:
    """`0.1.0` → `0, 1, 0, 0`（FixedFileInfo 要求四段，末段恒 0）。"""
    return ", ".join([*normalize(version_str).split("."), "0"])


def render(version_str: str | None = None) -> str:
    """渲染 version_info.txt 全文（不含尾换行）。"""
    ver = normalize(version_str or __version__)
    return (_TEMPLATE
            .replace("{VERSION}", ver)
            .replace("{FILEVERS}", filevers(ver)))


def write(out: Path | str, version_str: str | None = None) -> Path:
    """把渲染结果写到 `out`（LF、无尾换行）。返回写入路径。"""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：Windows 默认会把 \n 翻成 \r\n，那会让「生成器输出 == 已提交文件」
    # 的守卫按平台漂移。
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(version_str))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate version_info.txt from asr_voice/version.py")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output path (default: <repo>/version_info.txt)")
    args = parser.parse_args(argv)
    path = write(args.out)
    # 只打印仓库相对路径：本脚本会在 CI（stdout=cp1252）里跑，绝对路径一旦含
    # 非 ASCII 就会以 UnicodeEncodeError 把脚本自己搞崩（与 check_version 同因）。
    try:
        shown = path.relative_to(_ROOT)
    except ValueError:
        shown = Path(path).name
    print(f"[ok] version_info written: {shown} (version {__version__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
