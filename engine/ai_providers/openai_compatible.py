"""OpenAI Chat Completions 协议适配器。

适用范围
--------
- OpenAI 官方 GPT 系列（gpt-4o / gpt-4o-mini / gpt-3.5-turbo 等）
- 任何兼容 OpenAI 协议的服务：DeepSeek、Moonshot、SiliconFlow、
  自建网关 / 代理等，只需调整 ``base_url``。

各厂商调用逻辑相互独立：本文件不依赖 Claude / 文心适配器。
"""
from __future__ import annotations

from typing import Optional

from .base import AIProviderBase
from .errors import AIResponseError


class OpenAICompatibleProvider(AIProviderBase):
    """通用 OpenAI 协议适配器。"""

    provider_name = "openai_compatible"

    #: 厂商默认值，子类可覆盖；用户配置优先级更高
    DEFAULTS: dict = {
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini",
        "key_prefix": "sk-",
    }

    def __init__(self, config: dict = None, cache=None):
        merged = {**self.DEFAULTS, **(config or {})}
        super().__init__(merged, cache)
        # 允许通过配置显式指定提供商标识（如自建网关）
        name = self.config.get("provider_name")
        if name:
            self.provider_name = str(name)

    def is_configured(self) -> bool:
        key = str(self.config.get("api_key", "")).strip()
        if not key or len(key) < 10:
            return False
        prefix = str(self.config.get("key_prefix", "") or "")
        # key_prefix 为空表示不校验前缀（部分自建网关 Key 形态不固定）
        return not prefix or key.startswith(prefix)

    def _endpoint(self) -> str:
        base = str(self.config.get("base_url", "")).rstrip("/")
        # 非标准路径前缀（如阶跃 StepFun 的 /step_plan/v1）
        path_prefix = str(self.config.get("path_prefix", "") or "").rstrip("/")
        if path_prefix:
            return base + path_prefix + "/chat/completions"
        # 兼容用户填写 https://api.openai.com 或 .../v1 两种形式
        if not base.endswith("/v1"):
            base += "/v1"
        return base + "/chat/completions"

    def _do_chat(self, system: str, user: str,
                 max_tokens: int, temperature: float) -> str:
        headers = {
            "Authorization": f"Bearer {self.config.get('api_key', '')}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.config.get("model", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        data = self._post_json(self._endpoint(), headers, body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIResponseError(
                f"{self.provider_name} 响应结构异常，缺少 choices/message/content: {e}"
            )


class GPTProvider(OpenAICompatibleProvider):
    """OpenAI 官方 GPT 系列。"""

    provider_name = "openai"
    DEFAULTS = {
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini",
        "key_prefix": "sk-",
    }


class DeepSeekCompatProvider(OpenAICompatibleProvider):
    """DeepSeek（通过统一架构接入，与历史 DeepSeekAI 行为一致）。"""

    provider_name = "deepseek"
    DEFAULTS = {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_prefix": "sk-",
    }
