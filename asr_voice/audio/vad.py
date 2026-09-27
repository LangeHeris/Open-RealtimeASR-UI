"""基于能量的语音活动检测（VAD）。

通过 RMS 能量阈值判断每个音频块是否为语音：
- 语音开始时进入 SPEAKING 状态
- 连续静音超过 silence_duration_ms 判定语音结束
- 保留 pre_speech_buffer_ms 的预缓冲，避免吃掉首字
"""

from __future__ import annotations

import collections
import logging
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 事件类型
EVENT_LEVEL = "level"              # 当前块能量，用于 UI 显示
EVENT_SPEECH_START = "speech_start"  # 语音开始（值为 None）
EVENT_SPEECH_END = "speech_end"    # 语音结束（值为 np.ndarray 整段音频）
EVENT_SPEECH_TOO_SHORT = "speech_too_short"  # 语音过短被丢弃

STATE_IDLE = "idle"
STATE_SPEAKING = "speaking"

VadEvent = Tuple[str, object]


class VAD:
    """简单能量 VAD。"""

    def __init__(
        self,
        sample_rate: int,
        block_size: int,
        energy_threshold: float = 500.0,
        silence_duration_ms: int = 600,
        min_speech_ms: int = 300,
        pre_speech_buffer_ms: int = 200,
    ):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.block_ms = block_size * 1000 / sample_rate
        self.energy_threshold = energy_threshold
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_ms = min_speech_ms

        pre_blocks = max(1, int(round(pre_speech_buffer_ms / self.block_ms)))
        self._pre_buffer: "collections.deque[np.ndarray]" = collections.deque(
            maxlen=pre_blocks
        )
        self._silence_blocks = 0
        self._speech_blocks: List[np.ndarray] = []
        self._state = STATE_IDLE

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        self._pre_buffer.clear()
        self._speech_blocks.clear()
        self._silence_blocks = 0
        self._state = STATE_IDLE

    @staticmethod
    def compute_rms(block: np.ndarray) -> float:
        """计算 RMS 能量，返回 int16 量纲的值便于阈值配置。"""
        if block.dtype == np.int16:
            data = block.astype(np.float32) / 32768.0
        else:
            data = block.astype(np.float32)
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(data ** 2)) * 32768.0)

    def feed(self, block: np.ndarray) -> List[VadEvent]:
        """处理一个音频块，返回事件列表。"""
        events: List[VadEvent] = []
        energy = self.compute_rms(block)
        events.append((EVENT_LEVEL, energy))

        is_speech = energy >= self.energy_threshold

        if self._state == STATE_IDLE:
            self._pre_buffer.append(block)
            if is_speech:
                self._state = STATE_SPEAKING
                self._speech_blocks = list(self._pre_buffer)
                self._silence_blocks = 0
                events.append((EVENT_SPEECH_START, None))
                logger.debug("VAD 语音开始 energy=%.1f", energy)

        elif self._state == STATE_SPEAKING:
            self._speech_blocks.append(block)
            if is_speech:
                self._silence_blocks = 0
            else:
                self._silence_blocks += 1
                silence_ms = self._silence_blocks * self.block_ms
                if silence_ms >= self.silence_duration_ms:
                    duration_ms = len(self._speech_blocks) * self.block_ms
                    if duration_ms >= self.min_speech_ms:
                        audio = np.concatenate(self._speech_blocks, axis=0)
                        events.append((EVENT_SPEECH_END, audio))
                        logger.debug("VAD 语音结束 duration=%.0fms", duration_ms)
                    else:
                        events.append((EVENT_SPEECH_TOO_SHORT, None))
                        logger.debug("VAD 语音过短 %.0fms，丢弃", duration_ms)
                    self._state = STATE_IDLE
                    self._speech_blocks.clear()
                    self._pre_buffer.clear()
                    self._silence_blocks = 0

        return events
