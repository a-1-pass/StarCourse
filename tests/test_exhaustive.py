# _*_ coding:utf-8 _*_
"""穷举引擎 exhaustive.py 的单元测试（纯逻辑，无网络）。"""
import threading
import time

import pytest

from engine.exhaustive import (
    ExhaustiveResult,
    ExhaustiveSolver,
    SolverConfig,
    VarSpec,
    _as_bool,
    _as_float,
    _as_int,
)


# ── 工具函数 ──────────────────────────────────────────────

class FakeSubmitter:
    """记录提交历史，可预设每次提交的 (ok, msg)。"""

    def __init__(self, responses):
        # responses: list of (ok, msg)，按顺序循环使用
        self.responses = list(responses)
        self.history = []
        self.idx = 0

    def __call__(self, answers):
        self.history.append(dict(answers))
        ok, msg = self.responses[min(self.idx, len(self.responses) - 1)]
        self.idx += 1
        return ok, msg


def make_cfg(**overrides) -> SolverConfig:
    base = dict(enable=True, interval=0.0, per_question_max_tries=5,
                max_submissions=30, backoff_on_fail=0.0, max_vars=8, max_combos=200)
    base.update(overrides)
    return SolverConfig(**base)


# ── VarSpec ──────────────────────────────────────────────

def test_var_spec_ordered_candidates_seed_first():
    spec = VarSpec("q1", "single", ["A", "B", "C"])
    assert spec.ordered_candidates("B") == ["B", "A", "C"]


def test_var_spec_ordered_candidates_no_seed():
    spec = VarSpec("q1", "single", ["A", "B"])
    assert spec.ordered_candidates("") == ["A", "B"]


def test_var_spec_alt_candidates_excludes_seed():
    spec = VarSpec("q1", "single", ["A", "B", "C"])
    assert spec.alt_candidates("B") == ["A", "C"]


def test_var_spec_alt_candidates_empty_when_single_candidate():
    spec = VarSpec("q1", "single", ["A"])
    assert spec.alt_candidates("A") == []


# ── 配置解析 ──────────────────────────────────────────────

def test_config_from_dict_parses_kebab_keys():
    cfg = SolverConfig.from_dict({
        "enable": "true",
        "interval": "2.5",
        "per-question-max-tries": "3",
        "max-submissions": "10",
        "backoff-on-fail": "4",
        "max-vars": "5",
        "max-combos": "100",
    })
    assert cfg.enable is True
    assert cfg.interval == 2.5
    assert cfg.per_question_max_tries == 3
    assert cfg.max_submissions == 10
    assert cfg.backoff_on_fail == 4.0
    assert cfg.max_vars == 5
    assert cfg.max_combos == 100


def test_config_from_dict_none_is_defaults():
    cfg = SolverConfig.from_dict(None)
    assert cfg.enable is False
    assert cfg.interval == 3.0


def test_as_bool_various():
    assert _as_bool("true") is True
    assert _as_bool("1") is True
    assert _as_bool("yes") is True
    assert _as_bool("false") is False
    assert _as_bool("0") is False
    assert _as_bool("") is False
    assert _as_bool(True) is True


def test_as_float_fallback():
    assert _as_float("3.14", 0.0) == 3.14
    assert _as_float("abc", 9.9) == 9.9
    assert _as_float(None, 9.9) == 9.9


def test_as_int_fallback():
    assert _as_int("5", 0) == 5
    assert _as_int("xx", 7) == 7


# ── 文案解析 ──────────────────────────────────────────────

def test_score_from_text_percent():
    s = ExhaustiveSolver(lambda a: (True, ""), config=make_cfg())
    assert s._score_from_text("正确率: 60%") == 60.0


def test_score_from_text_score():
    s = ExhaustiveSolver(lambda a: (True, ""), config=make_cfg())
    assert s._score_from_text("得分: 80") == 80.0


def test_score_from_text_ratio():
    s = ExhaustiveSolver(lambda a: (True, ""), config=make_cfg())
    assert s._score_from_text("3/5") == 60.0


def test_score_from_text_none():
    s = ExhaustiveSolver(lambda a: (True, ""), config=make_cfg())
    assert s._score_from_text("没有分数") is None
    assert s._score_from_text("") is None


def test_msg_rejected_detects_keywords():
    s = ExhaustiveSolver(lambda a: (True, ""), config=make_cfg())
    assert s._msg_rejected("正确率过低，请重新作答") is True
    assert s._msg_rejected("提交成功") is False
    assert s._msg_rejected("") is False


# ── solve 主流程 ──────────────────────────────────────────

