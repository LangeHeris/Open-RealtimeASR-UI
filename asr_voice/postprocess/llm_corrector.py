"""LLM 文本后处理：调用 OpenAI 兼容接口修正 ASR 输出。

设计：
- 异步先注入原文，后台调 LLM 修正，完成后替换（退格删原文 + 重写修正版）。
- 超时/失败降级用原文，不阻塞用户。
- 累积窗口可配置：batch_sentences=1 每句即时修正（默认，验证版行为）；
  >1 攒 N 句一次性修正（省 API 调用、上下文更足），距上句静音超过
  batch_idle_ms 也触发剩余批次。
- OpenAI 兼容协议：OpenAI / DeepSeek / Moonshot / 智谱 / 通义 / 本地 vLLM / Ollama
  仅改 base_url + api_key + model 即可切换。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..i18n import language

logger = logging.getLogger(__name__)

# 内置修正 prompt（用户可在 config.llm.prompt 留空用内置模板，或自定义覆盖）
# 关键：末尾把 {text} 紧贴指令，并在指令中重复"仅输出修正后文本"——
# 防止 LLM 把"文本："后空段当成等输入，返回"请提供需要修正的文本"之类
# 元话语。部分小模型（gpt-4o-mini / deepseek-chat 等）在指令距输入远时
# 容易产出说明性文字污染修正结果。
# 中英各一份：英文用户看着英文界面，发出去的中文指令会偶发招回中文
# 说明性文字（上面这条风险在非中文 prompt 下更明显），故按界面语言选。
DEFAULT_PROMPT_ZH = (
    "修正以下 ASR 识别的中文文本。直接输出修正后的纯文本，"
    "不要输出任何说明、解释、前缀、引号或\"请提供文本\"之类的话语。\n"
    "规则：\n"
    "- 修正同音错别字（结合上下文，如\"识图\"->\"识别\"）\n"
    "- 仅当句子较长（含多个分句/逗号自然断点）时补全标点；"
    "短句（几个字、单个词）不加任何标点，保持原样\n"
    "- 去除口癖（\"那个\"\"然后\"\"嗯\"等无意义填充词）\n"
    "- 不增删原意、不重写\n\n"
    "文本：{text}"
)

DEFAULT_PROMPT_EN = (
    "Correct the following Chinese ASR transcription. Output only the corrected "
    "plain text with no explanations, prefixes, quotes or meta commentary.\n"
    "Rules:\n"
    "- Fix homophone/typo errors using context\n"
    "- Add punctuation only when the sentence is long (multiple clauses); "
    "keep short phrases unpunctuated\n"
    "- Remove filler words (\"um\", \"like\", repeated words)\n"
    "- Do not change meaning or rewrite\n\n"
    "Text: {text}"
)


def prompt_for_language(prompt: str | None) -> str:
    """按界面语言选内置模板；用户自定义（且非旧默认值）优先。

    留空 → 内置模板（zh/en 按 i18n.language()）。
    等于 DEFAULT_PROMPT_ZH → 旧版本把该文本写进过 config.llm.prompt（以及
    设置页「内置方案」预设），那不是用户的意图而是迁移残留，同样按语言
    归一化；否则英文界面下会一直发中文指令。
    其余非空值 → 返回自定义原文，但**去除首尾空白**（prompt 首尾空白无
    意义，且 yaml 多行块标量常带尾随换行）。
    非字符串值（yaml 写成数字/布尔等手滑内容）按留空处理——比 str() 成
    "123" 这种伪 prompt 更接近用户意图，也避免 .strip() 直接崩。

    注意本函数在 LLMCorrector 构造时求值：语言切换需重启生效（i18n 无热
    切换，与 spec §8 一致），已建实例的 prompt 不会中途变语言。
    局限：用户若刻意把 DEFAULT_PROMPT_ZH 原文当作自定义 prompt 粘贴进配置，
    会被判为迁移残留而按语言归一化——刻意想用这段中文的英文界面用户需
    改一字（如加个空格后的自定义变体）。
    """
    clean = prompt.strip() if isinstance(prompt, str) else ""
    if not clean or clean == DEFAULT_PROMPT_ZH.strip():
        return DEFAULT_PROMPT_EN if language() == "en" else DEFAULT_PROMPT_ZH
    return clean


class LLMCorrector:
    """LLM 文本修正器：先注入原文，后台修正后替换。

    线程模型：feed() 在主线程调用（原文注入由调用方完成），
    累积窗口满足或静音超时后启动后台线程调 LLM；
    LLM 完成后通过 on_corrected 回调（queued 到主线程）执行替换注入。
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout_ms: int = 3000,
        prompt: str = "",
        disable_thinking: bool = True,
        batch_sentences: int = 1,
        batch_idle_ms: int = 1000,
        history_provider: Optional[Callable[[], list]] = None,
        on_corrected: Optional[Callable[[str, str], None]] = None,
        on_corrected_multi: Optional[Callable[[list, float], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_ms = timeout_ms
        self.prompt = prompt_for_language(prompt)
        # True=请求附加"关闭思考"参数。官方支持关闭的模型都能生效：
        #   DeepSeek V4（deepseek-v4-flash/pro，思考默认开启）: thinking.type=disabled
        #   智谱 GLM-Zero / 通义 Qwen3：enable_thinking=false
        #   OpenAI o 系：reasoning_effort=minimal
        # 关不掉的旧模型（deepseek-reasoner/R1）参数被忽略即保留原输出，
        # 用户应更换支持关闭的模型。多数 OpenAI 兼容平台忽略未知字段不报错。
        self.disable_thinking = disable_thinking
        # 累积窗口：batch_sentences<=1 每句即时修正；>1 攒 N 句一次修正，
        # 距上句静音超过 batch_idle_ms 也触发（避免最后几句卡在窗口里）
        self.batch_sentences = max(1, int(batch_sentences or 1))
        self.batch_idle_ms = max(200, int(batch_idle_ms or 1000))
        self._pending: list[str] = []      # 累积缓冲（主线程 feed / idle 线程消费）
        self._pending_lock = threading.Lock()
        self._idle_gen = 0                 # 静音计时代次（新 feed 使旧计时失效）
        self._last_feed_at = 0.0           # 最近一次 feed 的注入时刻（回调带回，供替换防错删）
        # 在途修正请求数（_flush 加 / worker 回调前减）：busy() 据此报告
        # "是否还有没回来的修正"，语音命令「发送」的延迟回车依赖它
        self._inflight = 0
        # 历史上下文提供者：返回历史记录列表（最新在前）。非 None 时
        # 每次送修把最近若干条历史一并拼进 prompt（帮助 LLM 理解语境），
        # provider 由调用方注入（llm.history_context 开关控制传入与否），
        # 本模块不直接依赖 core.history（避免层级反向依赖）
        self.history_provider = history_provider
        # on_corrected(原文本, 修正文本)：由调用方在主线程执行替换注入
        self.on_corrected = on_corrected
        # on_corrected_multi(pairs, requested_at)：多句批次逐句替换用
        # pairs: list[(原句, 修正句)]，顺序=上屏顺序；requested_at 防错删
        self.on_corrected_multi = on_corrected_multi

    def feed(self, text: str, injected_at: float = 0.0) -> None:
        """ASR FINAL 到达：原文已由调用方注入，这里入累积队列并调度后台修正。

        injected_at：本条文本上屏的时刻（time.time()），修正完成回调时带回，
        供替换执行方判断"修正期间是否又有新句子上屏"（有则取消替换防误删）。
        调用方流程：injector.inject(text) → corrector.feed(text, time.time())。
        """
        if not text or not self.api_key:
            return
        # 去掉原文首尾空白（LLM 输入要干净，且替换时按此长度退格）
        clean = text.strip()
        if not clean:
            return
        # 批次发起时刻 = 本条（最新句）的上屏时刻；累积模式取最后一句的
        self._last_feed_at = injected_at or time.time()
        if self.batch_sentences <= 1:
            # 每句即时修正（最小版行为）
            self._flush([clean])
            return
        with self._pending_lock:
            self._pending.append(clean)
            if len(self._pending) >= self.batch_sentences:
                texts = list(self._pending)
                self._pending = []
                flush = True
            else:
                flush = False
        if flush:
            self._flush(texts)
        else:
            self._arm_idle()

    def _arm_idle(self) -> None:
        """重置静音计时：起新计时线程，旧代次自动失效。"""
        with self._pending_lock:
            self._idle_gen += 1
            gen = self._idle_gen
        threading.Thread(
            target=self._idle_worker, args=(gen,),
            daemon=True, name="llm-idle",
        ).start()

    def flush_pending(self) -> None:
        """立即送修累积缓冲中未送修的句子（会话结束/外部触发时调用）。

        与静音触发解耦：batch_idle 可以保持小（快响应），会话结束时
        （无语音超时/手动停止）残留句子被强制送修，不会因等窗口丢失。
        会话结束后不再有新 FINAL，替换必然安全（新句检测不触发）。
        """
        with self._pending_lock:
            if not self._pending:
                return
            texts = list(self._pending)
            self._pending = []
        self._flush(texts)

    def _idle_worker(self, gen: int) -> None:
        """静音超时：窗口未满也触发剩余批次。"""
        time.sleep(self.batch_idle_ms / 1000.0)
        with self._pending_lock:
            if gen != self._idle_gen or not self._pending:
                return
            texts = list(self._pending)
            self._pending = []
        self._flush(texts)

    def _flush(self, texts: list[str]) -> None:
        """启动后台 LLM 修正。

        上屏原文 = 逐句无分隔符拼接（退格长度基准）；多句批次送修时改用
        换行分隔 + 要求逐句逐行返回（见 _correct_worker 的行数校验）--
        直接整段拼接送修会让 LLM 合并/吞掉其中某句，替换后退格删掉全部
        原文却只写回缺句的版本，被吞的那句就"消失"了。
        """
        texts = [t for t in texts if t]
        if not texts:
            return
        joined = texts[0] if len(texts) == 1 else "\n".join(texts)
        original = "".join(texts)
        requested_at = self._last_feed_at   # 批次最后一句的上屏时刻（回调带回）
        # 在途计数 +1（busy() 语义见 __init__）：在锁内增减防并发丢更新
        with self._pending_lock:
            self._inflight += 1
        t = threading.Thread(
            target=self._correct_worker,
            args=(joined, texts, original, len(texts), requested_at),
            daemon=True,
            name="llm-correct",
        )
        t.start()

    def busy(self) -> bool:
        """是否仍有未送修/在途的修正请求（语音命令「发送」延迟回车用）。

        注意 _pending 只在锁内读；_inflight 在 _flush 加、worker 回调
        发出前减——调用方收到回调时本方法已不会把该请求算在途。
        """
        with self._pending_lock:
            return bool(self._pending) or self._inflight > 0

    def _correct_worker(self, joined: str, texts: list[str], original: str,
                        n_sentences: int, requested_at: float) -> None:
        """后台线程：调 LLM 修正，成功后回调 on_corrected / on_corrected_multi。

        joined：送修文本（多句时换行分隔）；texts：批内逐句原文；original：
        上屏原文拼接（供单句/降级退格位错）；n_sentences：批内句数（>1 时
        逐句配对校验）；requested_at：批次发起时最后一句的上屏时刻。

        先算结果、减在途计数、再发回调：调用方（app）收到回调时 busy()
        已不把本请求算在途，否则"最后一个修正完成后的延迟回车"会因
        看到伪忙而永远等下去。
        """
        # ---- 计算阶段（不发回调）----
        multi_pairs = None   # 非 None=多句批次成功，走 on_corrected_multi
        single = None        # 单句/降级路径的修正文本；None=失败
        failed = False
        try:
            corrected_raw = self._call_llm(joined, sentence_count=n_sentences)
            if n_sentences > 1:
                # 多句批次：逐行拆回，剥掉 LLM 可能加的行首序号。
                # 逐行与原文句配对：行数必须 == 批句数，且**任一行为空**
                # 即判定 LLM 合并/吞句了——整批放弃替换保留原文（宁可不修，
                # 不能丢句）。空行不过滤后再比较，避免数量失真。
                import re
                no_prefix = re.compile(r"^\s*\d+\s*[.、)．]:?\s*")
                lines = [no_prefix.sub("", ln).strip()
                         for ln in corrected_raw.splitlines()]
                pairs = [(texts[i], lines[i]) for i in range(n_sentences)]
                if (len(lines) != n_sentences
                        or any(not cr for _, cr in pairs)):
                    logger.warning(
                        "LLM 多句修正行数不符或含空行（%d/%d），保留原文：%r",
                        len([ln for ln in lines if ln]), n_sentences,
                        corrected_raw[:60],
                    )
                    failed = True
                elif self.on_corrected_multi:
                    multi_pairs = pairs
                else:
                    # 调用方未提供多句回调：降级为整段拼接（仅保留原文长度语义）
                    single = "".join(cr for _, cr in pairs)
            else:
                single = corrected_raw.strip()
        except Exception:
            logger.warning("LLM 修正失败，保留原文", exc_info=True)
            failed = True
        finally:
            with self._pending_lock:
                self._inflight -= 1

        # ---- 回调阶段 ----
        if multi_pairs is not None:
            self.on_corrected_multi(multi_pairs, requested_at)
            return
        corrected = single
        if failed:
            if self.on_corrected:
                # 失败也通知（传 None 表示出错）
                self.on_corrected(original, None, requested_at)
        elif corrected and corrected != original and self.on_corrected:
            logger.info("LLM 修正完成：%r -> %r",
                        original[:30], corrected[:30])
            self.on_corrected(original, corrected, requested_at)
        elif corrected == original:
            logger.debug("LLM 无修正（原文已正确）")
            if self.on_corrected:
                # 无修正也通知调用方（让用户知道 LLM 确实跑了）
                self.on_corrected(original, original, requested_at)
        # corrected 为空串：无回调（与旧实现一致，空结果按静默处理）

    def _call_llm(self, text: str, sentence_count: int = 1) -> str:
        """调用 OpenAI 兼容 /chat/completions 接口，返回修正文本。

        多句批次（sentence_count>1）在用户 prompt 末尾追加逐句逐行要求，
        保证返回行数与句数一致（配合 _correct_worker 的行数校验防丢句）。
        HTTP 细节统一走 postprocess.openai_client（I04 去重），本方法只
        负责修正链路特有的 prompt 组装与参数。
        """
        from .openai_client import chat_completion

        content = self.prompt.format(text=text)
        # 历史上下文（llm.history_context 开关开启时）：把最近的历史记录
        # 以时间正序拼在修正指令前面，仅作语境参考。历史放前缀而不是
        # 后缀——prompt 末尾必须紧贴待修正文本（见 DEFAULT_PROMPT_ZH 注释，
        # 防止小模型产出元话语）；放后面 LLM 会去"修正"历史文本。
        # 与待修文本完全相同的条目剔除（无信息量且易让小模型混淆）。
        if self.history_provider is not None:
            try:
                hist = [h for h in (self.history_provider() or [])
                        if isinstance(h, str) and h.strip() and h != text]
            except Exception:
                logger.debug("获取历史上下文失败", exc_info=True)
                hist = []
            if hist:
                hist.reverse()   # entries 最新在前 -> 时间正序（旧到新）
                content = (
                    "以下是最近的输入历史（按时间先后排列，仅作上下文参考，"
                    "用于理解语境和专有名词，不要修正、改写或输出它们）：\n"
                    + "\n".join(hist) + "\n\n" + content
                )
        if sentence_count > 1:
            content += (
                f"\n\n（以上共 {sentence_count} 句话，请逐句修正后按原句数"
                "每句一行输出；不得合并、省略、增删句子，不加序号。）"
            )
        return chat_completion(
            self.base_url, self.api_key, self.model, content,
            timeout_ms=self.timeout_ms,
            temperature=0.3,                   # 修正任务要稳定不要发散
            max_tokens=len(text) * 3 + 80,     # 修正版不会比原文长太多
            disable_thinking=self.disable_thinking,
        )
