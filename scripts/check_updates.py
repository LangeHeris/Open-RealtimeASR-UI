"""检查 funasr 包与本地识别模型是否有新版（只查元数据，不下载、不导入 torch/funasr）。

本地引擎加载时已关闭 funasr 的联网自检（AutoModel(disable_update=True)）并优先读
modelscope 缓存，因此「有没有新版」改由本脚本按需回答。发版前或想升级本地引擎时跑一次：

    python scripts/check_updates.py
    python scripts/check_updates.py --deep          # 额外比对 model.pt 的 sha256（慢）
    python scripts/check_updates.py --model paraformer-zh-streaming

退出码：0 = 全部最新；1 = 有更新可看；2 = 检查失败（网络/解析异常）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = Path.home() / ".cache" / "modelscope"
MODELSCOPE_API = "https://modelscope.cn/api/v1/models"
PYPI_API = "https://pypi.org/pypi/funasr/json"
UA = {"User-Agent": "ORI-check-updates/1.0"}

# pyproject.toml 缺省时的兜底映射（funasr 自带同名表，优先用它的）
_FALLBACK_ALIASES = {
    "paraformer-zh-streaming": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _funasr_aliases() -> dict[str, str]:
    """读取 funasr 自带的模型别名表（按文件执行，不 import funasr 以免拖入 torch）。"""
    try:
        spec = importlib.util.find_spec("funasr")
        if spec is None or not spec.submodule_search_locations:
            return dict(_FALLBACK_ALIASES)
        path = Path(list(spec.submodule_search_locations)[0]) / "download" / "name_maps_from_hub.py"
        if not path.is_file():
            return dict(_FALLBACK_ALIASES)
        namespace: dict = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
        aliases = {k: v for k, v in namespace.get("name_maps_ms", {}).items()
                   if isinstance(v, str) and "/" in v}
        return aliases or dict(_FALLBACK_ALIASES)
    except Exception:  # noqa: BLE001 映射表读不到时用兜底，不影响包版本检查
        return dict(_FALLBACK_ALIASES)


def _pinned_funasr() -> list[tuple[str, str]]:
    """收集仓库里 funasr 的版本声明：(来源, 声明串)。"""
    out: list[tuple[str, str]] = []
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        for m in re.finditer(r'^\s*"(funasr[^"]*)",?\s*$', text, re.M):
            out.append(("pyproject.toml", m.group(1)))
    for name in ("requirements.steam.cpu.txt", "requirements.steam.cuda.txt"):
        req = ROOT / name
        if not req.is_file():
            continue
        for line in req.read_text(encoding="utf-8").splitlines():
            if line.startswith("funasr=="):
                out.append((name, line.strip()))
    return out


def _installed_funasr_dirs() -> list[tuple[str, str]]:
    """各环境里实际安装的 funasr 版本：(环境, 版本)，只读 dist-info 目录名。"""
    exe = Path(sys.executable).resolve()
    # venv: <venv>\Scripts\python.exe；官方安装包: <root>\python.exe
    base = exe.parent.parent if exe.parent.name.lower() == "scripts" else exe.parent
    envs = [
        (str(base) if sys.platform == "win32" else "", "当前环境"),
        (str(ROOT / "steam_build" / "env"), "Steam CPU"),
        (str(ROOT / "steam_build" / "env_gpu"), "Steam GPU"),
        (str(ROOT / "steam_build" / "depot_gpu" / "env_gpu"), "depot GPU"),
    ]
    out: list[tuple[str, str]] = []
    for base, label in envs:
        if not base:
            continue
        site = Path(base) / "Lib" / "site-packages"
        if not site.is_dir():
            site = Path(base) / "lib"
        found = sorted(site.glob("funasr-*.dist-info")) if site.is_dir() else []
        if found:
            version = found[-1].name[len("funasr-"):-len(".dist-info")]
            out.append((label, version))
    return out


def _parse_version(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", value.split("+")[0])
    return tuple(int(n) for n in nums)


def check_package() -> tuple[bool, list[str]]:
    lines: list[str] = ["【funasr 包】"]
    pinned = _pinned_funasr()
    latest = _get_json(PYPI_API)["info"]["version"]
    lines.append(f"  PyPI 最新: {latest}")
    for source, spec in pinned:
        lines.append(f"  仓库声明: {source} -> {spec}")
    exact = [re.sub(r"^funasr==", "", spec) for source, spec in pinned if spec.startswith("funasr==")]
    outdated = False
    if exact:
        current = exact[0]
        outdated = _parse_version(latest) > _parse_version(current)
        lines.append(f"  结论: {'有新版，可升级 ' + current + ' -> ' + latest if outdated else '已是最新'}")
    installed = _installed_funasr_dirs()
    if installed:
        lines.append("  已安装: " + ", ".join(f"{label}={ver}" for label, ver in installed))
        stale = [f"{label}={ver}" for label, ver in installed if _parse_version(ver) < _parse_version(latest)]
        if stale:
            outdated = True
            lines.append("  需同步: " + ", ".join(stale))
    return outdated, lines


def check_model(model: str, deep: bool) -> tuple[bool, list[str]]:
    lines: list[str] = [f"【模型 {model}】"]
    hub_id = _funasr_aliases().get(model, model)
    lines.append(f"  仓库 ID: {hub_id}")
    info = _get_json(f"{MODELSCOPE_API}/{hub_id}")["Data"]
    updated = info.get("LastUpdatedTime")
    if updated:
        stamp = _dt.datetime.fromtimestamp(int(updated)).strftime("%Y-%m-%d")
        lines.append(f"  仓库最后更新: {stamp}")
    files = _get_json(f"{MODELSCOPE_API}/{hub_id}/repo/files?Revision=master")["Data"]["Files"]
    remote = {f["Name"]: f for f in files if f.get("Type") == "blob"}
    remote_pt = remote.get("model.pt")
    if remote_pt is None:
        lines.append("  远端未找到 model.pt，跳过比对")
        return False, lines
    local_dir = CACHE_ROOT / "models" / hub_id.replace("/", "--") / "snapshots"
    local_pt = None
    if local_dir.is_dir():
        for cand in sorted(local_dir.iterdir(), key=lambda p: (p.name != "master", p.name)):
            candidate = cand / "model.pt"
            if candidate.is_file():
                local_pt = candidate
                break
    if local_pt is None:
        lines.append("  本地缓存: 无（下次加载会在线下载，或已随发行包分发）")
        return True, lines
    local_size = local_pt.stat().st_size
    remote_size = int(remote_pt.get("Size") or 0)
    same = local_size == remote_size
    lines.append(f"  远端 model.pt: {remote_size:,} 字节")
    lines.append(f"  本地 model.pt: {local_size:,} 字节")
    if same and deep:
        digest = hashlib.sha256()
        with open(local_pt, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        same = digest.hexdigest() == (remote_pt.get("Sha256") or "")
        lines.append(f"  sha256 比对: {'一致' if same else '不一致'}")
    lines.append(f"  结论: {'本地已是最新' if same else '有新版模型，可重新下载'}")
    return (not same), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 funasr 包与本地模型是否有新版")
    parser.add_argument("--model", default="paraformer-zh-streaming", help="模型别名（默认 paraformer-zh-streaming）")
    parser.add_argument("--deep", action="store_true", help="额外比对 model.pt 的 sha256（需读整份权重，较慢）")
    args = parser.parse_args(argv)

    try:
        pkg_outdated, pkg_lines = check_package()
        model_outdated, model_lines = check_model(args.model, args.deep)
    except Exception as exc:  # noqa: BLE001 网络/接口异常统一报错退出
        print(f"检查失败：{exc}", file=sys.stderr)
        return 2

    for line in pkg_lines + [""] + model_lines:
        print(line)

    if pkg_outdated or model_outdated:
        print("\n提示：升级 funasr 后记得同步 pyproject.toml 的「实测通过」注释、"
              "requirements.steam.cpu/cuda.txt 的 pin，并重跑 tests/。")
        return 1
    print("\n全部已是最新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
