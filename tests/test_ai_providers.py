# _*_ coding:utf-8 _*_
"""ai_providers 多模型 AI 架构测试：

- 配置归一化与提供商工厂
- 各适配器端点构造 / 请求体 / 响应解析
- 统一错误分类、超时控制与重试
- MultiAIClient 优先级容灾
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from ai_providers import (
    AIAuthError,
    AIProviderBase,
    AIProviderError,
    AIRequestError,
    AIResponseError,
    AIRateLimitError,
    ClaudeProvider,
    DeepSeekCompatProvider,
    GPTProvider,
    MultiAIClient,
    OpenAICompatibleProvider,
    WenxinProvider,
    build_multi_client,
    build_provider,
    normalize_json_config,
)


# ─────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────

class FakeCache:
    """避免 CacheDAO 触碰真实文件系统。"""

    def __init__(self):
        self.items = {}

    def get_cache(self, key):
        return self.items.get(key)

    def add_cache(self, key, value):
        self.items[key] = value

    def get_stats(self):
        return {"items": len(self.items)}

    def clear_cache(self):
        self.items.clear()


def make_resp(status=200, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    resp.text = text
    return resp


CHOICES = lambda text: {"choices": [{"message": {"content": text}}]}


# ─────────────────────────────────────────────────────────
# 1. 配置归一化
# ─────────────────────────────────────────────────────────

class TestNormalizeJson:
    def test_flat_structure(self):
        raw = {
            "default_provider": "openai",
            "priority": ["openai", "deepseek"],
            "timeout": 25,
            "max_retries": 2,
            "openai": {"api_key": "sk-x"},
            "deepseek": {"api_key": "sk-y"},
        }
        cfg = normalize_json_config(raw)
        assert cfg["active"] == "openai"
        assert cfg["priority"] == ["openai", "deepseek"]
        assert cfg["timeout"] == 25
        assert cfg["max_retries"] == 2
        assert set(cfg["providers"]) == {"openai", "deepseek"}

    def test_priority_falls_back_to_default(self):
        cfg = normalize_json_config(
            {"default_provider": "claude", "claude": {"api_key": "k"}}
        )
        assert cfg["priority"] == ["claude"]
        assert cfg["timeout"] == 30  # 默认值

    def test_non_dict_meta_ignored(self):
        cfg = normalize_json_config(
            {"default_provider": "wenxin", "wenxin": {"api_key": "a"}}
        )
        assert cfg["active"] == "wenxin"
        assert cfg["providers"]["wenxin"] == {"api_key": "a"}


# ─────────────────────────────────────────────────────────
# 2. 工厂
# ─────────────────────────────────────────────────────────

class TestFactory:
    @pytest.mark.parametrize(
        "name,cls",
        [
            ("deepseek", DeepSeekCompatProvider),
            ("openai", GPTProvider),
            ("gpt", GPTProvider),
            ("claude", ClaudeProvider),
            ("wenxin", WenxinProvider),
            ("ernie", WenxinProvider),
        ],
    )
    def test_registered(self, name, cls):
        p = build_provider(name, {}, cache=FakeCache())
        assert isinstance(p, cls)

    def test_unknown_falls_back_to_openai_compatible(self):
        p = build_provider("mygateway", {"api_key": "sk-1234567890"},
                           cache=FakeCache())
        assert isinstance(p, OpenAICompatibleProvider)
        assert p.provider_name == "mygateway"


# ─────────────────────────────────────────────────────────
# 3. OpenAI 兼容适配器
# ─────────────────────────────────────────────────────────

class TestOpenAICompatible:
    @pytest.mark.parametrize(
        "base,expected",
        [
            ("https://api.openai.com",
             "https://api.openai.com/v1/chat/completions"),
            ("https://api.openai.com/v1",
             "https://api.openai.com/v1/chat/completions"),
        ],
    )
    def test_endpoint_normalization(self, base, expected):
        p = OpenAICompatibleProvider(
            {"base_url": base, "api_key": "sk-1234567890"}, cache=FakeCache()
        )
        assert p._endpoint() == expected

    def test_path_prefix(self):
        p = OpenAICompatibleProvider(
            {"base_url": "https://api.stepfun.com",
             "path_prefix": "/step_plan/v1",
             "api_key": "anything"},
            cache=FakeCache(),
        )
        assert p._endpoint() == (
            "https://api.stepfun.com/step_plan/v1/chat/completions"
        )

    @pytest.mark.parametrize(
        "key,prefix,expected",
        [
            ("", "sk-", False),
            ("sk-1234567890", "sk-", True),
            ("xx-1234567890", "sk-", False),
            ("any-token-123456", "", True),  # 不校验前缀
            ("short", "", False),
        ],
    )
    def test_is_configured(self, key, prefix, expected):
        p = OpenAICompatibleProvider(
            {"api_key": key, "key_prefix": prefix}, cache=FakeCache()
        )
        assert p.is_configured() is expected

    def test_do_chat_success(self):
        p = GPTProvider({"api_key": "sk-1234567890"}, cache=FakeCache())
        p._session.post = MagicMock(
            return_value=make_resp(200, CHOICES("B"))
        )
        assert p._do_chat("sys", "user", 10, 0.0) == "B"
        call = p._session.post.call_args
        assert call.args[0] == "https://api.openai.com/v1/chat/completions"
        kwargs = call.kwargs
        assert kwargs["headers"]["Authorization"] == "Bearer sk-1234567890"
        assert kwargs["json"]["model"] == "gpt-4o-mini"
        assert kwargs["timeout"] == 30

    def test_do_chat_bad_structure_raises(self):
        p = GPTProvider({"api_key": "sk-1234567890"}, cache=FakeCache())
        p._session.post = MagicMock(
            return_value=make_resp(200, {"choices": []})
        )
        with pytest.raises(AIResponseError):
            p._do_chat("sys", "user", 10, 0.0)

    def test_deepseek_defaults(self):
        p = build_provider("deepseek", {}, cache=FakeCache())
        assert p.config["base_url"] == "https://api.deepseek.com"
        assert p.config["model"] == "deepseek-chat"


# ─────────────────────────────────────────────────────────
# 4. 统一错误分类 / 超时 / 重试
# ─────────────────────────────────────────────────────────

class TestPostJsonErrors:
    def _provider(self, **overrides):
        cfg = {"api_key": "sk-1234567890", **overrides}
        return GPTProvider(cfg, cache=FakeCache())

    def test_timeout(self):
        p = self._provider()
        p._session.post = MagicMock(side_effect=requests.Timeout("slow"))
        with pytest.raises(AIRequestError) as ei:
            p._post_json("u", {}, {})
        assert ei.value.timeout is True

    def test_connection_error(self):
        p = self._provider()
        p._session.post = MagicMock(
            side_effect=requests.ConnectionError("reset")
        )
        with pytest.raises(AIRequestError) as ei:
            p._post_json("u", {}, {})
        assert ei.value.retryable is True

    def test_401_auth_error(self):
        p = self._provider()
        p._session.post = MagicMock(
            return_value=make_resp(401, text="unauthorized")
        )
        with pytest.raises(AIAuthError):
            p._post_json("u", {}, {})

    def test_403_auth_error(self):
        p = self._provider()
        p._session.post = MagicMock(return_value=make_resp(403))
        with pytest.raises(AIAuthError):
            p._post_json("u", {}, {})

    def test_429_rate_limit(self):
        p = self._provider()
        p._session.post = MagicMock(return_value=make_resp(429))
        with pytest.raises(AIRateLimitError):
            p._post_json("u", {}, {})

    def test_500_server_error(self):
        p = self._provider()
        p._session.post = MagicMock(return_value=make_resp(503))
        with pytest.raises(AIRequestError) as ei:
            p._post_json("u", {}, {})
        assert ei.value.retryable is True

    def test_400_business_error(self):
        p = self._provider()
        p._session.post = MagicMock(
            return_value=make_resp(400, text="bad request")
        )
        with pytest.raises(AIResponseError):
            p._post_json("u", {}, {})

    def test_retry_then_success(self):
        p = self._provider(max_retries=1)
        p._session.post = MagicMock(
            side_effect=[
                requests.Timeout("slow"),
                make_resp(200, CHOICES("A")),
            ]
        )
        data = p._post_json("u", {}, {})
        assert data["choices"][0]["message"]["content"] == "A"
        assert p._session.post.call_count == 2

    def test_custom_timeout_propagates(self):
        p = self._provider(timeout=15)
        assert p.timeout == 15
        p._session.post = MagicMock(
            return_value=make_resp(200, CHOICES("A"))
        )
        p._post_json("u", {}, {})
        assert p._session.post.call_args.kwargs["timeout"] == 15


# ─────────────────────────────────────────────────────────
# 5. Claude 适配器
# ─────────────────────────────────────────────────────────

class TestClaude:
    def _build(self):
        return ClaudeProvider(
            {"api_key": "ck-abc", "model": "claude-3-5-haiku-20241022"},
            cache=FakeCache(),
        )

    def test_is_configured(self):
        p = ClaudeProvider({}, cache=FakeCache())
        assert p.is_configured() is False
        assert self._build().is_configured() is True

    def test_do_chat(self):
        p = self._build()
        p._session.post = MagicMock(
            return_value=make_resp(
                200,
                {"content": [
                    {"type": "text", "text": "李白"},
                    {"type": "thinking", "text": "ignored"},
                ]},
            )
        )
        assert p._do_chat("sys-prompt", "题目", 300, 0.0) == "李白"
        call = p._session.post.call_args
        assert call.args[0] == "https://api.anthropic.com/v1/messages"
        kwargs = call.kwargs
        assert kwargs["headers"]["x-api-key"] == "ck-abc"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        body = kwargs["json"]
        assert body["system"] == "sys-prompt"
        assert body["max_tokens"] == 300

    def test_temperature_clamped(self):
        p = self._build()
        p._session.post = MagicMock(
            return_value=make_resp(200, {"content": [{"type": "text",
                                                      "text": "x"}]})
        )
        p._do_chat("s", "u", 100, 1.7)
        body = p._session.post.call_args.kwargs["json"]
        assert body["temperature"] == 1.0


# ─────────────────────────────────────────────────────────
# 6. 文心一言适配器
# ─────────────────────────────────────────────────────────

class TestWenxin:
    def _build(self):
        return WenxinProvider(
            {"api_key": "cid", "secret_key": "csecret",
             "model": "ernie-4.0-turbo-8k"},
            cache=FakeCache(),
        )

    def test_is_configured_requires_both(self):
        p = WenxinProvider({"api_key": "cid"}, cache=FakeCache())
        assert p.is_configured() is False
        assert self._build().is_configured() is True

    def test_full_flow(self):
        p = self._build()
        token_resp = make_resp(
            200, {"access_token": "tok123", "expires_in": 2592000}
        )
        chat_resp = make_resp(200, {"result": "答案文本"})
        p._session.post = MagicMock(
            side_effect=[token_resp, chat_resp]
        )
        assert p._do_chat("sys", "题目", 300, 0.0) == "答案文本"

        calls = p._session.post.call_args_list
        assert calls[0].args[0].startswith(
            "https://aip.baidubce.com/oauth/2.0/token"
        )
        assert "client_id=cid" in calls[0].args[0]
        chat_url = calls[1].args[0]
        assert chat_url.startswith(
            "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/"
            "wenxinworkshop/chat/ernie-4.0-turbo-8k"
        )
        assert "access_token=tok123" in chat_url

    def test_token_reused(self):
        p = self._build()
        token_resp = make_resp(
            200, {"access_token": "tok123", "expires_in": 2592000}
        )
        p._session.post = MagicMock(
            side_effect=[token_resp, make_resp(200, {"result": "a"})]
        )
        p._do_chat("s", "q", 100, 0.0)
        # 第二次调用：token 未过期，直接打 chat 接口
        p._session.post = MagicMock(
            return_value=make_resp(200, {"result": "b"})
        )
        p._do_chat("s", "q", 100, 0.0)
        assert p._session.post.call_count == 1
        assert "oauth" not in p._session.post.call_args.args[0]

    def test_token_endpoint_failure_is_auth(self):
        p = self._build()
        p._session.post = MagicMock(
            return_value=make_resp(400, text="invalid client")
        )
        with pytest.raises(AIAuthError):
            p._do_chat("s", "q", 100, 0.0)

    def test_business_error_code_110(self):
        p = self._build()
        p._session.post = MagicMock(
            side_effect=[
                make_resp(200, {"access_token": "t", "expires_in": 2592000}),
                make_resp(200, {"error_code": 110, "error_msg": "token invalid"}),
            ]
        )
        with pytest.raises(AIAuthError):
            p._do_chat("s", "q", 100, 0.0)

    def test_other_business_error(self):
        p = self._build()
        p._session.post = MagicMock(
            side_effect=[
                make_resp(200, {"access_token": "t", "expires_in": 2592000}),
                make_resp(200, {"error_code": 336501, "error_msg": "other"}),
            ]
        )
        with pytest.raises(AIResponseError):
            p._do_chat("s", "q", 100, 0.0)


# ─────────────────────────────────────────────────────────
# 7. MultiAIClient 容灾
# ─────────────────────────────────────────────────────────

class StubProvider:
    def __init__(self, name, configured, result):
        self.provider_name = name
        self._configured = configured
        self._result = result
        self.last_error = None

    def is_configured(self):
        return self._configured

    def answer_question(self, question):
        if isinstance(self._result, type) and issubclass(
            self._result, AIProviderError
        ):
            raise self._result("boom")
        return self._result

    def answer_question_content(self, question):
        return self._result


class TestMultiAIClient:
    def test_skip_unconfigured(self):
        m = MultiAIClient([
            StubProvider("a", False, None),
            StubProvider("b", True, "X"),
        ])
        assert m.is_configured()
        assert m.answer_question({}) == "X"

    def test_first_empty_then_second(self):
        s1 = StubProvider("a", True, None)
        s1.last_error = "timeout"
        m = MultiAIClient([s1, StubProvider("b", True, "Y")])
        assert m.answer_question({}) == "Y"

    def test_first_success_wins(self):
        s2 = StubProvider("b", True, "Z")
        m = MultiAIClient([StubProvider("a", True, "A"), s2])
        assert m.answer_question({}) == "A"

    def test_all_empty_returns_none(self):
        m = MultiAIClient([
            StubProvider("a", True, None),
            StubProvider("b", True, None),
        ])
        assert m.answer_question({}) is None

    def test_none_configured(self):
        m = MultiAIClient([
            StubProvider("a", False, None),
            StubProvider("b", False, None),
        ])
        assert not m.is_configured()
        assert m.answer_question({}) is None

    def test_exception_then_fallback(self):
        m = MultiAIClient([
            StubProvider("a", True, AIRateLimitError),
            StubProvider("b", True, "W"),
        ])
        assert m.answer_question({}) == "W"


# ─────────────────────────────────────────────────────────
# 8. 端到端：build_multi_client
# ─────────────────────────────────────────────────────────

class TestBuildMultiClient:
    CONFIG = {
        "priority": ["deepseek", "openai"],
        "timeout": 20,
        "max_retries": 0,
        "providers": {
            "deepseek": {"api_key": "sk-1234567890"},
            "openai": {"api_key": "sk-1234567890"},
        },
    }

    def test_build(self):
        cache = FakeCache()
        client = build_multi_client(self.CONFIG, cache=cache)
        assert isinstance(client, MultiAIClient)
        assert client.provider_names() == ["deepseek", "openai"]
        assert client.is_configured()
        p0, p1 = client.providers
        assert p0.timeout == 20
        # 缓存共享
        assert p0._cache is cache
        assert p1._cache is cache

    def test_failover_through_real_providers(self):
        client = build_multi_client(self.CONFIG, cache=FakeCache())
        p0, p1 = client.providers
        # 首家返回空内容
        p0._session.post = MagicMock(
            return_value=make_resp(200, CHOICES(""))
        )
        # 第二家正常
        p1._session.post = MagicMock(
            return_value=make_resp(200, CHOICES("B"))
        )
        question = {
            "question_type": "single", "title": "题干",
            "options": ["选项一", "选项二"],
        }
        assert client.answer_question(question) == "B"

    def test_empty_config_returns_none(self):
        assert build_multi_client({}, cache=FakeCache()) is None
        assert build_multi_client({"priority": []}, cache=FakeCache()) is None
