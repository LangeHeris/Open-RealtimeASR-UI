"""提供商中立的对话事件词汇 —— 两个对话客户端与控制器共享的单一事实源。

豆包（二进制整数事件 150/350/359/450/451/459/550）与阿里云 Qwen
（OpenAI-Realtime 风格 JSON 事件 response.audio.delta / speech_started / …）
各自把 wire 协议翻译成这套中立语义事件；DialogController 只认这些常量，
不认识任何提供商的原始事件码。新增第三个提供商只需写一个新客户端 +
一张映射表，控制器与下行链路（播放器/闸门/barge-in/重连）零改动。

放 asr/ 层：两客户端（asr/）与控制器（core/）都 import，避免 asr→core 倒挂。
设计文档：docs/superpowers/specs/2026-09-11-qwen-audio-realtime-provider-design.md §3。
"""

from __future__ import annotations

# ---- 中立语义事件（on_event 第一参数的取值；payload 键见各事件注释）----
SESSION_READY = "session_ready"        # 会话就绪 → 控制器进 LISTENING   {dialog_id}
USER_TRANSCRIPT = "user_transcript"    # 用户 ASR 中间/最终              {text, is_final}
USER_TURN_END = "user_turn_end"        # 用户说完 → 进 THINKING          {}
AI_TURN_START = "ai_turn_start"        # AI 本轮开始，开音频闸门          {reply_id}
AI_TRANSCRIPT = "ai_transcript"        # AI 回复文本（供上屏）           {text}
BARGE_IN = "barge_in"                  # 用户打断 → 清播放 + 关闸门       {}
AI_TURN_END = "ai_turn_end"            # AI 本轮结束 → 排空判定          {exit_intent}
AI_FULL_TEXT = "ai_full_text"          # 整轮完整文本（豆包 550）：本轮无 350 文本时兜底上屏 {text}


class DialogError(Exception):
    """对话客户端错误（独立异常类，不复用 ASRError——语义不同）。

    code 为服务端错误码（错误帧 code / 51/153/599 payload 的 code），
    本地错误（超时/连接失败）为 -1。报障时把 message 里的 X-Tt-Logid 一并带上。
    """

    def __init__(self, message: str, code: int | str = -1):
        super().__init__(message)
        self.code = code


class DialogErrorEvent:
    """on_error 回调的中立错误载荷：客户端自己分类自己的错误码。

    reconnectable 由客户端判定（只有它认识自己的码段）；控制器据此 +
    auto_reconnect 配置 + 每轮一次限额决定是否重建会话。hint 是单行中文
    提示（悬浮条右截断），同样从控制器下沉到客户端。
    code 可为 int（豆包）或 str（Qwen error.code）。
    """

    __slots__ = ("code", "message", "hint", "reconnectable")

    def __init__(self, code: int | str, message: str, hint: str = "",
                 reconnectable: bool = False):
        self.code = code
        self.message = message
        self.hint = hint
        self.reconnectable = reconnectable
