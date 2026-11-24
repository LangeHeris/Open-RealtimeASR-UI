"""源码版启动后的后台检查：funasr 包与本地模型是否落后于线上。

定位（重要）：**这是给开发者看的，不打扰终端用户**。Steam 版与打包版随包分发
固定版本的 funasr，用户既无法也不需要自行升级；只有从源码运行的开发者会
`pip install -U funasr`。因此：

- 只在源码版（非 --steam、非 frozen）且 funasr 已安装时执行，其余情形直接返回；
- 结果只写日志，不弹窗、不进设置页、不加 i18n；
- 用 state_dir()/update_check.json 节流：查成功后 7 天内不再联网，失败后 6 小时再试，
  断网环境不会每次启动都卡一次。

真正的「升级」动作仍然由人完成：scripts/check_updates.py 可在发版前手动复核。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CHECK_DELAY_S = 30.0          # 启动后延迟：等 UI 与模型预热稳定，避开启动高峰
TTL_SUCCESS_S = 7 * 86400     # 查成功后 7 天内不再联网
TTL_FAILURE_S = 6 * 3600      # 失败后 6 小时再试
HTTP_TIMEOUT_S = 10.0
PYPI_URL = "https://pypi.org/pypi/funasr/json"
MODELSCOPE_API = "https://modelscope.cn/api/v1/models"
DEFAULT_MODEL = "paraformer-zh-streaming"
STATE_FILE_NAME = "update_check.json"
USER_AGENT = "ORI-update-check/1.0"

# funasr 别名表的兜底副本（优先读 funasr 自己那份，见 _funasr_alias）
_FALLBACK_ALIASES = {
    "paraformer-zh-streaming": (
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    ),
}


# ---- 状态文件（节流用） ----

def _state_path() -> Path:
    from ..paths import state_dir

    return state_dir() / STATE_FILE_NAME


def _load_state() -> dict:
    try:
        raw = _state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("更新检查状态写入失败", exc_info=True)


def should_check(state: dict, now: float | None = None) -> bool:
    """是否该再次联网检查：成功后 7 天、失败后 6 小时内都不重复。"""
    now = time.time() if now is None else now
    last_success = float(state.get("last_success") or 0)
    if last_success and now - last_success < TTL_SUCCESS_S:
        return False
    last_attempt = float(state.get("last_attempt") or 0)
    if last_attempt and now - last_attempt < TTL_FAILURE_S:
        return False
    return True


# ---- 环境判断 ----

def is_dev_source_build() -> bool:
    """只在源码版生效：Steam 发行构建与 PyInstaller 打包版一律跳过。"""
    try:
        from ..steam_integration import is_steam_build

        if is_steam_build():
            return False
    except Exception:  # noqa: BLE001 判断失败按「非源码版」保守处理
        return False
    return not getattr(sys, "frozen", False)


def installed_funasr() -> str | None:
    """当前环境里 funasr 的版本；未安装返回 None（只读 dist-info，不 import funasr）。"""
    try:
        from importlib.metadata import version

        return version("funasr")
    except Exception:  # noqa: BLE001
        return None


# ---- 检查逻辑 ----

def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_version(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (value or "").split("+")[0])
    return tuple(int(n) for n in nums)


def _funasr_alias(model: str) -> str:
    """模型别名 → 仓库 ID；按文件执行 funasr 的映射表，避免 import funasr 拖入 torch。"""
    try:
        spec = importlib.util.find_spec("funasr")
        if spec is not None and spec.submodule_search_locations:
            path = (
                Path(list(spec.submodule_search_locations)[0])
                / "download"
                / "name_maps_from_hub.py"
            )
            if path.is_file():
                namespace: dict = {}
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
                hub_id = namespace.get("name_maps_ms", {}).get(model)
                if isinstance(hub_id, str) and "/" in hub_id:
                    return hub_id
    except Exception:  # noqa: BLE001 读不到映射表就用兜底
        pass
    return _FALLBACK_ALIASES.get(model, model)


def check_model(model: str = DEFAULT_MODEL) -> dict | None:
    """比对本地缓存与 ModelScope 上的 model.pt 尺寸；本地无缓存时返回 None。

    只比尺寸不下载、不算哈希：这里是「要不要升级」的提示，不是完整性校验。
    """
    hub_id = _funasr_alias(model)
    snapshots = Path.home() / ".cache" / "modelscope" / "models" / hub_id.replace("/", "--") / "snapshots"
    local_pt = None
    if snapshots.is_dir():
        for cand in sorted(snapshots.iterdir(), key=lambda p: (p.name != "master", p.name)):
            candidate = cand / "model.pt"
            if candidate.is_file():
                local_pt = candidate
                break
    if local_pt is None:
        return None
    files = _fetch_json(f"{MODELSCOPE_API}/{hub_id}/repo/files?Revision=master")["Data"]["Files"]
    remote = next((f for f in files if f.get("Name") == "model.pt"), None)
    if not remote:
        return None
    local_size = local_pt.stat().st_size
    remote_size = int(remote.get("Size") or 0)
    return {
        "name": model,
        "hub_id": hub_id,
        "local_size": local_size,
        "remote_size": remote_size,
        "newer": remote_size != local_size,
    }


def check_once(now: float | None = None) -> dict | None:
    """执行一次完整检查并落状态；不该检查（非源码版/未装 funasr/未到周期）返回 None。"""
    now = time.time() if now is None else now
    if not is_dev_source_build():
        return None
    installed = installed_funasr()
    if installed is None:
        return None

    state = _load_state()
    if not should_check(state, now):
        logger.debug("更新检查距上次不足周期，跳过")
        return None

    state["last_attempt"] = now
    funasr: dict = {"installed": installed}
    try:
        latest = _fetch_json(PYPI_URL)["info"]["version"]
        newer = _parse_version(latest) > _parse_version(installed)
        funasr.update({"latest": latest, "newer": newer})
        if newer:
            logger.info(
                "funasr 有新版本 %s（当前 %s），发版前可升级：pip install -U funasr==%s",
                latest, installed, latest,
            )
        else:
            logger.debug("funasr 已是最新：%s", installed)
    except Exception as exc:  # noqa: BLE001 断网/接口变更一律静默
        logger.debug("funasr 版本检查失败：%s", exc)
        state["funasr"] = {"installed": installed, "error": str(exc)}
        _save_state(state)
        return None

    try:
        model = check_model()
        result: dict = {"funasr": funasr, "model": model}
        if model is not None:
            if model["newer"]:
                logger.info(
                    "本地模型 %s 有新版本（远端 %s 字节 / 本地 %s 字节）；"
                    "删除 ~/.cache/modelscope/models 下对应目录后重启即可重新下载",
                    model["name"], model["remote_size"], model["local_size"],
                )
            else:
                logger.debug("本地模型已是最新：%s", model["name"])
    except Exception as exc:  # noqa: BLE001 模型检查失败不影响包版本结果
        logger.debug("模型版本检查失败：%s", exc)
        result = {"funasr": funasr, "model": None}

    state["last_success"] = now
    state["funasr"] = funasr
    if result.get("model"):
        state["model"] = result["model"]
    _save_state(state)
    return result


def start_in_background(delay: float = CHECK_DELAY_S) -> threading.Thread | None:
    """启动后台检查线程；非源码版/未装 funasr 时直接返回 None（调用方无需关心）。

    线程为 daemon：应用退出时不阻塞；延迟期间用户quit也不影响。
    """
    try:
        if not is_dev_source_build() or installed_funasr() is None:
            return None
    except Exception:  # noqa: BLE001 任何判断异常都不该影响启动
        return None

    def _worker() -> None:
        try:
            time.sleep(delay)
            check_once()
        except Exception:  # noqa: BLE001 后台线程绝不能抛到顶层
            logger.debug("后台更新检查异常", exc_info=True)

    thread = threading.Thread(target=_worker, daemon=True, name="update-check")
    thread.start()
    return thread
