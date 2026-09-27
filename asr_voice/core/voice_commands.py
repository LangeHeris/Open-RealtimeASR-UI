"""语音命令模式：触发词 + 本地命令表 + 自定义短语 + 「帮我」AI 路径。

四层结构（接入点见 app._on_asr_result / _maybe_route_command）：
1. 触发词（默认"听我说"，commands.prefix 可配）：final 句首命中即进入
   命令态，该句不上屏、不进历史、不送 LLM 修正。剥离触发词后有余文
   则直接作为命令；无余文进入待命态（下一句 final 作为命令）。
2. 本地命令表：有限动作命令（按键/设置切换），纯字符串匹配 + 手写
   变体。云端 ASR 对短命令词识别准确率高，不做拼音容错（升级口：
   未命中记日志，按日志补变体或换匹配函数即可）。先剥 AI 前缀再查
   表，「帮我发送」也能正确回车（查表优先，LLM 按不了键）。
3. 自定义短语（commands.phrases_enable 开启时）：整句与短语名精确
   匹配则返回 ("phrase", 内容, "")，由 app 整段注入。查表优先于短语
   （短语命名撞上命令词时命令生效）；词表见 core.custom_phrases。
4. 「帮我」前缀（commands.ai_prefix）：显式 AI 声明。查表不中且有该
   前缀 -> 送 LLM 对选中文本做变换（润色/翻译/扩写/改语气…）。

线程模型：detect_prefix/route 为纯函数（ASR 线程调用）；动作执行经
queued signal 切主线程（app._on_command_action）；AI 链路在注入线程
串行完成（injector.read_selection 回调 -> ai_call -> 粘贴替换）。

i18n 边界（A6 判定）：本模块的中文**不是界面文案**，而是运行时匹配目标 ——
- 触发词 / 「帮我」前缀 / 开关的「关·停用·取消」：ASR 要匹配的语音，与
  config 默认值（`config/defaults.py` 的 commands.prefix / ai_prefix）同源，
  翻译它会直接改变用户必须说出的口令；
- `_SIMPLE_COMMANDS` / `_TOGGLE_COMMANDS` 变体：同一件事，匹配用户说出的词
  （变体表同时含中文与英文说法，本就语言无关）；
- `AI_PROCESS_PROMPT`：发给大模型的提示词，中文是刻意选择。
所以整份文件用 `# i18n: not-ui-copy` 跳过守卫。**代价**：本文件日后若新增
真正的界面文案，守卫不会拦 —— review 时需人工确认。

设置页「命令参考」表（`ui/settings_dialog.py:1358` 调 `local_commands()`）
虽把变体拼成「 / 」展示，但那列的表头原文是「说法（中文 / 英文均可）」，
即它列的是**可说的词**而非待翻译的界面文案，故不在本批抽串范围；
该表的「效果」列等真正文案归第四批处理。
"""
# i18n: not-ui-copy
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---- 配置（app 启动/热加载时同步）----
# 默认关：命令模式按需开启（右键菜单「注入逻辑 ▸ 语音命令」），
# 关闭时本模块所有检测短路返回，逐字同步零额外延迟
_enabled = False
_prefix = "听我说"
_ai_prefix = "帮我"
_phrases_enabled = False
# 「帮我」AI 命令的思考模式（默认开，与 AI 修正的 disable_thinking 独立）
_ai_thinking = True
# 「帮我」AI 命令提示词模板：空=用内置默认透传模板（AI_PROCESS_PROMPT）；
# 非空则按用户模板替换 {selection}/{command} 占位符
_ai_prompt_template = ""


def set_config(enabled: bool, prefix: str, ai_prefix: str,
               phrases_enabled: bool = False,
               ai_thinking: bool = True,
               ai_prompt_template: str = "") -> None:
    """同步 commands 配置（热加载后由 app 再次调用）。"""
    global _enabled, _prefix, _ai_prefix, _phrases_enabled
    global _ai_thinking, _ai_prompt_template
    _enabled = bool(enabled)
    _prefix = (prefix or "听我说").strip()
    _ai_prefix = (ai_prefix or "帮我").strip()
    _phrases_enabled = bool(phrases_enabled)
    _ai_thinking = bool(ai_thinking)
    _ai_prompt_template = str(ai_prompt_template or "")


