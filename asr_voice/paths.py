"""用户状态目录：按运行模式分离。

打包版（PyInstaller frozen，GitHub 发行）用 <exe 所在目录>/.asr_voice/；
渠道版（launcher 静默拉起便携运行时，非 frozen）用 %APPDATA%\\<应用目录名>\\；
源码版用 ~/.asr_voice/。三者互不共享历史、窗口位置、自定义短语与自定义 prompt。

**为什么渠道版不写安装根**：安装根归平台客户端管——校验完整性、推送更新都可能
清掉里面的用户文件；同一台机的多个系统账号共用一份安装也会串状态。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 渠道版的用户状态目录名（位于 %APPDATA% 下）。
CHANNEL_STATE_DIRNAME = "Open-RealtimeASR-UI"


def install_dir() -> Path | None:
    r"""渠道版便携布局的安装根（runtime/env 的上一级）；非渠道版返回 None。

    launcher 现在直接跑 runtime\python.exe，其 parents[1] 即安装根；早期把 venv 的
    Scripts\python.exe 当入口时 parents[2] 才是——按目录标记（含 runtime/ 或 env/
    的候选）识别，两种布局都覆盖，故不依赖具体入口写法。
    """
    try:
        from .steam_integration import is_steam_build
        if not is_steam_build():
            return None
        exe = Path(sys.executable).resolve()
        for cand in (exe.parents[1], exe.parents[2]):
            if (cand / "runtime").is_dir() or (cand / "env").is_dir():
                return cand
    except Exception:
        pass
    return None


def _roaming_state_dir() -> Path | None:
    """%APPDATA%\\<应用目录名>；拿不到 APPDATA 时返回 None。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / CHANNEL_STATE_DIRNAME


def state_dir() -> Path:
    """返回用户状态目录（存放 history/state/phrases/prompts 等 JSON）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / ".asr_voice"
    if install_dir() is not None:
        # 渠道版：写到 %APPDATA%（安装根会被客户端校验/更新清掉）。
        # APPDATA 缺失（极端环境）才回退安装根，不静默丢状态。
        roaming = _roaming_state_dir()
        return roaming if roaming is not None else install_dir() / ".asr_voice"
    return Path.home() / ".asr_voice"
