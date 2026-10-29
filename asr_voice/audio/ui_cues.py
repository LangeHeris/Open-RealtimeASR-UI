"""录音起停提示音：按样式播放内置短音效。

样式 = 一组「开始/停止」WAV。当前内置三种：
- `bubble`（气泡）：freesound 拨片采样改造，开始原声裁短、停止降调；
- `cork`（瓶塞）：freesound 酒瓶塞爆裂声，同样开始原声、停止降调 + 低通；
- `click`（点击）：现代 UI 轻点，高频 click 起音 + 低频 click 收束。

后续新增样式只需把成对 WAV 放进 assets/sounds 并登记到 STYLES。

刻意不用 QtMultimedia，也不占用对话 TTS 的播放流：Windows 原生 winsound 异步
播放 WAV，延迟低、不碰音频设备。提示音绝不能影响录音主流程，失败只记日志。
"""

from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

STYLE_BUBBLE = "bubble"
STYLE_CORK = "cork"
STYLE_CLICK = "click"
DEFAULT_STYLE = STYLE_BUBBLE

_SOUNDS_DIR = Path(__file__).resolve().parent.parent / "assets" / "sounds"
# 样式 → (开始文件, 停止文件)
STYLES = {
    STYLE_BUBBLE: ("bubble_start.wav", "bubble_stop.wav"),
    STYLE_CORK: ("cork_start.wav", "cork_stop.wav"),
    STYLE_CLICK: ("click_start.wav", "click_stop.wav"),
}

_durations: dict[str, float] = {}


def available_styles() -> tuple[str, ...]:
    """已内置的提示音样式（设置页的选择器用）。"""
    return tuple(STYLES)


def _wav_seconds(path: Path) -> float:
    """WAV 时长（秒）：停止音要靠它决定「等声音放完再关麦」的时长。"""
    key = str(path)
    if key not in _durations:
        try:
            with wave.open(key, "rb") as wav:
                rate = wav.getframerate() or 0
                frames = wav.getnframes()
            _durations[key] = frames / rate if rate else 0.0
        except Exception:
            logger.warning("提示音时长读取失败：%s", path, exc_info=True)
            _durations[key] = 0.0
    return _durations[key]


def _play(style: str, kind: str) -> float:
    """播放指定样式的提示音，返回音频时长（秒）；未播放返回 0。"""
    if sys.platform != "win32":
        return 0.0
    names = STYLES.get(style) or STYLES[DEFAULT_STYLE]
    path = _SOUNDS_DIR / (names[0] if kind == "start" else names[1])
    if not path.is_file():
        logger.warning("提示音文件缺失，跳过：%s", path)
        return 0.0
    try:
        import winsound
    except Exception:
        logger.warning("winsound 不可用，跳过提示音", exc_info=True)
        return 0.0
    try:
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:
        logger.warning("提示音播放失败：%s", path, exc_info=True)
        return 0.0
    logger.info("提示音：%s（%s）", "开始" if kind == "start" else "停止", style)
    return _wav_seconds(path)


def play_start(style: str = DEFAULT_STYLE) -> float:
    """录音开始：短促起音。返回音频时长（秒）。"""
    return _play(style, "start")


def play_stop(style: str = DEFAULT_STYLE) -> float:
    """录音结束：收束音。返回音频时长（秒），调用方据此等放完再关麦。"""
    return _play(style, "stop")