def ai_thinking_enabled() -> bool:
    return _ai_thinking


def enabled() -> bool:
    return _enabled


# 动作注册表：app 启动时注入（避免本模块反向依赖 app）
_handlers: dict[str, Callable] = {}


def register_handlers(mapping: dict[str, Callable]) -> None:
    """注册动作 ID -> 执行函数。开关类动作签名为 handler(bool)。"""
    _handlers.clear()
    _handlers.update(mapping)


def get_handler(action: str) -> Optional[Callable]:
    return _handlers.get(action)


# ---- 本地命令表（云端 ASR + 短命令词，纯字符串匹配足够）----

# 简单命令（无参数）：(口语变体元组, 动作 ID)。变体含中英文（匹配
# 统一 lower：中文不受影响，英文命令不区分大小写）
_SIMPLE_COMMANDS: list[tuple[tuple[str, ...], str]] = [
    (("发送", "回车", "press enter"), "enter"),
    (("删除那句", "删掉那句", "删除刚听写", "delete that"), "delete_last"),
    (("全选", "选择全部", "select all"), "select_all"),
    (("撤销", "恢复上一步", "undo"), "undo"),
    (("退格", "删一个字", "backspace"), "backspace"),
    (("停止录音", "停止", "停止听写", "stop listening"), "stop_record"),
    (("逐字输入", "切逐字"), "method_keys"),
    (("剪贴输入", "剪贴板输入", "切剪贴"), "method_clip"),
]

# 开关命令（布尔参数）：文本含"关/停用/取消"=off，否则 on
_TOGGLE_COMMANDS: list[tuple[tuple[str, ...], str]] = [
    (("保留剪贴",), "keep_clipboard"),
    (("逐字同步",), "live_intermediate"),
    (("AI修正", "智能修正"), "llm_enable"),
    (("删除打断", "删除键打断"), "interrupt_delete"),
]


def toggle_actions() -> set[str]:
    """开关类动作 ID 集合（app 据此决定是否传 bool 参数）。"""
    return {action for _, action in _TOGGLE_COMMANDS}


def local_commands() -> list[tuple[tuple[str, ...], str, bool]]:
    """本地命令表快照（设置页命令参考展示用）。

    返回 (说法变体元组, 动作 ID, 是否开关类)。参考表直接由此生成，
    变体增删不需要同步改 UI。

    注意（A6）：返回的变体是**可说的词**（中文/英文混合），不是界面文案，
    调用方直接拼接展示即可，不要对这些词调 t()（词表见模块 docstring 的
    i18n 边界说明）。
    """
    return [(v, a, False) for v, a in _SIMPLE_COMMANDS] + [
        (v, a, True) for v, a in _TOGGLE_COMMANDS
    ]


_PUNCT = "。，、！？；：.,!?;:~～… \t"


def _clean(text: str) -> str:
    """去首尾空白与标点（触发词/命令词两侧的语气残留）。"""
    return (text or "").strip().strip(_PUNCT).strip()


def is_valid_command_text(text: str) -> bool:
    """是否为可作为命令处理的非噪声文本（纯标点帧忽略）。"""
    return bool(_clean(text))


def detect_prefix(text: str) -> tuple[bool, str]:
    """final 文本句首是否命中触发词。返回 (命中, 余文)。"""
    t = _clean(text)
    if not t:
        return False, ""
    if t.startswith(_prefix):
        return True, _clean(t[len(_prefix):])
    return False, ""


def may_be_command_start(text: str) -> bool:
    """中间帧预判：是否可能是命令句的开头（用于跳过逐字同步上屏）。

    True = 文本以触发词开头（命令句已成形），或触发词以文本开头
    （正在说触发词的途中，如"芝麻开"）。此时中间帧不上屏，等 final
    判定：是命令则整句静默；不是命令（口述内容恰好撞上触发词前缀，
    极罕见）final 会整句上屏，不丢字。

    只拦"长得像触发词"的帧，不拦短帧：同音误识别（如"之嘛开们"）
    文字对不上触发词就是没有信号，拦短帧只是把泄漏推迟一帧（同音字
    长到触发词长度照样会打出去顶掉选区），却让每句开头都慢一拍，
    得不偿失。同音残留由 final 命中后的 cancel_intermediate 清理。
    口径与 detect_prefix 一致（_clean 去首尾空白+标点）：引擎给句首
    补标点（如"，听我说"）时 lstrip 拦不住，中间帧会同步上屏、
    final 又被判为命令，输入框留下残字。
    """
    if not _enabled or not text:
        return False
    t = _clean(text)
    return t.startswith(_prefix) or _prefix.startswith(t)


