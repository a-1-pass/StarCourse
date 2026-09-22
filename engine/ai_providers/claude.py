"""Anthropic Claude 适配器（Messages API）。

接口文档：https://docs.anthropic.com/en/api/messages
- Endpoint: ``POST {base_url}/v1/messages``
- 认证头：``x-api-key`` + ``anthropic-version``
- 请求体与 OpenAI 协议不同：``system`` 为顶层字段，
  响应文本位于 ``content[*].text``。

调用逻辑独立：本文件不依赖 OpenAI / 文心适配器。
"""
from __future__ import annotations

from .base import AIProviderBase
from .errors import AIResponseError

#: Claude Messages API 版本（固定，避免模型侧升级造成不兼容）
ANTHROPIC_VERSION = "2023-06-01"


class ClaudeProvider(AIProviderBase):
    """Anthropic Claude 系列适配器。"""

    provider_name = "claude"

    DEFAULTS = {
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-20241022",
    }

    def __init__(self, config: dict = None, cache=None):
        merged = {**self.DEFAULTS, **(config or {})}
        super().__init__(merged, cache)

    def is_configured(self) -> bool:
        # Claude Key 不以固定前缀标记，仅校验非空
        return bool(str(self.config.get("api_key", "")).strip())

    def _do_chat(self, system: str, user: str,
                 max_tokens: int, temperature: float) -> str:
        url = str(self.config.get("base_url", "")).rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": str(self.config.get("api_key", "")),
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        # Claude 要求 max_tokens 必须为正整数
        body = {
            "model": self.config.get("model", "claude-3-5-haiku-20241022"),
            "max_tokens": max(1, int(max_tokens)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # temperature 仅在显式非默认时携带（Claude 取值范围 0-1）
        if temperature:
            body["temperature"] = min(1.0, max(0.0, float(temperature)))

        data = self._post_json(url, headers, body)
        try:
            parts = [
                block.get("text", "")
                for block in data["content"]
                if block.get("type") == "text"
            ]
            return "".join(parts)
        except (KeyError, TypeError) as e:
            raise AIResponseError(
                f"claude 响应结构异常，缺少 content[].text: {e}"
            )
