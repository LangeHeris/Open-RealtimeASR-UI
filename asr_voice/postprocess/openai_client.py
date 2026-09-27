"""OpenAI 兼容 /chat/completions 共享 HTTP 客户端。

llm_corrector（文本修正）与 voice_commands（「帮我」AI 变换）此前各自
手写了一份几乎相同的 urllib 调用（payload 构建、尽力关思考参数组、
headers、urlopen、超时换算），统一收敛到本模块：
- 请求构建 / 关思考参数组 / 异常传播行为与原实现完全一致
- 仅有的调用点差异（temperature、max_tokens 公式、prompt 组装）留在调用方
urllib/json 在函数内延迟导入：与引擎模块同理，冷启动没必要加载网络栈。
"""

from __future__ import annotations

import re

# 思考开启时部分本地部署（Ollama / vLLM 等）会把思考过程以
# <thinking>…</thinking> 块内联进 content 字段，直接粘贴会污染正文，
# 统一在响应侧剔除（关思考时模型本不产出思考块，剥离同样安全）。
_THINK_BLOCK = re.compile(
    r"<\s*(?:think|thinking|thought)\b[^>]*>.*?<\s*/\s*(?:think|thinking|thought)\s*>",
    re.IGNORECASE | re.DOTALL,
)
# max_tokens 截断时思考块没有闭合标签：整段截尾都是思考过程，一并剔除
_THINK_OPEN_TAIL = re.compile(
    r"<\s*(?:think|thinking|thought)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _strip_thinking(text: str) -> str:
    """剔除回复正文中混入的思考块（含标签与内容）。"""
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN_TAIL.sub("", text)
    return text.strip()


def chat_completion(base_url: str, api_key: str, model: str, content: str,
                    timeout_ms: int = 3000, temperature: float = 0.3,
                    max_tokens: int = 1024,
                    disable_thinking: bool = True) -> str:
    """同步调用 OpenAI 兼容 /chat/completions，返回回复文本（strip 后）。

    单条 user message；网络/解析异常原样抛出，由调用方降级处理
    （llm_corrector 保留原文，voice_commands 的 ai_call 由注入线程兜底）。
    """
    import json
    import urllib.request

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        # 尽力关闭思考/推理（各厂商参数不同，未知字段多数平台忽略）：
        # - DeepSeek V4（deepseek-v4-flash/pro，思考默认开）：thinking.type=disabled
        # - vLLM/部分框架：chat_template_kwargs.enable_thinking
        # - 智谱 GLM-Zero/通义 Qwen3：enable_thinking
        # - OpenAI o 系：reasoning_effort 最低档
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        payload["enable_thinking"] = False
        payload["thinking"] = {"type": "disabled"}
        payload["reasoning_effort"] = "minimal"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"].strip()
    # 本地部署（或关不掉的旧推理模型）可能把思考块混进 content，统一剔除
    return _strip_thinking(content)


def check_connectivity(base_url: str, api_key: str, model: str,
                      disable_thinking: bool = True,
                      timeout_ms: int = 10000) -> tuple:
    """连通性检测：最小化 chat 请求，验证 地址/密钥/模型 全链路。

    返回 (ok, message)：ok=True 时 message 为模型回复摘要；
    ok=False 时 message 为可读的错误说明（供设置界面直接展示）。
    """
    try:
        reply = chat_completion(
            base_url, api_key, model, "回复：OK",
            timeout_ms=timeout_ms, temperature=0.0, max_tokens=16,
            disable_thinking=disable_thinking,
        )
        brief = reply if len(reply) <= 40 else reply[:40] + "…"
        return True, brief or "OK"
    except Exception as exc:
        return False, _readable_error(exc)


def _readable_error(exc: Exception) -> str:
    """把底层网络/协议异常翻译成用户可读的提示。"""
    import json
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 401:
            return "密钥无效（401 未授权），请检查 API Key"
        if code == 403:
            return "无访问权限（403），请检查 API Key 与账号配额"
        if code == 404:
            return "接口地址不正确（404），请确认 API 地址以 /v1 结尾"
        if 400 <= code < 500:
            return f"请求被拒绝（HTTP {code}），请检查模型名与参数"
        return f"服务端错误（HTTP {code}），请稍后重试"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            return "连接超时，请检查网络或稍后重试"
        return f"无法连接到服务器，请检查网络与 API 地址（{reason}）"
    if isinstance(exc, TimeoutError):
        return "连接超时，请检查网络或稍后重试"
    if isinstance(exc, json.JSONDecodeError):
        return "服务返回非 JSON 响应，请确认该地址是 OpenAI 兼容接口"
    if isinstance(exc, KeyError):
        return "响应格式异常，请确认该地址是 OpenAI 兼容接口"
    return f"检测失败：{exc}"
