"""语音模式（对话）→ 历史记录：**一轮问答合成一条**。

用户诉求：「对话的内容也可以保存进历史记录里」（历史是"能复制走的文本"，
不是会话存档——所以条目是纯文本多行，不是结构化记录）。

配对规则（依据 core/dialog 的轮次语义）：
- 轮次号只在**说话人变化**时自增（见 DialogController._begin_turn），所以一个
  轮次只属于一个说话人，一次完整问答 = 用户轮 + AI 轮；
- 同一轮内会被反复推（豆包按句累积、Qwen 按 delta）⇒ 只留该轮**最后一次**
  文本，中间态不单独成条（否则历史会被半句刷屏）；
- 落库时机：① 用户开口开新轮（上一组「用户+AI」已完整）② 退出语音模式 /
  关闭程序时 flush。代价是历史里比屏幕晚一轮出现——配对必须等这轮结束；
- 打断（barge-in / 取消本轮）按用户实际看到、听到的那份文本记录，不加额外标记。

门控沿用「历史记录」总开关（core.history.add 自带）：关掉时对话同样不入库，
不新增开关。

线程：dialog_turn / state_changed 由控制器（asyncio 线程）发射，落到本模块的
槽可能是直投；内部加锁，与主线程的 flush 互斥。
"""
from __future__ import annotations

import logging
import threading

from . import history as _history
from .dialog import DIALOG_UI_STATES, ROLE_AI, ROLE_USER
from ..i18n import t

logger = logging.getLogger(__name__)

# 角色前缀复用气泡卡片的词条（zh「你 / AI」、en「You / AI」）：
# 历史是一条条纯文本，没有角色色带可依赖，前缀是唯一能表达"谁说的"的手段。
_ROLE_LABEL_KEYS = {ROLE_USER: "bubble.role.user", ROLE_AI: "bubble.role.ai"}


def format_exchange(user_text: str, ai_text: str) -> str:
    """一轮问答 → 一条历史条目；只有一方有内容时只写那一行。

    纯函数（词条在调用时取，不焊死语言），便于单测与复用。
    """
    sep = t("bubble.role.sep")
    lines = []
    for role, text in ((ROLE_USER, user_text), (ROLE_AI, ai_text)):
        body = (text or "").strip()
        if body:
            lines.append(t(_ROLE_LABEL_KEYS[role]) + sep + body)
    return "\n".join(lines)


class ExchangeRecorder:
    """把 dialog_turn 流聚合成"一轮问答"，落库交给 core.history。

    用法（app 侧）：``on_turn`` 接 ``dialog_turn`` 信号，``flush`` 在离开
    语音模式（state_changed 收到非对话态）和程序退出时调用。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq: int | None = None
        self._text: dict[str, str] = {ROLE_USER: "", ROLE_AI: ""}

    def on_turn(self, turn_seq: int, role: str, text: str,
                is_final: bool = False, interrupted: bool = False) -> None:
        """dialog_turn 槽（签名与信号一致；is_final/interrupted 仅语义留痕）。

        is_final 不能当作"这轮结束"用：AI 侧每次累积刷新都是 True（豆包每句、
        兜底整轮全文），用户侧也有中间结果。真正的轮次边界只有 turn_seq 变化。
        """
        body = (text or "").strip()
        with self._lock:
            new_turn = self._seq is not None and turn_seq != self._seq
            self._seq = turn_seq
            if new_turn and role == ROLE_USER:
                # 用户开口开新轮 ⇒ 上一组「用户+AI」已经完整。
                # 只在**用户轮**落库：AI 轮到来时还要与刚说的用户句配对，不能清。
                self._flush_locked()
            if body and role in self._text:
                self._text[role] = body

    def flush(self) -> None:
        """把还没落库的那组写进历史（退出语音模式 / 退出程序时调用）；幂等。"""
        with self._lock:
            self._flush_locked()

    def on_state(self, state: str) -> None:
        """dialog.state_changed 槽：离开对话态（收到 idle）即 flush。

        判断放在本模块（而不是 app 侧）是因为"这轮结束没结束"属于配对语义：
        app 只需把信号接过来，不必认识对话态集合。
        """
        if state not in DIALOG_UI_STATES:
            self.flush()

    def _flush_locked(self) -> None:
        entry = format_exchange(self._text[ROLE_USER], self._text[ROLE_AI])
        # 无论有没有内容都清空：重复 flush 不会把同一组记两遍
        self._text = {ROLE_USER: "", ROLE_AI: ""}
        if entry:
            _history.add(entry)