def route(text: str) -> tuple[str, str, str]:
    """命令文本路由。

    返回 ("local", action, arg) / ("phrase", 内容, "") /
    ("ai", instruction, "") / ("unknown", 原文, "")。
    """
    t = _clean(text)
    if not t:
        return "unknown", text, ""
    # 剥 AI 前缀后仍先查表："帮我发送"应回车，不进 LLM
    is_ai = t.startswith(_ai_prefix)
    body = _clean(t[len(_ai_prefix):]) if is_ai else t
    if body:
        hit = _match_local(body)
        if hit:
            return hit
    # 自定义短语：整句精确匹配短语名（查表优先于短语）
    if _phrases_enabled and body:
        from . import custom_phrases  # 延迟导入（防循环引用）

        content = custom_phrases.find(body)
        if content is not None:
            return "phrase", content, ""
    if is_ai and body:
        return "ai", body, ""
    logger.info("语音命令未匹配：%r", text)
    return "unknown", text, ""


def _match_local(t: str) -> Optional[tuple[str, str, str]]:
    # lower 统一：变体与文本都转小写，英文命令不区分大小写（中文不受影响）；
    # 另做一份去空格副本，"AI 修正"等空格差异写法也能命中"AI修正"
    tl = t.lower()
    tl_ns = tl.replace(" ", "")

    def _hit(v: str) -> bool:
        vl = v.lower()
        return vl in tl or vl.replace(" ", "") in tl_ns

    for variants, action in _SIMPLE_COMMANDS:
        if any(_hit(v) for v in variants):
            return "local", action, ""
    for variants, action in _TOGGLE_COMMANDS:
        if any(_hit(v) for v in variants):
            off = any(w in t for w in ("关", "停用", "取消"))
            return "local", action, "off" if off else "on"
    return None


# ---- 「帮我」AI 文本变换（逻辑独立于 llm_corrector，HTTP 共享 openai_client）----

# 模板用 replace 填充（selection/command 可能含花括号，format 会炸）。
# 透传设计：选中内容与用户口令原样交给模型，由模型自行理解处理，
# 不预设任务清单。唯一工程约束：要求直接输出结果正文——返回值会
# 原样粘贴进用户文档，混入解释性文字会污染正文
AI_PROCESS_PROMPT = (
    "用户选中了一段文本，并对它下了一条语音指令。"
    "按指令处理选中的文本，直接输出处理结果，"
    "不要输出任何解释、开场白或引号。\n\n"
    "选中的文本：\n{selection}\n\n语音指令：{command}"
)


def ai_call(base_url: str, api_key: str, model: str, timeout_ms: int,
            selection: str, command: str,
            disable_thinking: bool = True) -> str:
    """「帮我」路径的 LLM 调用（同步阻塞，注入线程执行）。

    不依赖/不改动 llm_corrector 实例（命令模式独立于修正开关，修正器
    可能未创建）；HTTP 细节与修正链路共享 postprocess.openai_client
    （I04 去重）。透传设计：选中内容与口令原样拼进提示词，由模型
    自行理解处理。
    """
    from ..postprocess.openai_client import chat_completion

    # 模板：用户自定义（非空）优先，否则用内置默认透传模板
    template = _ai_prompt_template or AI_PROCESS_PROMPT
    content = (template
               .replace("{selection}", selection)
               .replace("{command}", command))
    return chat_completion(
        base_url, api_key, model, content,
        timeout_ms=timeout_ms,
        temperature=0.4,
        # 变换结果可能略长于原文（扩写/补标点），按原文长度放大
        max_tokens=len(selection) * 2 + 400,
        disable_thinking=disable_thinking,
    )