def test_solve_disabled_submits_once():
    sub = FakeSubmitter([(True, "提交成功")])
    s = ExhaustiveSolver(sub, config=make_cfg(enable=False))
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A", "B"])])
    assert len(sub.history) == 1
    # 首次即通过 → degraded 复位为 False（虽走降级路径，但结果是通过）
    assert result.passed is True
    assert result.degraded is False


def test_solve_no_var_specs_degraded():
    sub = FakeSubmitter([(True, "ok")])
    s = ExhaustiveSolver(sub, config=make_cfg())
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A"])])  # 单候选
    assert len(sub.history) == 1
    assert result.passed is True
    assert result.degraded is False


def test_solve_first_submit_passes_early_stop():
    sub = FakeSubmitter([(True, "提交成功，整卷通过")])
    s = ExhaustiveSolver(sub, config=make_cfg())
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A", "B", "C"])])
    assert len(sub.history) == 1  # 命中即停，不再穷举
    assert result.passed is True
    assert result.attempts == 1


def test_solve_search_space_too_large_degraded():
    sub = FakeSubmitter([(True, "ok")])
    specs = [VarSpec(f"q{i}", "single", ["A", "B", "C", "D"]) for i in range(10)]
    base = {f"q{i}": "A" for i in range(10)}
    s = ExhaustiveSolver(sub, config=make_cfg(max_vars=5))
    result = s.solve(base, specs)
    assert len(sub.history) == 1  # 降级只提交一次
    assert result.passed is True
    assert result.degraded is False


def test_solve_enumeration_finds_answer():
    """无分数信号时按 Hamming 距离枚举，第二次提交（depth=1, 改第一题）命中。"""
    base = {"q1": "A", "q2": "A"}
    specs = [
        VarSpec("q1", "single", ["A", "B"]),
        VarSpec("q2", "single", ["A", "B"]),
    ]
    # 第一次 A/A 失败，第二次 B/A 成功
    sub = FakeSubmitter([
        (False, "打回重做"),
        (True, "提交成功"),
    ])
    s = ExhaustiveSolver(sub, config=make_cfg())
    result = s.solve(base, specs)
    assert result.passed is True
    assert result.answers["q1"] == "B"
    assert result.attempts == 2


def test_solve_hill_climb_keeps_improvement():
    """有分数信号时贪心爬坡：改 q1 分数上升则保留，继续改 q2。"""
    base = {"q1": "A", "q2": "A"}
    specs = [
        VarSpec("q1", "single", ["A", "B"]),
        VarSpec("q2", "single", ["A", "B"]),
    ]
    # A/A=40, B/A=60（保留）, B/B=100（通过）
    sub = FakeSubmitter([
        (False, "正确率: 40%"),
        (False, "正确率: 60%"),
        (True, "正确率: 100%"),
    ])
    s = ExhaustiveSolver(sub, config=make_cfg())
    result = s.solve(base, specs)
    assert result.passed is True
    assert result.answers["q1"] == "B"
    assert result.answers["q2"] == "B"
    assert result.attempts == 3


def test_solve_max_submissions_limit():
    sub = FakeSubmitter([(False, "打回")] * 100)
    s = ExhaustiveSolver(sub, config=make_cfg(max_submissions=3, max_vars=8, max_combos=200))
    specs = [VarSpec(f"q{i}", "single", ["A", "B"]) for i in range(4)]
    base = {f"q{i}": "A" for i in range(4)}
    result = s.solve(base, specs)
    assert result.attempts == 3
    assert result.passed is False


def test_solve_stop_event_interrupts():
    stop = threading.Event()
    stop.set()  # 预先停止
    sub = FakeSubmitter([(True, "ok")])
    s = ExhaustiveSolver(sub, config=make_cfg(), stop_event=stop)
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A", "B"])])
    assert len(sub.history) == 0  # 被停止，不提交
    assert result.passed is False


def test_solve_submit_exception_does_not_crash():
    def boom(answers):
        raise RuntimeError("network down")
    s = ExhaustiveSolver(boom, config=make_cfg())
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A", "B"])])
    # 异常被吞，不崩溃；返回未通过
    assert result.passed is False


def test_solve_uses_check_passed_callback():
    """有 check_passed 回调时以回调结果为准，忽略文案。"""
    calls = {"n": 0}
    def check():
        calls["n"] += 1
        return calls["n"] >= 2  # 第二次判定为通过
    sub = FakeSubmitter([
        (True, "打回重做"),  # 文案说打回
        (True, "打回重做"),
    ])
    s = ExhaustiveSolver(sub, check_passed=check, config=make_cfg())
    result = s.solve({"q1": "A"}, [VarSpec("q1", "single", ["A", "B"])])
    assert result.passed is True
    assert result.attempts == 2
