r"""创意工坊内容层：物品目录 → 模型 / 主题 / 字体 / 短语 / 提示词 / 功能包能力。

物品 = 一个普通目录（根下 `ori_workshop.json`，新类型扩 `_ALLOWED_KINDS` 与
对应提取函数）。目录两条来源按 resolved path 去重：SDK provider（发行层注册）
+ 文件系统发现（直接读 Steam 库与应用旁 `workshop/`）。必须并存：
`SteamAPI_InitFlat` 只对 Steam 客户端启动的进程成功，提权重启下 SDK 整条失效。
本层不碰 steamworks，解析纯逻辑可单测。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .steam_integration import strip_secret_fields

logger = logging.getLogger(__name__)

# 物品根下的清单文件名
MANIFEST_NAME = "ori_workshop.json"
# 引擎配置片段（可选）
ENGINE_CONFIG_NAME = "engine.yaml"

# 本地投放目录名（应用旁，与 themes/、fonts/ 同款约定）
WORKSHOP_DIRNAME = "workshop"

# ---- 功能包能力声明----
# 能力名白名单：陌生名一律忽略，防物品提前点亮未来才有的开关；JSON 随包公开，命名保持中性。
CAPABILITY_EXTERNAL_SERVICES = "external_services"
_KNOWN_CAPABILITIES = frozenset({CAPABILITY_EXTERNAL_SERVICES})

# 允许的内容类型。新增类型必须同时补 `extract_*` 与 UI 侧的消费入口。
ALLOWED_KINDS = ("models", "themes", "fonts", "phrases", "prompts")

# 单个物品下每一类最多解析多少条目（防畸形清单把 UI 撑爆）
_MAX_ENTRIES_PER_KIND = 256
# 包名：只取单段目录名，口径同 _bundled_model_dir（分隔符/绝对路径/./.. 一律拒绝）
_MAX_NAME_LEN = 64


def _safe_name(name: Any) -> str | None:
    """校验单个目录名；不合规返回 None。单段、禁分隔符与路径标记、限长，不接受路径拼接。"""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > _MAX_NAME_LEN:
        return None
    if name in (".", "..") or "/" in name or "\\" in name:
        return None
    if ":" in name or "\x00" in name:
        return None
    # Windows 保留设备名：CON/PRN 之类开头的目录无法创建，留着也没意义
    stem = name.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}:
        return None
    return name


def _safe_relpath(rel: Any) -> str | None:
    """校验物品内相对路径（允许子目录，如 `fonts/My/x.ttf`）；禁绝对路径、`..`、盘符与 NUL。"""
    if not isinstance(rel, str):
        return None
    rel = rel.strip().replace("\\", "/")
    if not rel or len(rel) > 512:
        return None
    if rel.startswith("/") or ":" in rel or "\x00" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


@dataclass(frozen=True)
class Manifest:
    """一个工坊物品的清单解析结果。"""

    item_dir: Path
    item_id: int
    name: str
    version: str = ""
    author: str = ""
    description: str = ""
    kinds: tuple[str, ...] = ()
    entries: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # capabilities 块声明的能力名（白名单过滤后；空 = 本物品不提供该能力）
    capabilities: tuple[str, ...] = ()

    def has(self, kind: str) -> bool:
        return bool(self.entries.get(kind))

    def list_of(self, kind: str) -> tuple[str, ...]:
        return self.entries.get(kind, ())


def parse_manifest(item_dir: Path, item_id: int = 0) -> Manifest | None:
    """解析物品目录下的清单；缺文件/格式错误/无内容一律 None（退订残留与空物品属正常）。"""
    try:
        item_dir = Path(item_dir)
        raw = item_dir / MANIFEST_NAME
        if not raw.is_file():
            return None
        data = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("工坊清单读取失败，跳过物品 %s", item_dir, exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("工坊清单格式无效（应为 JSON 对象），跳过：%s", item_dir)
        return None

    name = str(data.get("name") or item_dir.name).strip() or item_dir.name
    entries: dict[str, tuple[str, ...]] = {}
    raw_entries = data.get("entries")
    if isinstance(raw_entries, dict):
        for kind in ALLOWED_KINDS:
            values = raw_entries.get(kind)
            if not isinstance(values, (list, tuple)):
                continue
            names: list[str] = []
            for value in values[:_MAX_ENTRIES_PER_KIND]:
                # 允许写成字符串或 {"name": ...}
                candidate = value if isinstance(value, str) else (
                    value.get("name") if isinstance(value, dict) else None)
                safe = _safe_name(candidate)
                if safe is not None and safe not in names:
                    names.append(safe)
            if names:
                entries[kind] = tuple(names)
    elif any(k in data for k in ALLOWED_KINDS):
        # 兼容把内容直接平铺在顶层的写法
        for kind in ALLOWED_KINDS:
            values = data.get(kind)
            if isinstance(values, (list, tuple)):
                names = [n for n in (_safe_name(v) for v in values[:_MAX_ENTRIES_PER_KIND])
                         if n]
                if names:
                    entries[kind] = tuple(names)

    # ---- capabilities 块（可选）：只认 {"features": [...]} 且能力名在白名单；
    # 其余写法一律忽略——声明权限高，宁严勿宽
    capabilities: list[str] = []
    raw_caps = data.get("capabilities")
    if isinstance(raw_caps, dict):
        features = raw_caps.get("features")
        if isinstance(features, (list, tuple)):
            for feature in features[:_MAX_ENTRIES_PER_KIND]:
                if (isinstance(feature, str)
                        and feature.strip() in _KNOWN_CAPABILITIES
                        and feature.strip() not in capabilities):
                    capabilities.append(feature.strip())

    if not entries and not capabilities:
        logger.info("工坊物品无可用内容（清单为空或全部条目不合法），跳过：%s",
                    item_dir.name)
        return None
    return Manifest(
        item_dir=item_dir,
        item_id=item_id,
        name=name,
        version=str(data.get("version") or ""),
        author=str(data.get("author") or ""),
        description=str(data.get("description") or ""),
        kinds=tuple(k for k in ALLOWED_KINDS if k in entries),
        entries=entries,
        capabilities=tuple(capabilities),
    )


def iter_manifests(install_dirs) -> Iterator[Manifest]:
    """对一组物品目录逐个解析，跳过 None。"""
    for item in install_dirs or ():
        manifest = parse_manifest(Path(item))
        if manifest is not None:
            yield manifest


def installed_manifests() -> list[Manifest]:
    """当前可用的全部内容包（两来源合并去重）。SDK 不可用时也要能列出包列表。"""
    return list(iter_manifests(_installed_dirs()))


def external_services_pack_installed(install_dirs=None) -> bool:
    """是否装了声明「外部服务」能力的工坊功能包；传 `install_dirs` 则只看这些目录。

    只判存在性，不做鉴权。
    """
    dirs = _installed_dirs() if install_dirs is None else install_dirs
    for manifest in iter_manifests(dirs):
        if CAPABILITY_EXTERNAL_SERVICES in manifest.capabilities:
            return True
    return False


def model_dirs() -> list[Path]:
    """所有物品里的模型目录（`<物品>/models/<名>`），直接喂 funasr 的模型解析。"""
    out: list[Path] = []
    for manifest in iter_manifests(_installed_dirs()):
        for name in manifest.list_of("models"):
            candidate = manifest.item_dir / "models" / name
            if candidate.is_dir():
                out.append(candidate)
    return out


def theme_roots() -> list[Path]:
    """所有物品的主题包**根目录**（各物品的 `themes/` 子目录）。

    必须返根：下游扫的是"根下每个子目录 = 一个主题包"，返 `themes/<名>`
    会让它进到包里什么都不注册（主题静默消失）。
    """
    out: list[Path] = []
    seen: set[str] = set()
    for manifest in iter_manifests(_installed_dirs()):
        if not manifest.has("themes"):
            continue
        root = manifest.item_dir / "themes"
        if not root.is_dir():
            continue
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def font_files() -> list[Path]:
    """所有工坊物品里的字体文件（`<物品>/fonts/` 下的 ttf/otf/ttc）。"""
    out: list[Path] = []
    seen: set[str] = set()
    for manifest in iter_manifests(_installed_dirs()):
        declared = manifest.list_of("fonts")
        fdir = manifest.item_dir / "fonts"
        if declared:
            for name in declared:
                candidate = fdir / name
                if candidate.suffix.lower() in (".ttf", ".otf", ".ttc") and candidate.is_file():
                    key = str(candidate).lower()
                    if key not in seen:
                        seen.add(key)
                        out.append(candidate)
        elif fdir.is_dir():
            # 清单没列字体时按约定扫目录：投放方省事，但只认三种扩展名
            for candidate in sorted(fdir.iterdir()):
                if candidate.suffix.lower() in (".ttf", ".otf", ".ttc") and candidate.is_file():
                    key = str(candidate).lower()
                    if key not in seen:
                        seen.add(key)
                        out.append(candidate)
    return out


def phrase_packs() -> list[tuple[Path, list[dict]]]:
    """自定义短语包：`<物品>/phrases/<名>.json`，与用户 phrases.json 同构，可直接并进同一张表。"""
    out: list[tuple[Path, list[dict]]] = []
    for manifest in iter_manifests(_installed_dirs()):
        for name in manifest.list_of("phrases"):
            candidate = manifest.item_dir / "phrases" / f"{name}.json"
            if not candidate.is_file():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("工坊短语包解析失败，跳过：%s", candidate)
                continue
            items = data if isinstance(data, list) else (
                data.get("entries") if isinstance(data, dict) else None)
            if not isinstance(items, list):
                continue
            valid = []
            for entry in items[:_MAX_ENTRIES_PER_KIND]:
                if not isinstance(entry, dict):
                    continue
                phrase_name = str(entry.get("name") or "").strip()
                content = str(entry.get("content") or "")
                if phrase_name and content:
                    valid.append({"name": phrase_name, "content": content})
            if valid:
                out.append((candidate, valid))
    return out


def prompt_packs() -> list[tuple[str, Path]]:
    """提示词模板：`<物品>/prompts/<名>.md`，返回 (显示名, 文件路径)。"""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for manifest in iter_manifests(_installed_dirs()):
        for name in manifest.list_of("prompts"):
            candidate = manifest.item_dir / "prompts" / f"{name}.md"
            if not candidate.is_file():
                continue
            key = f"{manifest.name}/{name}".lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((name, candidate))
    return out


def apply_engine_config(local_cfg: dict, install_dirs=None) -> dict:
    """把物品里的 `engine.yaml` 合进本地配置，**密钥字段强制剥离**。

    返回新字典不改入参；只补缺不覆盖（同 `merge_cloud_config` 取向）。顶层标量一律丢弃，
    防物品用 `default_engine` 之类的键改掉运行期引擎。
    """
    import copy

    import yaml

    result = copy.deepcopy(local_cfg or {})
    for item_dir in install_dirs if install_dirs is not None else _installed_dirs():
        path = Path(item_dir) / ENGINE_CONFIG_NAME
        if not path.is_file():
            continue
        try:
            fragment = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("工坊引擎配置解析失败，跳过：%s", path, exc_info=True)
            continue
        if not isinstance(fragment, dict):
            logger.warning("工坊引擎配置格式无效（应为键值表），跳过：%s", path)
            continue
        # 先剥密钥，再谈合并
        stripped = strip_secret_fields(fragment)
        if stripped != fragment:
            logger.warning(
                "工坊引擎配置 %s 携带密钥字段，已剥离（不允许通过工坊内容"
                "下发第三方服务凭证）", path)
        for section, value in stripped.items():
            if not isinstance(value, dict):
                # 顶层标量一律不进：防止 default_engine 之类被物品改掉
                logger.info("工坊引擎配置的顶层标量键被忽略：%s.%s", path.name, section)
                continue
            node = result.get(section)
            if isinstance(node, dict):
                for key, item_value in value.items():
                    result[section].setdefault(key, item_value)
            else:
                result[section] = copy.deepcopy(value)
    return result


# ---- 物品目录的来源 ----
# ① SDK provider（发行层注册）：能报状态、能做写操作；② 文件系统 provider
# （自带）：提权 / SDK 失效时仍能发现已下载内容。二者合并须按 resolved path
# 去重（`_installed_dirs`）——否则主题注册刷"重名跳过"警告、短语列表重复。
_installed_dirs_provider = None

# 文件系统发现的短缓存（秒）：四条装载链会在一次界面构建里各查一遍，重扫太浪费。
_FS_CACHE_TTL_S = 2.0
_fs_cache: tuple[float, tuple[Path, ...]] | None = None


def set_installed_dirs_provider(provider) -> None:
    """注册"已安装物品目录"提供者（无参可调用，返回目录序列）；None 解除。仅影响 SDK 来源，文件系统来源始终生效。"""
    global _installed_dirs_provider
    _installed_dirs_provider = provider


def _sdk_item_dirs() -> tuple[Path, ...]:
    """SDK 来源的物品目录（provider 未注册/抛异常时为空）。"""
    if _installed_dirs_provider is None:
        return ()
    try:
        items = _installed_dirs_provider() or ()
    except Exception:
        logger.debug("取已安装工坊目录失败（SDK 来源）", exc_info=True)
        return ()
    out: list[Path] = []
    for item in items:
        try:
            path = Path(item)
        except TypeError:
            continue
        if path.is_dir():
            out.append(path)
    return tuple(out)


# ---- 文件系统来源（不依赖 SDK）----
# 布局：<Steam 库>/steamapps/workshop/content/<AppID>/<物品ID>/
# 库可能多个，须读 libraryfolders.vdf 枚举，只认安装根会漏一半。
# winreg 无 HKCU/HKLM 别名——短名 getattr 会静默拿到 None，探测恒空。
_STEAM_REG_PATHS = (
    # (hive 常量名, 子键, 值名)；HKCU 优先（用户级安装覆盖机器级）
    ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam", "InstallPath"),
)

# libraryfolders.vdf 里的库路径行： "path"		"E:\\Steam"
_LIB_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')


def _read_registry_str(hive_name: str, subkey: str, value: str) -> str:
    """读注册表字符串；任何失败返回空串（非 Windows / 无权限 / 不存在）。"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:          # pragma: no cover - Windows 上必有
        return ""
    hive = getattr(winreg, hive_name, None)
    if hive is None:             # pragma: no cover
        return ""
    try:
        with winreg.OpenKey(hive, subkey) as key:
            data, _kind = winreg.QueryValueEx(key, value)
    except OSError:
        return ""
    return str(data or "")


