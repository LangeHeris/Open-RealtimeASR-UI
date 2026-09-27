"""历史对话剪贴板：记录最终上屏文本，右键菜单点击复制。

AI 修正开启时记录的是修正后的文本（修正前原文不入历史）；
AI 修正关闭时记录 ASR 原始识别文本。

存储于状态目录的 history.json（打包版在 exe 同目录 .asr_voice/，
源码版在 ~/.asr_voice/，见 paths.state_dir；与窗口状态文件同目录），
不写 config.yaml，避免触发热加载。开关状态由 app 启动时从
config 读入（history.enable），右键菜单切换即时生效并持久化。

独立历史窗口通过 add_listener/remove_listener 注册变化回调，
条目新增/清空后实时刷新（保持 stdlib-only，不引 Qt）。
"""
from __future__ import annotations

import json
import logging
import threading

from ..paths import state_dir

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 500  # 独立历史窗口滚动浏览，容量放宽；右键菜单仍只展示最近 15 条
_STATE_DIR = state_dir()
_HISTORY_FILE = _STATE_DIR / "history.json"

_lock = threading.Lock()
_enabled = True
_entries: list[str] | None = None  # 延迟加载（首次访问时读文件）
_listeners: set = set()  # 变化通知回调（条目新增/清空时调用，无参）


def add_listener(fn) -> None:
    """注册变化回调（供独立历史窗口实时刷新）。"""
    _listeners.add(fn)


def remove_listener(fn) -> None:
    """注销变化回调。"""
    _listeners.discard(fn)


def _notify() -> None:
    """通知全部监听者；单回调异常不影响其余。"""
    for fn in tuple(_listeners):
        try:
            fn()
        except Exception:
            logger.debug("历史变化通知回调异常", exc_info=True)


def set_enabled(enabled: bool) -> None:
    """开关历史记录（不清空已有条目，仅停止新增）。"""
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    return _enabled


def _load() -> list[str]:
    """返回内部条目列表（最新在前），首次调用时从文件加载。"""
    global _entries
    if _entries is None:
        _entries = []
        try:
            if _HISTORY_FILE.exists():
                data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    _entries = [str(t) for t in data if str(t).strip()][:_MAX_ENTRIES]
        except Exception:
            logger.debug("读取历史记录失败", exc_info=True)
    return _entries


def _save() -> None:
    """持久化到 history.json。失败仅记日志，不影响使用。"""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(
            json.dumps(_entries or [], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("保存历史记录失败", exc_info=True)


def add(text: str) -> None:
    """最终文本上屏后记录一条；开关关闭或空文本忽略。

    AI 修正开启时由修正回调传入修正后文本（原文不记），
    修正关闭时由注入流程传入原始识别文本。

    与上一条完全相同（连续重复句）不重复记录，避免刷屏。
    """
    text = (text or "").strip()
    if not _enabled or not text:
        return
    with _lock:
        entries = _load()
        if entries and entries[0] == text:
            return
        entries.insert(0, text)
        del entries[_MAX_ENTRIES:]
        _save()
    _notify()


def entries() -> list[str]:
    """返回历史条目副本（最新在前）。"""
    with _lock:
        return list(_load())


def clear() -> None:
    """清空全部历史条目。"""
    global _entries
    with _lock:
        _entries = []
        _save()
    _notify()
