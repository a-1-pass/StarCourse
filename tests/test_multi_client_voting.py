# _*_ coding:utf-8 _*_
"""MultiAIClient 多数投票集成测试。"""
from unittest.mock import MagicMock

import pytest

from engine.ai_providers.multi_client import MultiAIClient


def _make_provider(name, answer=None, configured=True):
    p = MagicMock()
    p.provider_name = name
    p.is_configured.return_value = configured
    p.answer_question.return_value = answer
    p.last_error = None
    return p


def test_vote_uses_majority_across_providers():
    """多个提供商返回不同答案时取多数。"""
    p1 = _make_provider("deepseek", "A")
    p2 = _make_provider("openai", "B")
    p3 = _make_provider("claude", "A")
    client = MultiAIClient([p1, p2, p3], vote_times=1)
    # vote_times=1 表示每个提供商问一次，共 3 次投票
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "A"


def test_vote_single_provider_multiple_times():
    """单个提供商时，vote_times 控制自问次数，取多数。"""
    p1 = MagicMock()
    p1.provider_name = "deepseek"
    p1.is_configured.return_value = True
    # 三次返回 A, B, A → 多数 A
    p1.answer_question.side_effect = ["A", "B", "A"]
    p1.last_error = None
    client = MultiAIClient([p1], vote_times=3)
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "A"
    assert p1.answer_question.call_count == 3


def test_vote_tie_falls_back_to_first():
    """平票时回退到第一个非空答案。"""
    p1 = _make_provider("deepseek", "A")
    p2 = _make_provider("openai", "B")
    client = MultiAIClient([p1, p2], vote_times=1)
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "A"  # 平票回退第一个


def test_vote_disabled_uses_first_result():
    """vote_times=0 时走原容灾链（第一个非空即返回）。"""
    p1 = _make_provider("deepseek", "A")
    p2 = _make_provider("openai", "B")
    client = MultiAIClient([p1, p2], vote_times=0)
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "A"
    # p2 不应被调用
    p2.answer_question.assert_not_called()


def test_vote_skips_unconfigured_providers():
    p1 = _make_provider("deepseek", "A", configured=False)
    p2 = _make_provider("openai", "B")
    client = MultiAIClient([p1, p2], vote_times=1)
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "B"
    p1.answer_question.assert_not_called()


def test_vote_ignores_none_answers():
    """某个提供商返回 None 不参与投票。"""
    p1 = _make_provider("deepseek", "A")
    p2 = _make_provider("openai", None)
    p3 = _make_provider("claude", "A")
    client = MultiAIClient([p1, p2, p3], vote_times=1)
    result = client.answer_question({"question_type": "single", "title": "q"})
    assert result == "A"
