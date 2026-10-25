"""Configurable streaming chat client; credentials are read at request time."""

import json

import requests

from app.config.settings import DEEPSEEK_USE_SYSTEM_PROXY
from app.config.runtime import ConfigError, get_config


class LLMServiceError(RuntimeError):
    """A user-readable model service failure."""


def create_http_session(use_system_proxy=DEEPSEEK_USE_SYSTEM_PROXY):
    """Create an API session; local chat defaults to direct connections."""
    session = requests.Session()
    session.trust_env = use_system_proxy
    return session


_SESSION = create_http_session()


def _api_error(response):
    if response.status_code in (401, 403):
        return "DeepSeek 鉴权失败，请在本机网页的API设置中检查Key（或检查已有.env配置）。"
    if response.status_code == 429:
        return "DeepSeek 请求过于频繁或余额不足，请稍后再试并检查账户额度。"
    # Provider errors can echo submitted content or credentials. Never render
    # the raw response body in the browser or logs.
    return f"DeepSeek API 返回 {response.status_code}，请检查模型配置和服务状态。"

def stream_chat(messages):
    try:
        config = get_config()
    except ConfigError as exc:
        raise LLMServiceError(str(exc)) from None
    if not config["api_key"]:
        raise LLMServiceError("尚未配置API Key，请在本机网页的API设置中填写。")
    _SESSION.trust_env = config["use_system_proxy"]
    try:
        response = _SESSION.post(
            config["url"],
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": config["model"],
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True,
                # Interactive chat needs a final answer within the small output
                # budget, not the provider's default thinking-only response.
                "thinking": {"type": "disabled"},
            },
            stream=True,
            timeout=120,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise LLMServiceError("连接 DeepSeek 超时，请稍后重试。") from exc
    except requests.RequestException as exc:
        proxy_hint = (
            " 当前已启用系统代理，请检查代理是否可用。"
            if config["use_system_proxy"]
            else " 当前使用直连；如网络必须经过代理，请设置 DEEPSEEK_USE_SYSTEM_PROXY=true。"
        )
        raise LLMServiceError(f"无法连接 DeepSeek。{proxy_hint}") from exc

    if response.status_code != 200:
        message = _api_error(response)
        response.close()
        raise LLMServiceError(message)

    answered, reasoning_seen, finish_reason = False, False, None
    try:
        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8", errors="replace")
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices") or []
                        if not choices:  # Some compatible APIs send usage-only chunks.
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        reasoning_seen = reasoning_seen or bool(delta.get("reasoning_content"))
                        finish_reason = choice.get("finish_reason") or finish_reason
                        content = delta.get("content", "")
                        if content:
                            answered = True
                            yield content
                    except json.JSONDecodeError:
                        continue
        if finish_reason == "length":
            raise LLMServiceError("DeepSeek 达到输出长度上限，未保存不完整回答。请缩短问题后手动重试。")
        if not answered:
            message = ("DeepSeek 仅返回思考内容，没有最终回答。请检查聊天请求的思考模式设置。"
                       if reasoning_seen else "DeepSeek 没有返回文字，未保存空回答。")
            raise LLMServiceError(message)
    except requests.RequestException as exc:
        raise LLMServiceError("DeepSeek 返回内容时连接中断，请重试。") from exc
    finally:
        response.close()