def steam_install_root() -> Path | None:
    """Steam 安装根；探测不到返回 None。只读注册表，不猜常见路径（猜错会扫到别的目录）。"""
    for hive_name, subkey, value in _STEAM_REG_PATHS:
        raw = _read_registry_str(hive_name, subkey, value)
        if not raw:
            continue
        try:
            root = Path(raw)
        except (ValueError, OSError):
            continue
        if root.is_dir():
            return root
    return None


def _parse_library_paths(vdf_text: str) -> list[str]:
    """取 vdf 里的全部库路径（纯函数，便于单测）：只取 `"path"` 行并反转义，不完整解析。"""
    out: list[str] = []
    for raw in _LIB_PATH_RE.findall(vdf_text or ""):
        path = raw.replace("\\\\", "\\").strip()
        if path and path not in out:
            out.append(path)
    return out


def steam_library_roots() -> list[Path]:
    """全部 Steam 库根目录（安装根自身也算一个库）。

    读 `<安装根>/config/libraryfolders.vdf` 枚举附加库。
    vdf 缺失/损坏时退化为"只有安装根"——不抛异常。
    """
    root = steam_install_root()
    if root is None:
        return []
    roots: list[Path] = [root]
    vdf = root / "config" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return roots
    for raw in _parse_library_paths(text):
        try:
            candidate = Path(raw)
        except (ValueError, OSError):
            continue
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _app_id() -> int:
    """本应用的 Steam AppID（回落 0 = 未注入，文件系统发现整体短路）。"""
    from .steam_integration import STEAM_APP_ID

    try:
        return int(STEAM_APP_ID)
    except (TypeError, ValueError):     # pragma: no cover
        return 0


