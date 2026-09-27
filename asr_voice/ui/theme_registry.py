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
    """主题包根目录（可能有多个）。

    打包版：exe 内嵌 _MEIPASS/themes（完整构建自带 DLC 主题包）+ exe 同目录
    themes/（用户可继续投放外部主题包）；渠道版（depot，非 frozen 裸跑）：
    <安装根>/themes/（assemble_depot 的投放位置）；源码版：项目根 themes/。
    依次合并，重名主题以后出现者被跳过。

    depot 必须走 paths.install_dir()：__file__ 上溯两级的「项目根」在 depot 里
    是 app/，而主题包在 <安装根>/themes/（与 app/ 平级）。修复前三个主题包在
    depot 全部静默缺席（渠道独占像素主题从菜单消失、凯尔特/赛博朋克只剩
    内置配色没有特性装饰），2026-09-25 首传之夜真机实测抓到。
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
    return roots


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

    必须住在这一层：`floating_bar` 依赖 `context_menu`（菜单构建），helper 放
    那边会让菜单拿不到，只能自己拼 `theme["name"]`——A6 把翻译接进本函数后，
    那条绕过路径不会跟着切语言。

    顺序：i18n 词条（`theme.<key>`）优先，命中即返回；**外部主题包**没词条，
    回退包自带 name（作者写的名字，不替它编翻译）；都没有才返回内部键——
    宁可让用户看到陌生的键，也不要吞掉"这个主题存在"的事实。

    存在性判定用 `t_or_none()` 而非 `t()`：后者三层兜底，返回值无法区分
    "词条存在"与"回退成了键名"。

    注意 `themes/*/theme.yaml` 的 `name:` 仍是作者写的原文（本仓库里是中文），
    所以"外部主题包在英文界面显示英文名"**没有**被解决 —— 本仓库当前之所以
    显示名正确，是因为内置主题的 `theme.*` 词条覆盖了全部 6 个键。给第三方
    主题包做显示名本地化是另一件事（商店版只投放内置主题，暂不需要）。
    """
    translated = t_or_none(f"theme.{key}")
    if translated is not None:
        return translated
    theme = get_theme(key)
    return (theme or {}).get("name") or key
