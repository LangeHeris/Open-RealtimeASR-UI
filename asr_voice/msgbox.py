"""统一错误提示（启动期致命错误共用）。

打包版（PyInstaller frozen，无控制台）与 商店版（launcher 静默拉起，stderr
无人可见）弹应用自有 Fluent 风格的 Qt 错误对话框（ui.error_dialog，与欢迎页/
设置界面同视觉血统）；Qt 初始化失败时回退 Win32 原生 MessageBoxW，保证提示必达；
其余源码版打印到 stderr（控制台可见，且测试/CI 不弹模态框）。

全项目启动期致命错误（配置加载失败、核心模块导入失败、热键注册失败、
运行时异常）统一走 show_error，保证打包版与源码版的提示行为一致、
风格统一。
"""

from __future__ import annotations

import sys

from .i18n import t


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _is_steam() -> bool:
    try:
        from .steam_integration import is_steam_build
        return is_steam_build()
    except Exception:
        return False


def show_error(message: str, title: str = "Open-RealtimeASR-UI") -> None:
    """显示致命错误。

    - 打包版 / 商店版（无控制台）：Qt Fluent 风格错误对话框；
      Qt 不可用时回退 Windows MessageBoxW（图标 0x10 = MB_ICONERROR）
    - 其余源码版：打印到 stderr（保持控制台可见）
    """
    if not (_is_frozen() or _is_steam()):
        print(t("err.stderr_line", message=message), file=sys.stderr)
        return
    try:
        _qt_show_error(message, title)
    except Exception:
        _win32_show_error(message, title)


def _qt_show_error(message: str, title: str) -> None:
    """Qt 对话框路径。

    启动期致命错误常发生在 QApplication 建立之前（配置加载失败等），
    这里临时创建、用完即弃。任何失败都向外抛，由 show_error 回退 Win32。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from .ui.error_dialog import ErrorDialog

    app = QApplication.instance()
    if app is None:
        # 与 main.py 一致：按显示器实际缩放渲染，避免 125%/150% 缩放下发虚
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass
        app = QApplication([])
    # 字体尽力而为：缺失（如 CI 产物无内置字体）不阻断错误展示
    try:
        from .ui.fonts import apply_default_font
        apply_default_font(app)
    except Exception:
        pass
    # 主题跟随设置界面主题；配置不可用时（本错误可能就是配置加载失败）
    # ErrorDialog 内部回退默认主题
    cfg = None
    try:
        from .config.loader import get_config_unchecked
        cfg = get_config_unchecked(None)
    except Exception:
        cfg = None
    ErrorDialog(message, title=title, cfg=cfg).exec()


def _win32_show_error(message: str, title: str) -> None:
    """Win32 兜底路径：仅 Qt 路径失败时使用（如 Qt DLL 缺失/损坏）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        pass