def _steam_content_item_dirs() -> list[Path]:
    """本应用在 Steam 库里的工坊内容目录；AppID 为 0 时返回空（不能拿未知 ID 去扫）。"""
    app_id = _app_id()
    if not app_id:
        return []
    out: list[Path] = []
    for library in steam_library_roots():
        content = library / "steamapps" / "workshop" / "content" / str(app_id)
        if not content.is_dir():
            continue
        try:
            children = sorted(content.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                out.append(child)
    return out


def local_workshop_roots() -> list[Path]:
    """本地投放目录（应用旁 `workshop/`，每个子目录 = 一个物品，结构同工坊）。"""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        # 打包版：exe 同目录（完整构建还可能内嵌一份）
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / WORKSHOP_DIRNAME)
        roots.append(Path(sys.executable).parent / WORKSHOP_DIRNAME)
    else:
        # 渠道版走 install_dir()（depot 里包在 <安装根>/workshop/
        # 与 app/ 平级）；源码版退到项目根。
        try:
            from .paths import install_dir
            depot_root = install_dir()
        except Exception:
            depot_root = None
        if depot_root is not None:
            roots.append(depot_root / WORKSHOP_DIRNAME)
        roots.append(Path(__file__).resolve().parents[1] / WORKSHOP_DIRNAME)
    return [r for r in roots if r.is_dir()]


