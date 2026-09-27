"""日志初始化与清理。

策略（适合托盘常驻型桌面程序）：
- 按天滚动：TimedRotatingFileHandler(when="midnight")，每天一个文件；
- 保留天数：启动时删除超过 retention_days 天的旧日志（按 mtime 判定），
  不依赖"写满才滚"，长期运行也不无限增长；
- 清空：clear_all_logs 关闭当前文件句柄、删除全部日志文件后重新打开，
  供设置界面"清空日志"按钮调用。
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_BASE_NAME = "asr_voice.log"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resolve_log_file(cfg) -> Path:
    """解析日志文件绝对路径（与 setup_logging 一致，供 UI 打开/清空复用）。

    锚定规则：相对路径（如默认 logs/asr_voice.log）统一解析到
    打包版 = exe 同目录 / 源码版 = 项目根，且保留 logs/ 子目录；
    绝对路径原样使用（用户显式指定时尊重其意图）。
    """
    log_cfg = cfg.logging
    log_path = Path(str(log_cfg.file))
    if not log_path.is_absolute():
        if _is_frozen():
            # 打包后日志写到 exe 同目录（开机自启时工作目录是 system32，
            # 不能锚定 cwd）；保留配置里的 logs/ 相对目录
            anchor = Path(sys.executable).parent
        else:
            # 源码模式：相对路径锚定项目根
            anchor = Path(__file__).resolve().parent.parent
        log_path = anchor / log_path
    return log_path


def _log_file_pattern(log_path: Path) -> list:
    """匹配当前日志及其滚动/压缩文件：asr_voice.log、asr_voice.log.2026-08-24 等。"""
    return [log_path, *log_path.parent.glob(log_path.name + ".*")]


def cleanup_old_logs(log_path: Path, retention_days: int) -> None:
    """删除超过 retention_days 天的旧日志（按文件修改时间判定）。"""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for p in _log_file_pattern(log_path):
        try:
            if p.exists() and p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            logging.getLogger(__name__).warning("清理旧日志失败：%s", p, exc_info=True)


def setup_logging(cfg) -> None:
    """根据配置初始化日志（按天滚动 + 启动清理）。"""
    log_cfg = cfg.logging
    level = getattr(logging, str(log_cfg.level).upper(), logging.INFO)
    retention_days = int(getattr(log_cfg, "retention_days", 30) or 30)

    log_path = resolve_log_file(cfg)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 启动清理：删除 retention_days 天前的旧日志
    cleanup_old_logs(log_path, retention_days)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 按天滚动：零点切文件，backupCount=保留天数 控制同方式备份上限；
    # delay=True 首次落盘才建文件，避免无日志时也占一个空文件
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=max(1, retention_days),
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 非打包模式才输出到控制台（打包后无控制台）；pythonw 启动时
    # sys.stdout 为 None，跳过
    if not _is_frozen() and sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)


def clear_all_logs(cfg) -> None:
    """清空全部日志文件并重新打开（供"清空日志"按钮调用）。

    先关闭根 logger 的文件句柄释放占用，再删除当前及所有滚动/压缩文件，
    最后重新 setup_logging 打开一个空文件，运行中亦可安全调用。
    """
    log_path = resolve_log_file(cfg)
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                h.close()
            except Exception:
                logging.getLogger(__name__).debug(
                    "关闭旧日志 handler 失败，忽略", exc_info=True)
            root.removeHandler(h)
    for p in _log_file_pattern(log_path):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            logging.getLogger(__name__).warning("清空日志文件失败：%s", p, exc_info=True)
    # 重新建立空日志
    setup_logging(cfg)
