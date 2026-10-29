"""DLC 主题的可选绘制特性模块。

内置特性（celtic / cyberpunk / pixel）在精简构建（lite）中被物理排除
（见 .spec 的 excludes），导入失败即视为该特性不可用；主题注册表会跳过
声明了缺失特性的主题包。

主题包也可以自带 `feature.py`：注册表加载主题包时把它注册进来，
这样新增主题不必再改本文件的清单。
主程序通过 get_feature(name) 获取模块，所有调用点都要容忍 None。
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_FEATURES: dict[str, object] = {}

for _name in ("celtic", "cyberpunk", "pixel"):
    try:
        _FEATURES[_name] = importlib.import_module(f".{_name}", __name__)
    except Exception:
        logger.debug("主题特性模块 %s 不可用", _name, exc_info=True)


def get_feature(name: str):
    """按名称取特性模块；不可用返回 None。"""
    return _FEATURES.get(name)


def register_feature(name: str, module) -> None:
    """登记主题包自带的特性模块（同名内置模块优先，避免包覆盖内置实现）。"""
    if name and module is not None and name not in _FEATURES:
        _FEATURES[name] = module
