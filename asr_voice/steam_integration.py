"""发行渠道集成：发行层判断 + 平台 SDK 能力封装（两层互不依赖）。

发行层：launcher 透传 --steam → is_steam_build()。GitHub/PyInstaller 版无此参数。
能力层：steamworks import 失败或 SDK 初始化失败 → 静默降级
（仅失去云存档/统计，其余功能照常）。所有公开方法先判可用性，
不可用一律安全空操作（返回 False/None/0，不抛异常）。

AppID：开发期靠工作目录 steam_appid.txt（steamworks 自动读取）；
正式环境由 平台客户端注入。本模块不硬编码 AppID。
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

def _load_dlc_app_id() -> int:
    """取免费显卡加速 DLC 的 AppID；没有构建期产物就回落 0。

    **为什么不写死**：本文件在**公开仓**里，而项目约定是「真 ID 不入库」
    （见 `tools/gen_vdf.py` 的模块 docstring）。真值由构建期注入 ——
    `tools/assemble_depot.py` 从环境变量 `STEAM_DLC_APP_ID` 生成
    `app\\asr_voice\\_steam_ids.py`，**只落在 depot 里**，源码树永远没有该文件。

    回落 0 是唯一安全的缺省值：0 会让下面所有 DLC 判断短路（界面上的
    「安装 DLC」入口不显示），而不是误报「未安装」。GitHub 版走的正是这条路。
    """
    try:
        from ._steam_ids import STEAM_DLC_GPU_APP_ID as value
    except ImportError:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):     # pragma: no cover - 生成物被手改成垃圾
        return 0


STEAM_DLC_GPU_APP_ID = _load_dlc_app_id()


def is_steam_build() -> bool:
    """发行层判断（全项目收敛点）：进程命令行含 --steam 即 商店版。"""
    return "--steam" in sys.argv[1:]


def _import_steamworks():
    """延迟导入 steamworks（测试打补丁点）。

    注意来源：**不是 PyPI 上的包**（PyPI 没有 `steamworks`，社区 SteamworksPy
    又缺 Cloud 且方法名对不上）。它是随发行 depot 一起投放的自研 ctypes 薄封装
    `steam/steamworks/`，只存在于 商店版。

    所以 GitHub 版（含公开仓克隆出来的开发环境）走到这里必然 ImportError ——
    那是**设计内的降级路径**，不是缺陷：SteamIntegration.init() 会静默降级，
    仅失去云存档/统计/DLC。
    """
    import steamworks
    return steamworks


class SteamIntegration:
    """进程级单例：init 一次，其余方法判可用性后空操作。"""

    def __init__(self) -> None:
        self._instance = None   # STEAMWORKS 实例（init 成功才有）
        self._cloud = None      # steamworks.Cloud
        self._stats = None      # steamworks.UserStats
        self._apps = None       # steamworks.Apps

    @property
    def available(self) -> bool:
        return self._instance is not None

    def init(self) -> bool:
        if self._instance is not None:
            return True
        try:
            sw = _import_steamworks()
            instance = sw.STEAMWORKS()
            if not instance.initialize():
                logger.info("SteamAPI_Init 失败（非 Steam 环境），静默降级")
                return False
            self._instance = instance
            self._cloud = instance.Cloud()
            self._stats = instance.UserStats()
            self._apps = instance.Apps()
            logger.info("Steamworks 已初始化")
            return True
        except Exception:
            logger.info("Steamworks 初始化失败，静默降级（无云存档/统计）", exc_info=True)
            self._instance = None
            return False

    def run_callbacks(self) -> None:
        if self._instance is not None:
            try:
                self._instance.run_callbacks()
            except Exception:
                pass

    def cloud_write(self, filename: str, data: bytes) -> bool:
        if self._cloud is None:
            return False
        try:
            return bool(self._cloud.FileWrite(filename, data))
        except Exception:
            logger.debug("云存档写入失败：%s", filename, exc_info=True)
            return False

    def cloud_read(self, filename: str) -> bytes | None:
        if self._cloud is None:
            return None
        try:
            data = self._cloud.FileRead(filename)
            return bytes(data) if data else None
        except Exception:
            logger.debug("云存档读取失败：%s", filename, exc_info=True)
            return None

    def cloud_file_count(self) -> int:
        if self._cloud is None:
            return 0
        try:
            return int(self._cloud.GetFileCount())
        except Exception:
            return 0

    def store_stats(self, recorded_seconds: int, recognized_chars: int, sessions: int) -> bool:
        if self._stats is None:
            return False
        try:
            self._stats.SetStatInt("recorded_seconds", int(recorded_seconds))
            self._stats.SetStatInt("recognized_chars", int(recognized_chars))
            self._stats.SetStatInt("sessions", int(sessions))
            return bool(self._stats.StoreStats())
        except Exception:
            logger.debug("统计写入失败", exc_info=True)
            return False

    def dlc_installed(self, dlc_app_id: int) -> bool:
        if self._apps is None or not dlc_app_id:
            return False
        try:
            return bool(self._apps.BIsDlcInstalled(int(dlc_app_id)))
        except Exception:
            return False


_integration: SteamIntegration | None = None


def get_integration() -> SteamIntegration:
    """进程级单例。"""
    global _integration
    if _integration is None:
        _integration = SteamIntegration()
    return _integration


def steam_available() -> bool:
    """能力层：steamworks 可导入且 SDK 初始化成功（懒初始化单例）。"""
    return get_integration().init()


# ---- 云存档纯逻辑（无 steamworks 依赖，可离线单测）----
# 这一段刻意不碰任何 平台 API：云存档最容易出事的地方是「密钥有没有被同步
# 上去」，那是一条纯数据规则，必须能在没有 平台客户端的机器上验证。
SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("aliyun", "api_key"),
    ("tencent", "secret_id"), ("tencent", "secret_key"), ("tencent", "app_id"),
    ("xfyun", "app_id"), ("xfyun", "api_key"), ("xfyun", "api_secret"),
    ("xfyun", "std_app_id"), ("xfyun", "std_api_key"),
    ("volcengine", "api_key"), ("volcengine", "access_token"),
    ("volcengine", "app_id"),
    ("dialog", "api_key"), ("dialog", "app_id"), ("dialog", "access_token"),
    ("llm", "api_key"),
)

# dialog 的嵌套子节（qwen/hermes）同样带密钥，扁平清单盖不到
_SECRET_NESTED: tuple[tuple[str, str, str], ...] = (
    ("dialog", "qwen", "api_key"),
    ("dialog", "hermes", "api_key"),
)


def _is_secret(section: str, key: str, nested: str | None) -> bool:
    if nested is None:
        return (section, key) in SECRET_FIELDS
    return (section, nested, key) in _SECRET_NESTED


def sanitize_config_dict(cfg_dict: dict) -> dict:
    """深拷贝并剔除全部密钥字段（云存档默认脱敏）。

    必须深拷贝：原地删等于把调用方手上那份配置直接抹掉。
    """
    import copy

    clean = copy.deepcopy(cfg_dict or {})
    for section, key in SECRET_FIELDS:
        node = clean.get(section)
        if isinstance(node, dict):
            node.pop(key, None)
    for section, sub, key in _SECRET_NESTED:
        node = clean.get(section)
        if not isinstance(node, dict):
            continue
        subnode = node.get(sub)
        if isinstance(subnode, dict):
            subnode.pop(key, None)
    return clean


def build_cloud_files(cfg_dict: dict, history_entries: list,
                      include_keys: bool) -> dict:
    """组装云存档文件：{文件名: 文本}。

    - `config.steamcloud.yaml`：**恒脱敏**，与 include_keys 无关。开关只决定
      要不要多传一份 `config.full.yaml`，不能让默认那份跟着变脏。
    - `history.json`：识别历史（纯文本，无密钥）。
    """
    import copy
    import json
    from datetime import datetime, timezone

    import yaml

    entries = list(history_entries or [])
    meta = {"saved_at": datetime.now(timezone.utc).isoformat(),
            "entries": len(entries)}
    clean = sanitize_config_dict(cfg_dict)
    clean["_meta"] = meta
    files = {
        "config.steamcloud.yaml": yaml.safe_dump(
            clean, allow_unicode=True, sort_keys=False),
        "history.json": json.dumps(entries, ensure_ascii=False),
    }
    if include_keys:
        full = copy.deepcopy(cfg_dict or {})
        full["_meta"] = meta
        files["config.full.yaml"] = yaml.safe_dump(
            full, allow_unicode=True, sort_keys=False)
    return files


def _strip_secrets_in_node(node, section: str, nested: str | None = None):
    """就地剔除一个节点里的全部密钥字段（递归到任意深度）。

    用于「整块从云收进来」的两条路径（`_merge_node` 只逐键判断，遇到
    value 是 dict 而本地同键不是 dict 时会整块落下，里面的嵌套密钥就漏了）。
    """
    if not isinstance(node, dict):
        return node
    for key in list(node.keys()):
        if _is_secret(section, key, nested):
            node.pop(key, None)
        elif isinstance(node[key], dict):
            _strip_secrets_in_node(node[key], section, nested=key)
    return node


def _merge_node(dst: dict, src: dict, section: str, cloud_newer: bool,
                nested: str | None) -> None:
    """就地合并一节：密钥字段永不从云覆盖，其余按新旧与缺失决定。"""
    import copy

    for key, value in src.items():
        if _is_secret(section, key, nested):
            continue  # 密钥字段本地优先
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge_node(dst[key], value, section, cloud_newer, nested=key)
            continue
        if key not in dst or cloud_newer:
            if isinstance(value, dict):
                # 整块落下前先脱敏：本地没有这一支，云上的嵌套密钥不能跟着进来
                dst[key] = _strip_secrets_in_node(
                    copy.deepcopy(value), section, nested=key)
                continue
            dst[key] = copy.deepcopy(value)


def merge_cloud_config(local: dict, cloud: dict) -> dict:
    """云 → 本地的恢复合并。

    三条规则，优先级从高到低：
    1. 密钥字段**永不**从云进入本地（云端那份本来就可能是别人的机器写的）；
    2. 本地缺的字段从云补（补之前同样脱敏）；
    3. 两边都有且云更新（`_meta.saved_at` 更大）时，云覆盖本地。

    `_meta` 始终保留本地的：它是本地配置的时间戳，被云覆盖会让下轮比较失真。
    返回副本，不改动入参。

    契约：`local` **要么为空字典、要么带 `_meta.saved_at`**。真实调用点
    （`main._maybe_restore_cloud_config`）只在本地 config.yaml 缺失时跑，
    恒传 `{}`；若将来有人传一份非空且无时间戳的本地配置进来，下面的兜底
    会让云上的非密钥设置整体赢——那是接口契约之外的用法。
    """
    import copy

    merged = copy.deepcopy(local or {})
    if not cloud:
        return merged
    _local_meta = (local or {}).get("_meta")
    _cloud_meta = cloud.get("_meta")
    local_saved = str(_local_meta.get("saved_at", "")) if isinstance(
        _local_meta, dict) else ""
    cloud_saved = str(_cloud_meta.get("saved_at", "")) if isinstance(
        _cloud_meta, dict) else ""
    # 本地没时间戳时按「云更新」处理：恢复动作本来就是为了把云上的设置拿回来，
    # 而真实调用点只在本地 config.yaml **缺失**时跑（那时 local 是空字典，
    # 全靠这条把云上的字段都收进来）。两边都有时间戳则老实比大小。
    cloud_newer = (not local_saved) or bool(cloud_saved > local_saved)
    for section, value in cloud.items():
        if section == "_meta":
            continue
        if section not in merged:
            # 整节缺失也不能原样收：云上的密钥一样不带进来
            merged[section] = _strip_secrets_in_node(
                copy.deepcopy(value), section)
            continue
        if isinstance(value, dict) and isinstance(merged[section], dict):
            _merge_node(merged[section], value, section, cloud_newer,
                        nested=None)
        else:
            logger.debug(
                "云存档合并跳过类型不匹配的节 %s（云 %s / 本地 %s）",
                section, type(value).__name__, type(merged[section]).__name__)
    return merged


class StatsTracker:
    """统计累计（只记数字，不记文本）：录音秒数 / 识别字数 / 会话数。"""

    def __init__(self) -> None:
        self.recorded_seconds = 0
        self.recognized_chars = 0
        self.sessions = 0

    def add_final_chars(self, n: int) -> None:
        self.recognized_chars += max(0, int(n))

    def add_session(self, seconds: float) -> None:
        self.sessions += 1
        self.recorded_seconds += max(0, int(seconds))

    def flush(self, integ) -> bool:
        """落盘；返回是否成功（能力层不可用时 integ.store_stats 自会返 False）。"""
        return integ.store_stats(
            self.recorded_seconds, self.recognized_chars, self.sessions)


def nvidia_gpu_present() -> bool:
    """N 卡探测：NVIDIA 驱动在则 nvcuda.dll 可加载（不依赖 CUDA 版 torch）。

    只用来决定要不要提示装显卡加速 DLC，误判的代价很低，所以宁可用最省事的
    探测方式，不要为此拉起 torch/nvidia-smi。
    """
    try:
        import ctypes

        ctypes.WinDLL("nvcuda.dll")
        return True
    except Exception:
        return False
