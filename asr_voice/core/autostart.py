r"""开机自启动：HKCU Run 注册表键读写。

实现方式：HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
写入当前程序的自启命令（用户级，无需管理员权限）。

- 打包模式（exe）：直接写 exe 绝对路径
- 源码模式：优先用 pythonw.exe（无控制台窗口）运行项目根的 run.py；
  配置与日志查找已锚定源码位置（loader.find_config_path /
  main.setup_logging），工作目录漂移（开机自启时为 system32）无影响

自启命令统一追加 --hidden 参数：开机静默启动（仅托盘图标，悬浮条
不弹出，热键按下时悬浮条自然呼出）。用户手动启动不带参数，悬浮条
正常显示--与微信/QQ 的静默自启同款方案。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 注册表键名即任务管理器/启动项里显示的名称
APP_NAME = "Open-RealtimeASR-UI"
# 历史遗留键名：0.x 用 "OpenRealtimeASR-UI"，测试版用缩写 "ORI"，
# 改名后一并清理，避免任务管理器启动项里出现孤儿条目
_LEGACY_NAMES = ("ORI", "OpenRealtimeASR-UI")


def _launch_command() -> str | None:
    """构造自启命令行；无法确定时返回 None。"""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --hidden'
    try:
        from ..paths import install_dir
        root = install_dir()
        if root is not None:
            launcher = root / "launcher.exe"
            if launcher.is_file():
                # 渠道版：注册 launcher.exe（它注入发行标记并选依赖环境），
                # 开机自启不依赖平台客户端（SDK 初始化失败静默降级）
                return f'"{launcher}" --hidden'
    except Exception:
        pass
    exe = Path(sys.executable)
    # 源码模式优先 pythonw：开机自启不弹控制台黑框
    pyw = exe.with_name("pythonw.exe")
    python = pyw if pyw.exists() else exe
    project_root = Path(__file__).resolve().parent.parent.parent
    return f'"{python}" "{project_root / "run.py"}" --hidden'


def is_enabled() -> bool:
    """当前用户是否已开启开机自启动（注册表值存在即视为开启）。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False
    except Exception:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """开/关开机自启动，返回 (成功, 用户提示消息的 i18n 键)。

    i18n（A6）：本模块**不接触界面语言**，所以返回键而不是文案，由 UI 层
    （`app._on_autostart_toggled`）在调用点 `t()`。理由同 i18n 模块 docstring
    的「导入期求值禁令」——本模块的手段不建界面，语言在它跑起来时还没定。
    失败详情（异常文本）只进日志，**不编码进返回的键**；界面文案由
    `app.err.autostart_registry` 承担（值里不含 `{error}` 占位符）。
    """
    try:
        import winreg
    except ImportError:
        return False, "app.err.autostart_unsupported"

    try:
        # 清理历史遗留的旧键名，避免改名后在任务管理器启动项里出现孤儿条目
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            for legacy in _LEGACY_NAMES:
                try:
                    winreg.DeleteValue(key, legacy)
                except FileNotFoundError:
                    pass

        if enabled:
            cmd = _launch_command()
            if not cmd:
                return False, "app.err.autostart_no_command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            return True, "hint.autostart_on"
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass  # 本来就没开，视为成功
        return True, "hint.autostart_off"
    except OSError as exc:
        # 异常详情进日志（排障用），界面只说"注册表操作失败"——不把异常文本
        # 塞进展示串里当参数（那是把两种东西编码进一个字符串，调用方还得解析）
        logger.warning("开机自启注册表操作失败：%s", exc, exc_info=True)
        return False, "app.err.autostart_registry"
