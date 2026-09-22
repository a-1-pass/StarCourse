"""AI 提供商统一异常体系。

所有适配器在请求各厂商接口时，统一抛出本模块中的异常类型，
便于上层（MultiAIClient / 调用方）按错误性质决定重试或切换。
"""
from __future__ import annotations


class AIProviderError(Exception):
    """AI 提供商异常基类。"""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class AIConfigError(AIProviderError):
    """配置缺失或配置不合法（如未填写 API Key）。"""


class AIAuthError(AIProviderError):
    """认证/授权失败（HTTP 401/403，或厂商返回凭证错误码）。一般不可重试。"""


class AIRateLimitError(AIProviderError):
    """触发限流（HTTP 429）。可重试 / 可切换下一个提供商。"""

    def __init__(self, message: str):
        super().__init__(message, retryable=True)


class AIRequestError(AIProviderError):
    """网络层错误：超时、连接失败、服务端 5xx。"""

    def __init__(self, message: str, *, retryable: bool = True, timeout: bool = False):
        super().__init__(message, retryable=retryable)
        self.timeout = timeout


class AIResponseError(AIProviderError):
    """响应不可解析或结构不符合预期（非 2xx 的业务错误、JSON 结构缺失等）。"""
