"""DLC 主题的可选绘制特性模块。

每个特性模块会在精简构建（lite）中被物理排除（见 .spec 的 excludes），
导入失败即视为该特性不可用；主题注册表会跳过声明了缺失特性的主题包。
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
