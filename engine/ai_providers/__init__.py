"""多模型 AI 答题提供商包。

公开 API
--------
- ``build_provider(name, config)``  构建单个提供商
- ``build_multi_client(config)``   构建按优先级容灾的多模型客户端
- ``load_ai_config()``             加载归一化配置（config.json / config.yml）
- ``MultiAIClient``                容灾客户端
- ``AIProviderBase``               提供商基类（扩展新厂商时继承）
"""
from .ai_config import load_ai_config, normalize_json_config
from .base import AIProviderBase
from .claude import ClaudeProvider
from .errors import (
    AIAuthError,
    AIConfigError,
    AIProviderError,
    AIRateLimitError,
    AIRequestError,
    AIResponseError,
)
from .factory import PROVIDER_REGISTRY, build_multi_client, build_provider
from .multi_client import MultiAIClient
from .openai_compatible import (
    DeepSeekCompatProvider,
    GPTProvider,
    OpenAICompatibleProvider,
)
from .wenxin import WenxinProvider

__all__ = [
    "AIProviderBase",
    "MultiAIClient",
    "OpenAICompatibleProvider",
    "GPTProvider",
    "DeepSeekCompatProvider",
    "ClaudeProvider",
    "WenxinProvider",
    "PROVIDER_REGISTRY",
    "build_provider",
    "build_multi_client",
    "load_ai_config",
    "normalize_json_config",
    "AIProviderError",
    "AIConfigError",
    "AIAuthError",
    "AIRateLimitError",
    "AIRequestError",
    "AIResponseError",
]
