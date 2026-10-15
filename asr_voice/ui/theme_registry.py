"""主题注册表：内置主题 + 外部主题包合并后的唯一查询入口。

主题包目录 `themes/`（打包版：exe 同目录；源码版：项目根），一个主题一个子目录，
目录内 `theme.yaml` 描述配色与特性。主题声明的 `feature` 对应特性模块缺失时
（如精简构建物理排除）跳过该主题并记日志——内容不存在即隔离。

新主题包投放即生效（重启后），无需改代码。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from ..i18n import t_or_none
from .presets import THEMES as _BUILTIN_THEMES
from .theme_features import get_feature

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = ("bg", "text", "accent")

_cache: dict[str, dict] | None = None


def _themes_roots() -> list[Path]:
    """主题包根目录（可能有多个），依次合并，重名主题以后出现者被跳过。

    打包版 _MEIPASS/themes + exe 同目录 themes/；渠道版 <安装根>/themes/（**必须走
    paths.install_dir()**，否则 __file__ 上溯两级在 depot 里是 app/ 会静默丢包）；源码版项目根 themes/。
    """
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "themes")
        roots.append(Path(sys.executable).parent / "themes")
    else:
        from ..paths import install_dir

        depot_root = install_dir()
        if depot_root is not None:
            roots.append(depot_root / "themes")
        roots.append(Path(__file__).resolve().parents[2] / "themes")
    roots.extend(_workshop_theme_roots())
    return roots


def _workshop_theme_roots() -> list[Path]:
    """已安装工坊物品里的主题包**根目录**（无工坊 / 不可用时为空）。

    是「根」不是「单个主题包」：本函数的结果被直接并进 `_themes_roots()`
    的扫描列表，而那边扫的是"根下的每个子目录 = 一个主题包"。
    """
    try:
        from ..workshop import theme_roots
        return theme_roots()
    except Exception:
        logger.debug("查询工坊主题失败", exc_info=True)
        return []


def _load_pack(pack_dir: Path) -> dict | None:
    """解析单个主题包；格式错误/缺字段/特性缺失时返回 None 并记日志。"""
    meta_file = pack_dir / "theme.yaml"
    if not meta_file.is_file():
        logger.warning("主题包缺少 theme.yaml，跳过：%s", pack_dir)
        return None
    try:
        data = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("主题包 theme.yaml 解析失败，跳过：%s", pack_dir, exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("主题包 theme.yaml 格式无效（应为键值表），跳过：%s", pack_dir)
        return None
    for key in _REQUIRED_KEYS:
        if not data.get(key):
            logger.warning("主题包缺少必填字段 %s，跳过：%s", key, pack_dir.name)
            return None
    feature = str(data.get("feature") or "")
    if feature and get_feature(feature) is None:
        logger.info("主题包 %s 所需特性模块 %s 不可用，跳过", pack_dir.name, feature)
        return None
    theme = dict(data)
    # 内部键：包自带 key 优先（语言无关、参与持久化），没有则退回目录名
    # 兼容早期主题包；name 只作显示名
    theme["key"] = str(data.get("key") or pack_dir.name).strip()
    theme.setdefault("name", pack_dir.name)
    theme["_dir"] = pack_dir
    # 特性模块若需要包内素材（如凯尔特的结图），注入包目录
    if feature:
        feat = get_feature(feature)
        if feat is not None and hasattr(feat, "set_pack_dir"):
            feat.set_pack_dir(pack_dir)
    return theme


def available_themes(refresh: bool = False) -> dict[str, dict]:
    """全部可用主题：内置在前，主题包按目录名排序。结果缓存。

    注册表按**内部键**索引（config.ui.theme_name 存的也是内部键）；
    显示名在 `theme["name"]`，界面一律经 `theme_display_name()` 取（同文件，末尾）。
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache
    merged: dict[str, dict] = {
        key: dict(t, key=key) for key, t in _BUILTIN_THEMES.items()
    }
    for root in _themes_roots():
        if not root.is_dir():
            continue
        for pack_dir in sorted(root.iterdir()):
            if not pack_dir.is_dir():
                continue
            theme = _load_pack(pack_dir)
            if theme is None:
                continue
            if theme["key"] in merged:
                logger.warning("主题包与已有主题重名，跳过：%s", theme["key"])
                continue
            merged[theme["key"]] = theme
    _cache = merged
    return merged


def get_theme(name: str) -> dict | None:
    """按名称取主题定义；不存在返回 None。"""
    return available_themes().get(name)


def theme_display_name(key: str) -> str:
    """内部键 → 显示名：内置主题走 i18n，外部主题包用包内 name。

    顺序：i18n 词条 `theme.<key>` 命中即返回；外部包回退包自带 name；都没有才返回内部键。
    存在性判定用 `t_or_none()` 而非 `t()`——后者三层兜底分不清"词条存在"与"回退成键名"。
    """
    translated = t_or_none(f"theme.{key}")
    if translated is not None:
        return translated
    theme = get_theme(key)
    return (theme or {}).get("name") or key
