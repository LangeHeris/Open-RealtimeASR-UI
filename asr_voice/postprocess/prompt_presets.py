"""AI 修正提示词版本库：内置多版本 + 用户自定义版本管理。

内置版本随代码发布；自定义版本存状态目录的 prompts.json（打包版在
exe 同目录 .asr_voice/，源码版在 ~/.asr_voice/，见 paths.state_dir），
不写 config.yaml 避免行级写入难以表达嵌套字典。
运行时实际生效的仍是 llm.prompt（设置保存时写入），版本库只是
设置界面的选择/管理入口。
"""
from __future__ import annotations

import json
import logging

from ..paths import state_dir

logger = logging.getLogger(__name__)

# 内置提示词版本（仅一个，随代码发布）。内容为**空串**：llm.prompt 留空正是
# "按界面语言用内置模板"的表达（见 llm_corrector.prompt_for_language）。
# 这里若不填空而填某语言的模板原文，英文界面用户选中「内置方案」后保存，
# 会把中文长文写进 llm.prompt —— 界面显示的与实际生效的不一致。
# 其余版本全部由用户新增，存于状态目录的 prompts.json。
BUILTIN_PROMPTS: dict[str, str] = {
    "内置方案": "",
}

_FILE = state_dir() / "prompts.json"

_cache: dict[str, str] | None = None  # 自定义版本缓存（写操作时失效）


def custom_prompts() -> dict[str, str]:
    """用户自定义版本（名字 -> 内容）。失败返回空 dict。"""
    global _cache
    if _cache is not None:
        return dict(_cache)
    _cache = {}
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # 允许空内容版本（新增后等待输入），仅要求值为字符串
                _cache = {str(k): str(v) for k, v in data.items() if str(k).strip()}
    except Exception:
        logger.debug("读取自定义提示词失败", exc_info=True)
    return dict(_cache)


def _write(data: dict[str, str]) -> None:
    global _cache
    _cache = dict(data)
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:
        logger.debug("保存自定义提示词失败", exc_info=True)


def save_custom(name: str, content: str) -> None:
    """保存（或覆盖）一个自定义版本。

    允许空内容：新增流程是先建空版本并选中、再让用户在编辑框里慢慢
    填写——空版本是合法的中间态，强制非空会让"新增一个空版本"
    这条路径直接失效（save_custom(name, "") 被静默吞掉，下拉里
    永远看不到新建项）。
    """
    name = name.strip()
    if not name:
        return
    data = custom_prompts()
    data[name] = content.strip()
    _write(data)


def delete_custom(name: str) -> None:
    """删除一个自定义版本（不存在的名字静默忽略）。"""
    data = custom_prompts()
    if name in data:
        del data[name]
        _write(data)


def all_versions() -> dict[str, str]:
    """全部版本：内置在前，自定义在后（重名时内置优先）。"""
    versions = dict(BUILTIN_PROMPTS)
    for name, content in custom_prompts().items():
        if name not in versions:
            versions[name] = content
    return versions
