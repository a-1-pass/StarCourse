# _*_ coding:utf-8 _*_
"""客观题答案穷举求解引擎（纯逻辑，无网络依赖）。

背景
----
部分学习通任务点存在「正确率过低则打回重做」机制，一次作答（AI/题库）无法保证
整卷通过。本模块对 **单选/判断题** 的候选答案做有界穷举：

1. 命中即停：一旦判定整卷通过立即结束，不再发多余提交；
2. 控频：每次提交前等待可配置间隔，被平台打回后额外退避，并受最大提交数约束；
3. 定界：题数/组合数超过上限时直接放弃穷举，只提交一次现有答案。

设计要点
--------
- 本模块**不含任何 HTTP**：提交与判定由调用方以回调注入
  （``submit_once(answers) -> (ok, msg)`` / ``check_passed() -> bool``），
  便于单元测试，并复用于 Work / Quiz 两条答题链路。
- 平台反馈往往只有「整卷通过 / 被打回」（无分数、无逐题对错），因此：
  * 若被打回文案里能解析出正确率 / 得分 / 正确题数，则用**贪心爬坡**（更省请求）；
  * 否则按**改动数（Hamming 距离）递增**枚举候选向量。
"""
from __future__ import annotations

import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

try:  # 引擎侧统一使用 loguru；测试环境缺失时退回标准库
    from loguru import logger as _default_logger
except Exception:  # pragma: no cover - 仅测试环境兜底
    _default_logger = logging.getLogger("Exhaustive")


# 平台「打回」文案关键词（用于日志、退避，以及无判定回调时的机会性判断）
_REJECT_WORDS = (
    "打回", "正确率过低", "重新作答", "重新提交", "重做", "不及格", "未通过",
)

# 机会性分数信号：仅当被拒文案里带数字时才启用爬坡
_RE_PERCENT = re.compile(r"正确率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%")
_RE_SCORE = re.compile(r"得分\s*[:：]?\s*(\d+(?:\.\d+)?)")
_RE_RATIO = re.compile(r"(\d+)\s*/\s*(\d+)")


@dataclass
class VarSpec:
    """参与穷举的一道题（调用方需保证只传入单选/判断题）。"""

    question_id: str
    q_type: str = ""
    candidates: List[str] = field(default_factory=list)

    def ordered_candidates(self, seed: str) -> List[str]:
        """尝试顺序：现有答案（种子）优先，其余按给定顺序去重。"""
        out: List[str] = []
        if seed:
            out.append(seed)
        for cand in self.candidates:
            if cand not in out:
                out.append(cand)
        return out

    def alt_candidates(self, seed: str) -> List[str]:
        """除种子之外的候选（枚举阶段使用）。"""
        return [c for c in self.ordered_candidates(seed) if c != seed]


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class SolverConfig:
    """穷举参数（默认值即 engine/config.yml 中 FunConfig.deal-mission.exhaustive 的默认值）。"""

    enable: bool = False
    interval: float = 3.0
    per_question_max_tries: int = 5
    max_submissions: int = 30
    backoff_on_fail: float = 5.0
    max_vars: int = 8
    max_combos: int = 200

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "SolverConfig":
        data = data or {}
        return cls(
            enable=_as_bool(data.get("enable", False)),
            interval=_as_float(data.get("interval", 3.0), 3.0),
            per_question_max_tries=_as_int(data.get("per-question-max-tries", 5), 5),
            max_submissions=_as_int(data.get("max-submissions", 30), 30),
            backoff_on_fail=_as_float(data.get("backoff-on-fail", 5.0), 5.0),
            max_vars=_as_int(data.get("max-vars", 8), 8),
            max_combos=_as_int(data.get("max-combos", 200), 200),
        )


@dataclass
class ExhaustiveResult:
    """穷举结果：``passed`` 为整卷是否通过；``degraded`` 表示未真正穷举（只提交了一次）。"""

    passed: bool = False
    answers: Dict[str, str] = field(default_factory=dict)
    attempts: int = 0
    degraded: bool = False
    reason: str = ""


