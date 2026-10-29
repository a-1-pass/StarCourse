# _*_ coding:utf-8 _*_
"""vote_times 配置贯通测试。"""
from unittest.mock import patch

import pytest

from ai_providers.ai_config import normalize_json_config
from ai_providers.factory import build_multi_client
from ai_providers.multi_client import MultiAIClient


def test_normalize_json_config_preserves_vote_times():
    raw = {
        "default_provider": "deepseek",
        "vote_times": 3,
        "deepseek": {"api_key": "k", "model": "m"},
    }
    cfg = normalize_json_config(raw)
    assert cfg.get("vote_times") == 3


def test_normalize_json_config_default_vote_times_zero():
    raw = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "k", "model": "m"},
    }
    cfg = normalize_json_config(raw)
    assert cfg.get("vote_times", 0) == 0


def test_build_multi_client_passes_vote_times():
    config = {
        "active": "deepseek",
        "priority": ["deepseek"],
        "vote_times": 2,
        "providers": {"deepseek": {"api_key": "k", "model": "m"}},
    }
    with patch("ai_providers.factory.CacheDAO"):
        client = build_multi_client(config)
    assert isinstance(client, MultiAIClient)
    assert client._vote_times == 2


def test_build_multi_client_default_vote_times_zero():
    config = {
        "active": "deepseek",
        "priority": ["deepseek"],
        "providers": {"deepseek": {"api_key": "k", "model": "m"}},
    }
    with patch("ai_providers.factory.CacheDAO"):
        client = build_multi_client(config)
    assert client._vote_times == 0
