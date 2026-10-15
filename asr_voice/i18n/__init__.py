"""轻量键值表 i18n：zh/en 双语。

UI 处统一 `t("key")`；语言由 ui.language 定，空 = 首次运行按系统语言探测；
无热切换，改语言后重启生效。**导入期求值禁令**：模块级常量、类属性、函数
默认参数都在 import 那一刻求值，此时 `_lang` 仍是默认值，`t()` 结果会被
**冻结**，之后切语言不再变；这类位置**只存 i18n 键**，文案一律在**调用点**取。

词条表是包内子模块（zh.py / en.py）；框架若写成同名的 i18n.py，包目录会在导入时
遮蔽它。`_windows_ui_lang()` / `_locale_name()` 抽出便于在非 Windows 上打桩测试。
"""
from __future__ import annotations

import logging
import os
import threading

from .en import MESSAGES as _EN
from .zh import MESSAGES as _ZH

logger = logging.getLogger(__name__)

SUPPORTED = ("zh", "en")

# 不变量：词条表只整体替换、绝不在位修改，因此 t() 读侧无需加锁也不会撕裂。
_lock = threading.Lock()
_lang = "zh"
_table = _ZH


def _canonical(lang: str) -> str:
    """大小写/空白归一 + IETF 标签取主语言（"zh_CN" / "zh-Hans" → "zh"）。

    用户在 config 里手写 "zh_CN" 时不应该被当成非法值丢掉意图。
    """
    tag = str(lang or "").strip().lower().replace("_", "-")
    return tag.split("-", 1)[0] if tag else ""


def set_language(lang: str) -> bool:
    """切换当前语言；归一化后不在 SUPPORTED 内则拒绝并保持原值，返回是否成功。"""
    global _lang, _table
    lang = _canonical(lang)
    if lang not in SUPPORTED:
        return False
    with _lock:
        _lang = lang
        _table = _EN if lang == "en" else _ZH
    return True


def language() -> str:
    return _lang


def _windows_ui_lang() -> int | None:
    """Windows 显示语言 LANGID；非 Windows / 取不到返回 None。"""
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return None


def _locale_name() -> str:
    """非 Windows 回退用的 locale 名（"zh_CN.UTF-8" 之类）。

    `locale.getdefaultlocale` 自 3.11 起弃用、计划 3.15 移除，届时若只剩它，
    中文用户会静默掉成英文——故先取环境变量，拿不到再试 locale API。
    """
    name = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") \
        or os.environ.get("LANG") or ""
    if name:
        return name
    try:
        import locale
        return locale.getdefaultlocale()[0] or ""
    except Exception:
        return ""


def detect_system_language() -> str:
    """系统显示语言探测：中文主语言 → zh，其余 → en（v1 仅中英）。

    Windows LANGID 的低 10 位是主语言码：0x0804 简体与 0x0404 繁体同为 0x04，
    一并归 zh；非 Windows 走 locale 名，再不成退 en。
    """
    lang_id = _windows_ui_lang()
    if lang_id is not None:
        return "zh" if (lang_id & 0x3FF) == 0x04 else "en"
    return "zh" if _locale_name().lower().startswith("zh") else "en"


def apply_configured_language(configured: str) -> str:
    """启动早期定语言：配置受支持值优先，否则系统探测。返回生效语言（已归一化）。"""
    if set_language(configured):
        return _lang
    set_language(detect_system_language())
    return _lang


def t(key: str, default: str | None = None, **params) -> str:
    """取词条；当前语言缺 key 回退 zh，再缺返回 default（无 default 则回 key）。

    params 走 str.format：词条里的字面花括号要写成 {{ }}。格式化失败返回带占位符
    的原文而非 key——用户宁可看到花括号，也不要看到键名丢失全部语义。
    """
    text = _table.get(key)
    if text is None:
        text = _ZH.get(key)
    if text is None:
        logger.debug("i18n 缺词条：%s", key)
        return key if default is None else default
    if params:
        try:
            return text.format(**params)
        except Exception:
            logger.debug("i18n 格式化失败：%s %r", key, params, exc_info=True)
            return text
    return text


def t_or_none(key: str) -> str | None:
    """取词条；**没有**该词条时返回 None（用来判定"这个词条存在吗"）。

    与 `t()` 不同：`t()` 有三层兜底（zh → default → 键名），返回值不便判存在性。
    """
    text = _table.get(key)
    if text is None:
        text = _ZH.get(key)
    return text