class ExhaustiveSolver:
    """有界穷举求解器：只依赖注入的回调，可在测试中完全离线运行。"""

    def __init__(
        self,
        submit_once: Callable[[Dict[str, str]], "tuple[bool, str]"],
        check_passed: Optional[Callable[[], bool]] = None,
        config: Optional[SolverConfig] = None,
        stop_event=None,
        logger=None,
    ):
        self.submit_once = submit_once
        self.check_passed = check_passed
        self.config = config or SolverConfig()
        self.stop_event = stop_event
        self.log = logger or _default_logger

    # ── 对外入口 ──────────────────────────────────────────

    def solve(self, base_answers: Dict[str, str],
              var_specs: List[VarSpec]) -> ExhaustiveResult:
        base = dict(base_answers or {})
        result = ExhaustiveResult(answers=dict(base))
        cfg = self.config

        if not cfg.enable:
            result.degraded = True
            result.reason = "穷举未启用（exhaustive.enable=false）"
            return self._submit_seed(result, base)

        specs = [
            s for s in (var_specs or [])
            if s.question_id in base and len(set(s.candidates)) >= 2
        ]
        if not specs:
            result.degraded = True
            result.reason = "无可穷举的单选/判断题"
            return self._submit_seed(result, base)

        combos = 1
        for spec in specs:
            combos *= len(set(spec.candidates))
        if len(specs) > cfg.max_vars or combos > cfg.max_combos:
            result.degraded = True
            result.reason = (
                f"搜索空间过大（题数 {len(specs)}/{cfg.max_vars}，"
                f"组合 {combos}/{cfg.max_combos}）"
            )
            return self._submit_seed(result, base)

        current = dict(base)
        passed, score = self._attempt(result, current)
        if passed:
            result.reason = result.reason or "首次提交即通过（早停）"
            return result

        if score is not None:
            self.log.info(f"[穷举] 平台反馈可解析分数={score:g}，改用贪心爬坡")
            self._hill_climb(result, current, specs, score)
        else:
            self._enumerate(result, current, specs)

        if result.passed:
            return result

        result.degraded = True
        result.answers = dict(base)
        if not result.reason:
            result.reason = "穷举未能使整卷通过，保留现有答案"
        self.log.warning(f"[穷举] 结束：{result.reason}（共提交 {result.attempts} 次）")
        return result

    # ── 内部：提交与判定 ──────────────────────────────────

    def _submit_seed(self, result: ExhaustiveResult,
                     base: Dict[str, str]) -> ExhaustiveResult:
        """降级路径：只提交一次现有答案。"""
        result.answers = dict(base)
        passed, _ = self._attempt(result, base)
        result.passed = passed
        if passed:
            result.degraded = False
            result.reason = "现有答案提交即通过"
        else:
            result.reason = result.reason or "提交现有答案未通过"
        self.log.info(f"[穷举] 降级提交一次：{result.reason}")
        return result

    def _attempt(self, result: ExhaustiveResult,
                 answers: Dict[str, str]) -> "tuple[bool, Optional[float]]":
        """提交一个候选向量；返回 (是否通过, 可解析分数或 None)。"""
        if self._stopped():
            result.reason = "用户停止"
            return False, None
        if result.attempts >= self.config.max_submissions:
            result.reason = f"达到最大提交次数 {self.config.max_submissions}"
            return False, None
        if not self._sleep(self.config.interval):
            result.reason = "等待提交间隔时被用户停止"
            return False, None

        try:
            ok, msg = self.submit_once(dict(answers))
        except Exception as e:  # 提交异常不应中断整条任务链
            self.log.warning(f"[穷举] 提交异常: {e}")
            return False, None

        msg = str(msg or "")
        result.attempts += 1
        self.log.info(
            f"[穷举] 第 {result.attempts} 次提交 {answers} → "
            f"{'请求成功' if ok else '请求失败'}{(' ' + msg[:120]) if msg else ''}"
        )

        score = self._score_from_text(msg)
        if self._msg_rejected(msg):
            self.log.info(f"[穷举] 平台未通过：{msg[:120]}")
            if not self._sleep(self.config.backoff_on_fail):
                result.reason = "退避等待时被用户停止"
                return False, score

        passed = self._is_passed(msg)
        if passed:
            result.passed = True
            result.answers = dict(answers)
            result.reason = f"第 {result.attempts} 次提交通过（命中即停）"
            self.log.info(f"[穷举] {result.reason}")
        return passed, score

    def _is_passed(self, msg: str) -> bool:
        """优先用调用方的判定回调；无回调时退化为文案+分数判断。

        判定规则（无回调时）：
        - 含「打回」类关键词 → 未通过；
        - 可解析出分数且分数 < 100 → 未通过（平台上报分数即已评分，
          满分才算通过；低于满分说明还有提升空间，继续穷举）；
        - 其余情况视为通过（best-effort）。
        """
        if self.check_passed is not None:
            try:
                return bool(self.check_passed())
            except Exception as e:
                self.log.warning(f"[穷举] 通过判定回调异常: {e}")
                return False
        if self._msg_rejected(msg):
            return False
        score = self._score_from_text(msg)
        if score is not None and score < 100.0:
            return False
        return True

    def _sleep(self, seconds: float) -> bool:
        """可中断等待；返回 False 表示等待期间被停止。"""
        seconds = _as_float(seconds, 0.0)
        if self.stop_event is not None:
            if seconds <= 0:
                return not self.stop_event.is_set()
            return not self.stop_event.wait(seconds)
        if seconds > 0:
            time.sleep(seconds)
        return True

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    # ── 内部：两种搜索策略 ────────────────────────────────

    def _hill_climb(self, result: ExhaustiveResult, current: Dict[str, str],
                    specs: List[VarSpec], seed_score: float) -> None:
        """有分数信号时的贪心爬坡：逐题换候选，只保留让分数上升的改动。"""
        best = seed_score
        for spec in specs:
            tries = 0
            for cand in spec.alt_candidates(current.get(spec.question_id, "")):
                if result.passed or self._stopped():
                    return
                if result.attempts >= self.config.max_submissions:
                    result.reason = f"达到最大提交次数 {self.config.max_submissions}"
                    return
                if tries >= self.config.per_question_max_tries:
                    break
                trial = dict(current)
                trial[spec.question_id] = cand
                passed, score = self._attempt(result, trial)
                tries += 1
                if passed:
                    return
                if score is not None and score > best:
                    self.log.info(
                        f"[穷举] 保留改动 {spec.question_id}={cand}（分数 {best:g}→{score:g}）"
                    )
                    best = score
                    current[spec.question_id] = cand
                    break

    def _enumerate(self, result: ExhaustiveResult, current: Dict[str, str],
                   specs: List[VarSpec]) -> None:
        """无分数信号时：按改动数（Hamming 距离）由小到大枚举候选向量。"""
        base = dict(current)
        for depth in range(1, len(specs) + 1):
            for combo in itertools.combinations(specs, depth):
                value_lists = [s.alt_candidates(base.get(s.question_id, "")) for s in combo]
                if any(not values for values in value_lists):
                    continue
                for values in itertools.product(*value_lists):
                    if result.passed or self._stopped():
                        return
                    if result.attempts >= self.config.max_submissions:
                        result.reason = f"达到最大提交次数 {self.config.max_submissions}"
                        return
                    trial = dict(base)
                    for spec, value in zip(combo, values):
                        trial[spec.question_id] = value
                    passed, _ = self._attempt(result, trial)
                    if passed:
                        return
        result.reason = result.reason or "候选组合已枚举完（未通过）"

    # ── 内部：文案解析 ────────────────────────────────────

    def _score_from_text(self, msg: str) -> Optional[float]:
        """从被打回文案中解析可比分数（越高越好）；解析不出返回 None。"""
        if not msg:
            return None
        match = _RE_PERCENT.search(msg)
        if match:
            return _as_float(match.group(1), None)
        match = _RE_SCORE.search(msg)
        if match:
            return _as_float(match.group(1), None)
        match = _RE_RATIO.search(msg)
        if match:
            total = _as_float(match.group(2), 0.0)
            if total > 0:
                return _as_float(match.group(1), 0.0) / total * 100.0
        return None

    def _msg_rejected(self, msg: str) -> bool:
        if not msg:
            return False
        return any(word in msg for word in _REJECT_WORDS)
