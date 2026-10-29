# _*_ coding:utf-8 _*_
"""AI 答题多数投票机制的单元测试。"""
import pytest

from engine.ai_providers.voting import majority_vote, vote_with_confidence


# ── 基础多数投票 ──────────────────────────────────────────

def test_majority_vote_unanimous():
    assert majority_vote(["A", "A", "A"]) == "A"


def test_majority_vote_simple_majority():
    assert majority_vote(["A", "B", "A"]) == "A"


def test_majority_vote_none_when_tie():
    # 平票无明确多数 → None（让调用方决定兜底）
    assert majority_vote(["A", "B"]) is None


def test_majority_vote_three_way_tie():
    assert majority_vote(["A", "B", "C"]) is None


def test_majority_vote_ignores_none():
    assert majority_vote(["A", None, "A"]) == "A"


def test_majority_vote_all_none_returns_none():
    assert majority_vote([None, None]) is None


def test_majority_vote_empty_returns_none():
    assert majority_vote([]) is None


def test_majority_vote_single():
    assert majority_vote(["B"]) == "B"


# ── 带置信度 ──────────────────────────────────────────────

def test_vote_confidence_unanimous_is_one():
    answer, conf = vote_with_confidence(["A", "A", "A"])
    assert answer == "A"
    assert conf == 1.0


def test_vote_confidence_two_thirds():
    answer, conf = vote_with_confidence(["A", "A", "B"])
    assert answer == "A"
    assert abs(conf - 2 / 3) < 1e-9


def test_vote_confidence_tie_is_zero():
    answer, conf = vote_with_confidence(["A", "B"])
    assert answer is None
    assert conf == 0.0


def test_vote_confidence_all_none():
    answer, conf = vote_with_confidence([None, None])
    assert answer is None
    assert conf == 0.0


# ── 多选项答案归一化（多选题字母顺序无关） ───────────────

def test_majority_vote_multiple_choice_normalized():
    # "ABC" 与 "BCA" 应视为同一答案
    assert majority_vote(["ABC", "BCA", "ABC"]) == "ABC"


def test_majority_vote_multiple_choice_tie_normalized():
    # "AB" vs "BA" 视为同一，非平票
    assert majority_vote(["AB", "BA"]) == "AB"
