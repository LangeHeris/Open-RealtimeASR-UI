"""程序入口。

用法：
    python -m asr_voice.main
    python -m asr_voice.main --config path/to/config.yaml

退出码：0 正常退出；1 未捕获异常；2 命令行用法错误；
3 配置加载失败；4 核心模块导入失败；5 运行时异常。
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config.loader import describe_config, get_config_unchecked, is_configured
from .logging_setup import _is_frozen, setup_logging
from .msgbox import show_error
from .steam_integration import is_steam_build

# 退出码（见上方 docstring）
EXIT_OK = 0
EXIT_USAGE = 2  # argparse 用法错误自带 2，这里只是声明
EXIT_CONFIG = 3
EXIT_DEPENDENCY = 4
EXIT_RUNTIME = 5

# 发行层默认引擎：商店版内置离线引擎（funasr）开箱即用，GitHub 版不干预
STEAM_DEFAULT_ENGINE = "funasr"


def _default_engine() -> str | None:
    """发行层默认引擎：商店版 funasr（内置离线引擎开箱即用），GitHub 版 None。"""
    return STEAM_DEFAULT_ENGINE if is_steam_build() else None


def check_microphone() -> bool:
    """商店版启动检测默认输入设备；缺失弹明确提示并返回 False。

    只在 商店版调用：开源版用户多半自己知道有没有麦克风，且很多人用
    虚拟设备/远程桌面，探测失败却强退会很烦人。
    """
    try:
        import sounddevice as sd

        sd.query_devices(kind="input")
        return True
    except Exception as exc:
        # ImportError（缺 sounddevice / PortAudio DLL）与「真的没设备」对用户的
        # 处置一样（都录不了），所以仍弹同一句；但前者是打包事故而不是用户
        # 环境问题，日志里留 traceback 便于事后区分。
        # 注意：main() 里的 logger 是 setup_logging 之后才建的局部变量，
        # 本函数可能在它之前被调，故这里自己取同名 logger。
        # 日志串不 i18n：全项目日志都是中文，A10 只验收界面文案。
        logging.getLogger("asr_voice").error(
            "麦克风检测失败：%s", exc, exc_info=True)  # noqa: i18n
        from .i18n import t

        show_error(t("err.no_microphone"))
        return False


def _maybe_restore_cloud_config(target, explicit_config: str = "") -> None:
    """新机首启：把**本次刚生成的**默认配置换成 云上那份（脱敏）。

    `target` 直接取 `ensure_default_config()` 的返回值——它非 None 就说明
    config.yaml 是这一次刚创建出来的，本地本来没有任何用户配置。这样
    「绝不覆盖用户已有配置」是结构性保证，不必再判断文件新旧；也顺带
    复用了它那套目录选择逻辑（打包版 exe 同目录 / 不可写时退到用户主目录），
    不用在这里抄一遍。

    传了 `--config` 就跳过：那是显式指定的文件，不该被云存档悄悄覆盖。
    任何失败都只记日志——恢复不了最坏就是走全新配置，绝不能拦启动。
    """
    if target is None or explicit_config or not is_steam_build():
        return

    # 此刻 setup_logging 还没跑（它在配置就绪之后），日志可能只进默认 handler；
    # 这段本来也只在异常时才有输出，不值得为它把日志初始化提前。
    log = logging.getLogger("asr_voice")
    try:
        from pathlib import Path

        from .steam_integration import get_integration, merge_cloud_config

        integ = get_integration()
        if not integ.init():
            return  # 发行集成未生效/初始化失败，静默降级

        from .config.loader import _atomic_write

        target = Path(target)
        raw = integ.cloud_read("config.steamcloud.yaml")
        if not raw:
            return
        import yaml

        cloud = yaml.safe_load(raw.decode("utf-8")) or {}
        # local 传空字典：本地文件都还不存在，全靠云补；密钥不会跟着进来
        # （merge_cloud_config 的规则 1）。
        merged = merge_cloud_config({}, cloud)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, yaml.safe_dump(
            merged, allow_unicode=True, sort_keys=False))
        log.info("已从 Steam 云恢复配置：%s", target)
    except Exception:
        log.warning("云存档恢复失败，改用全新配置", exc_info=True)


def main() -> int:
    # 界面语言尽早定，且在拿到配置后再校正一次：
    # argparse 的 description/help、配置加载失败时的报错框都构建于 config 之前，
    # 若等到那时才定语言，它们会永久停在默认 zh（A6 把它们换成 t() 之后尤其致命）。
    from .i18n import apply_configured_language as _apply_ui_language, t

    _apply_ui_language("")

    # High DPI：按显示器实际缩放渲染，避免 125%/150% 缩放下界面发虚
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    # 静默启动标志：开机自启命令携带 --hidden（仅托盘常驻，悬浮条不弹，
    # 热键按下时悬浮条自然呼出）；手动启动不带参数，悬浮条正常显示。
    # 打包版不跑 argparse，直接查 argv（exe 可正常接收命令行参数）
    start_hidden = "--hidden" in sys.argv[1:]
    if _is_frozen():
        # 打包后无命令行参数解析
        config_arg = None
    else:
        parser = argparse.ArgumentParser(description=t("cli.description"))
        parser.add_argument("--config", help=t("cli.help.config"))
        parser.add_argument(
            "--steam", action="store_true",
            help=t("cli.help.steam"),
        )
        parser.add_argument(
            "--hidden", action="store_true",
            help=t("cli.help.hidden"),
        )
        parser.add_argument(
            "--replace", action="store_true",
            help=t("cli.help.replace"),
        )
        args = parser.parse_args()
        config_arg = args.config
        start_hidden = args.hidden

    # 首启生成 config.yaml，否则设置界面存密钥会静默失败
    try:
        from .config.loader import ensure_default_config
        created = ensure_default_config(default_engine=_default_engine())
        # 商店版新机首启：本次刚生成默认配置时，若云上有存档就换成云上那份
        # （created 为 None = 本地本来就有配置，此时一行都不动）
        _maybe_restore_cloud_config(created, config_arg)

        cfg = get_config_unchecked(config_arg, default_engine=_default_engine())
        # 配置就绪：ui.language 优先于进入 main 时的系统探测结果
        _apply_ui_language(str(getattr(getattr(cfg, "ui", None), "language", "") or ""))
    except Exception as exc:
        show_error(t("err.config_load", err=exc))
        return EXIT_CONFIG

    setup_logging(cfg)
    logger = logging.getLogger("asr_voice")
    if is_configured(cfg):
        logger.info("配置就绪：%s", describe_config(cfg))
    elif getattr(cfg, "engine", "") == "funasr":
        logger.warning(
            "本地引擎依赖（funasr）不可用：打包版不含本地引擎依赖"
            "（funasr + torch 体积过大），请改用云端引擎或以源码方式运行"
        )
    else:
        logger.warning(
            "当前引擎（%s）密钥未配置，程序将启动但无法录音，请右键悬浮条 -> 设置填写密钥",
            getattr(cfg, "engine", "?"),
        )

    # 麦克风缺失：**只警示，不拦启动**。
    # 早先按任务书写成 return EXIT_RUNTIME，但远程桌面、虚拟声卡、驱动瞬时
    # 不可用都会让探测失败——那些场景下 商店版会彻底打不开，且平台可能
    # 把非 0 退出码当崩溃上报。改成与上面「密钥未配置」一致的处理：说清楚，
    # 但让程序起来（用户能进设置、插上麦后直接用）。
    # 放在提权重启之前——缺麦克风时拉起管理员进程毫无意义。
    if is_steam_build() and not check_microphone():
        logger.warning("未检测到麦克风，程序仍将启动，但录音不可用")

    # 「永久为管理员启动」：开关开启且当前未提权时，拉起提权实例后退出
    # 当前普通权限实例（拒绝 UAC 则以普通权限继续，不阻塞启动）
    from .core.elevation import relaunch_if_configured
    if relaunch_if_configured(cfg):
        return EXIT_OK

    try:
        from .core.app import VoiceApp
    except ImportError as exc:
        logger.error("核心模块加载失败：%s", exc)
        show_error(t("err.core_import", err=exc))
        return EXIT_DEPENDENCY

    app = VoiceApp(cfg, start_hidden=start_hidden)
    try:
        return app.run()
    except KeyboardInterrupt:
        logger.info("用户中断，退出")
        return EXIT_OK
    except Exception as exc:
        logger.exception("运行时异常")
        show_error(t("err.runtime", err=exc))
        return EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