def _local_item_dirs() -> list[Path]:
    """本地投放目录下的物品目录（每个子目录一个物品）。"""
    out: list[Path] = []
    for root in local_workshop_roots():
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and child.name not in (".", ".."):
                out.append(child)
    return out


def _filesystem_item_dirs() -> tuple[Path, ...]:
    """文件系统来源的全部物品目录（带短缓存）。"""
    global _fs_cache
    now = time.monotonic()
    if _fs_cache is not None and now - _fs_cache[0] < _FS_CACHE_TTL_S:
        return _fs_cache[1]
    found: list[Path] = []
    try:
        found.extend(_steam_content_item_dirs())
    except Exception:
        logger.debug("扫描 Steam 工坊内容目录失败", exc_info=True)
    try:
        found.extend(_local_item_dirs())
    except Exception:
        logger.debug("扫描本地投放目录失败", exc_info=True)
    result = tuple(found)
    _fs_cache = (now, result)
    return result


def clear_filesystem_cache() -> None:
    """清空文件系统发现缓存（测试用，也可给"手动刷新"入口调）。"""
    global _fs_cache
    _fs_cache = None


def _installed_dirs() -> tuple[Path, ...]:
    """全部已安装物品目录：两来源合并，按 resolved path 去重（否则内容重复注册）。"""
    seen: set[str] = set()
    out: list[Path] = []
    for source in (_sdk_item_dirs(), _filesystem_item_dirs()):
        for path in source:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return tuple(out)