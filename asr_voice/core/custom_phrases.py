"""自定义短语词表：短语名 -> 文本内容，语音命令模式下触发词后说出
短语名即整段插入。

存储于状态目录的 phrases.json（打包版在 exe 同目录 .asr_voice/，
源码版在 ~/.asr_voice/，见 paths.state_dir；与历史/窗口状态文件同目录），
数组保序，每项 {"name": "...", "content": "..."}。不写 config.yaml，
避免触发热加载。本模块不感知启用开关（开关在 voice_commands 层判断），
仅做纯数据存取与匹配。

匹配口径与命令表一致：精确匹配、不做拼音容错。查找前对输入做与
voice_commands._clean 相同口径的清洗（去首尾空白与标点，英文
lower）。文件带 mtime 缓存：设置界面保存后无需通知即生效。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from ..paths import state_dir

logger = logging.getLogger(__name__)

_STATE_DIR = state_dir()
_PHRASES_FILE = _STATE_DIR / "phrases.json"

_PUNCT = "。，、！？；：.,!?;:~～… \t"

_lock = threading.Lock()
_cache: Optional[tuple[float, dict[str, str]]] = None  # (mtime, 名->内容)


def _clean(text: str) -> str:
    """去首尾空白与标点，英文 lower（与命令匹配口径一致）。"""
    return (text or "").strip().strip(_PUNCT).strip().lower()


def _load() -> dict[str, str]:
    """读取词表为 名->内容 字典（同名后者覆盖），带 mtime 缓存。"""
    global _cache
    try:
        mtime = _PHRASES_FILE.stat().st_mtime
    except OSError:
        _cache = None
        return {}
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    mapping: dict[str, str] = {}
    try:
        data = json.loads(_PHRASES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                content = str(item.get("content") or "")
                if name and content:
                    mapping[_clean(name)] = content
    except Exception:
        logger.debug("读取自定义短语失败", exc_info=True)
    _cache = (mtime, mapping)
    return mapping


def find(text: str) -> Optional[str]:
    """按短语名查找内容；命中返回内容，未命中/未启用返回 None。"""
    key = _clean(text)
    if not key:
        return None
    with _lock:
        return _load().get(key)


def entries() -> list[dict]:
    """返回原始条目副本（设置界面读取）。"""
    with _lock:
        try:
            data = json.loads(_PHRASES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict)]


def save(entries: list[dict]) -> bool:
    """写盘（过滤空行，同名保留后者）。失败记日志返回 False。"""
    global _cache
    clean: list[dict] = []
    seen: dict[str, int] = {}
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        content = str(item.get("content") or "")
        if not name or not content:
            continue
        rec = {"name": name, "content": content}
        idx = seen.get(_clean(name))
        if idx is not None:
            clean[idx] = rec
        else:
            seen[_clean(name)] = len(clean)
            clean.append(rec)
    try:
        with _lock:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _PHRASES_FILE.write_text(
                json.dumps(clean, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            _cache = None  # 失效缓存，下次查找重读
    except Exception:
        logger.debug("保存自定义短语失败", exc_info=True)
        return False
    return True
