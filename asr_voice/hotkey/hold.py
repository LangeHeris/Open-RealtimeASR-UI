"""长按说话的状态机（纯逻辑，无 Qt、无 ctypes、不读系统时钟）。

为什么单独一个模块：录音热键与对话热键共用同一套「按住」语义，但两条路径
的副作用完全不同（一个启停录音、一个开合上行闸门）。把「按下/计时/松手」
这段判断抽成纯函数式状态机，两边的差异就只剩「收到 ACTIVATE/END 之后做什么」，
副作用不再混进计时逻辑，也让假时钟单测能覆盖全部迁移。

本模块同时放两条长按路径共用的开关判定 `hold_flag_enabled`：**两条路径读的是
同一个开关** `hotkey.hold_to_talk`（`dialog` 节没有这个键——照 `cfg.dialog` 读会
`getattr` 兜底恒得 False，让整条对话长按永远不生效且不报错）。放在这里，两边
import 同一份实现，不会各自演化出略微不同的版本。

时间由调用方传入（now 秒），因此本模块可被完整单测，见
tests/test_hold_tracker.py。
"""

from __future__ import annotations

# 开关值的白名单真值。配置加载器（loader._coerce_value）只把**字符串**归一化成
# bool，既非 true 也非 false 的字符串原样保留，于是 `hold_to_talk: "meby"`（拼写
# 错误）会以 truthy 字符串走到判定处——naive bool() 会把它当成开启，用户以为关着、
# 功能却开着，且完全看不出来。因此只认明确的真值，其余一律按关闭处理（fail closed）。
HOLD_TRUE_VALUES = frozenset(("true", "1", "yes", "on"))


def hold_flag_enabled(value) -> bool:
    """把长按开关的配置值归一化成布尔：只认 True 与白名单字符串。

    为什么不能直接 bool()：拼错的字符串（`hold_to_talk: "meby"`）是 truthy 的，
    bool() 会让它静默开启功能。这里改成「认不出就是关」——失效方向偏向用户可见
    （功能没生效）而不是静默生效。

    非 True 的非字符串值一律按关闭处理，包括 YAML 里手写的裸 `1`/`0`：加载器只
    归一化字符串，裸数字原样到达这里。裸 `1` 与字符串 `"1"` 语义相同、拒掉它确实
    偏保守，但开关这类取值上 fail-closed 是唯一可接受的失效方向；要开启请写 true
    （或带引号的 "1"，它会被加载器归一化成 True）。
    """
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in HOLD_TRUE_VALUES
    return False


class HoldPhase:
    """状态机内部相位。"""
    IDLE = "idle"        # 未按下
    ARMED = "armed"      # 已按下、未达阈值（不得产生任何副作用）
    HOLDING = "holding"  # 已越过阈值，正式生效


class HoldAction:
    """一次 press/poll 调用要调用方执行的动作。"""
    NONE = "none"          # 无动作
    DISCARD = "discard"    # 轻点：整段撤销，当作没发生（spec 要求短按完全不算录音）
    ACTIVATE = "activate"  # 越过阈值：正式开始
    END = "end"            # 松手：结束本次长按


class HoldTracker:
    """按下 -> 计时 -> 越过阈值 -> 松手 的状态机。

    ARMED 期间只累积时间、不产出业务动作，这是「轻点完全不算录音」在实现层
    的落点：所有会被用户感知的副作用都挂在 ACTIVATE / END 上。
    """

    def __init__(self, threshold_ms: int = 300) -> None:
        self._threshold_s = max(0.0, float(threshold_ms) / 1000.0)
        self._phase = HoldPhase.IDLE
        self._pressed_at: float | None = None  # 首次 poll 建立时间基准

    @property
    def phase(self) -> str:
        return self._phase

    def press(self) -> str:
        """热键按下。已在 ARMED/HOLDING 时忽略（系统自动重复的 keydown）。"""
        if self._phase != HoldPhase.IDLE:
            return HoldAction.NONE
        self._phase = HoldPhase.ARMED
        self._pressed_at = None
        return HoldAction.NONE

    def poll(self, held: bool, now: float) -> str:
        """采样一次按键状态。

        held: 此刻按键是否仍按下（探针调用失败时调用方传 False——宁可提前结束，
              不可永久按住，fail-safe 方向见 spec §7）
        now:  单调时钟秒数
        """
        if self._phase == HoldPhase.IDLE:
            return HoldAction.NONE
        if not held:
            activated = self._phase == HoldPhase.HOLDING
            self._phase = HoldPhase.IDLE
            return HoldAction.END if activated else HoldAction.DISCARD
        if self._pressed_at is None:
            self._pressed_at = float(now)
            if self._threshold_s <= 0.0:
                # 阈值为 0：按下即刻生效（退化配置，仍需可用）
                self._phase = HoldPhase.HOLDING
                return HoldAction.ACTIVATE
            return HoldAction.NONE
        # 时钟回拨不会凭空越过阈值。真正的保障是状态机形状：必须同时满足
        # 「处于 ARMED」且「threshold_s > 0」——阈值为 0 时首个 poll 已直接进入
        # HOLDING，而负的 elapsed 永远不满足 >= threshold_s。因此下面的 max()
        # 是冗余防御（穷举 25 万条 press/poll 轨迹无可区分输入），不是该保障的来源。
        elapsed = max(0.0, float(now) - self._pressed_at)
        if self._phase == HoldPhase.ARMED and elapsed >= self._threshold_s:
            self._phase = HoldPhase.HOLDING
            return HoldAction.ACTIVATE
        return HoldAction.NONE

    def reset(self) -> None:
        """强制回到空闲（会话被外部中断、配置热改等场景）。

        注意：reset() **不产出任何动作**，包括 END。若调用方已按 ACTIVATE 开始了
        某事（例如开始录音），必须自己负责收尾——不要指望 reset() 之后会收到 END，
        否则会留下一个已开始却永不结束的会话。
        """
        self._phase = HoldPhase.IDLE
        self._pressed_at = None
