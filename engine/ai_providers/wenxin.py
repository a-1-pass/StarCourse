"""百度文心一言（ERNIE）适配器。

接口文档：https://cloud.baidu.com/doc/WENXINWORKSHOP/index.html
调用流程与其他厂商不同，本模块独立封装：
1. 使用 ``api_key``（CLIENNTClient ID）+ ``secret_key`` 通过 OAuth 2.0
   换取 ``access_token``，本地缓存至过期前 60 秒；
2. 调用
   ``POST /rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}?access_token=...``
3. 成功文本位于 ``result``；失败时返回 ``error_code`` / ``error_msg``。

常用模型：ernie-4.0-turbo-8k、ernie-4.0-8k-latest、ernie-3.5-8k、
ernie-speed-pro-128k、ernie-lite-8k 等（模型名直接作为 URL 路径段）。
"""
from __future__ import annotations

import time

import requests

from .base import AIProviderBase
from .errors import AIAuthError, AIRequestError, AIResponseError


class WenxinProvider(AIProviderBase):
    """百度文心一言适配器。"""

    provider_name = "wenxin"

    DEFAULTS = {
        "base_url": "https://aip.baidubce.com",
        "model": "ernie-4.0-turbo-8k",
    }

    def __init__(self, config: dict = None, cache=None):
        merged = {**self.DEFAULTS, **(config or {})}
        super().__init__(merged, cache)
        self._access_token: str = ""
        self._token_expire: float = 0.0

    def is_configured(self) -> bool:
        # 文心需要双凭证
        return bool(str(self.config.get("api_key", "")).strip()) and bool(
            str(self.config.get("secret_key", "")).strip()
        )

    def _get_access_token(self) -> str:
        """获取（或复用缓存的）OAuth access_token。"""
        if self._access_token and time.time() < self._token_expire - 60:
            return self._access_token

        base = str(self.config.get("base_url", "")).rstrip("/")
        url = (
            f"{base}/oauth/2.0/token?grant_type=client_credentials"
            f"&client_id={self.config.get('api_key', '')}"
            f"&client_secret={self.config.get('secret_key', '')}"
        )
        try:
            resp = self._session.post(url, timeout=self.timeout)
        except requests.Timeout as e:
            raise AIRequestError("wenxin 获取 token 超时", timeout=True)
        except requests.ConnectionError as e:
            raise AIRequestError(f"wenxin 获取 token 连接失败: {e}")
        except requests.RequestException as e:
            raise AIRequestError(f"wenxin 获取 token 失败: {e}")

        if resp.status_code != 200:
            raise AIAuthError(
                f"wenxin 凭证认证失败(HTTP {resp.status_code}): {resp.text[:200]}"
            )
        try:
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expire = time.time() + int(data.get("expires_in", 2592000))
        except (ValueError, KeyError) as e:
            raise AIResponseError(f"wenxin token 响应结构异常: {e}")
        return self._access_token

    def _do_chat(self, system: str, user: str,
                 max_tokens: int, temperature: float) -> str:
        token = self._get_access_token()
        base = str(self.config.get("base_url", "")).rstrip("/")
        model = str(self.config.get("model", "ernie-4.0-turbo-8k"))
        url = (
            f"{base}/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}"
            f"?access_token={token}"
        )
        # 文心没有独立 system 参数，将系统要求拼入用户消息（用换行分隔）
        content = f"{system}\n{user}" if system else user
        body = {
            "messages": [{"role": "user", "content": content}],
            "max_output_tokens": max(1, int(max_tokens)),
        }
        # 文心 temperature 范围 0.1-1.0，默认 0.95
        body["temperature"] = (
            min(1.0, max(0.1, float(temperature))) if temperature else 0.95
        )

        data = self._post_json(url, {"Content-Type": "application/json"}, body)

        # 文心业务错误可能以 200 + error_code 形式返回
        error_code = data.get("error_code")
        if error_code:
            error_msg = str(data.get("error_msg", ""))
            # 110/111: access_token 失效；17/18/19: 权限/额度类认证问题
            if error_code in (110, 111, 17, 18, 19):
                raise AIAuthError(f"wenxin 认证/权限错误 {error_code}: {error_msg}")
            raise AIResponseError(f"wenxin 业务错误 {error_code}: {error_msg}")

        return str(data.get("result", ""))
