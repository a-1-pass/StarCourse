"""AI 提供商注册表与构建工厂。"""
from __future__ import annotations

from cache_dao import CacheDAO

from .base import AIProviderBase
from .claude import ClaudeProvider
from .multi_client import MultiAIClient
from .openai_compatible import (
    DeepSeekCompatProvider,
    GPTProvider,
    OpenAICompatibleProvider,
)
from .wenxin import WenxinProvider

#: 已注册提供商名称 -> 适配器类
PROVIDER_REGISTRY = {
    "deepseek": DeepSeekCompatProvider,
    "openai": GPTProvider,
    "gpt": GPTProvider,
    "claude": ClaudeProvider,
    "wenxin": WenxinProvider,
    "ernie": WenxinProvider,
}


def build_provider(name: str, config: dict = None, cache=None) -> AIProviderBase:
    """按名称构建单个提供商。

    未注册名称（如 stepfun、自建网关）统一按 OpenAI 兼容协议处理，
    由配置中的 ``base_url`` / ``path_prefix`` 决定实际端点。
    """
    cls = PROVIDER_REGISTRY.get(str(name))
    if cls is None:
        config = {"provider_name": str(name), **(config or {})}
        cls = OpenAICompatibleProvider
    return cls(config or {}, cache)


def build_multi_client(config: dict = None, cache: CacheDAO = None):
    """基于配置构建优先级容灾客户端；无有效配置时返回 None。"""
    if config is None:
        from .ai_config import load_ai_config
        config = load_ai_config()
    if not config:
        return None

    cache = cache if cache is not None else CacheDAO()
    names = config.get("priority") or [config.get("active")]
    names = [str(n) for n in (names or []) if n]
    if not names:
        return None

    common = {
        key: config[key] for key in ("timeout", "max_retries") if key in config
    }
    providers = []
    provider_cfgs = config.get("providers", {})
    for name in names:
        pcfg = {**common, **provider_cfgs.get(name, {})}
        providers.append(build_provider(name, pcfg, cache))
    return MultiAIClient(providers)
