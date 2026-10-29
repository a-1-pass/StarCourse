# _*_ coding:utf-8 _*_
"""AI 答题多数投票工具。

用于 self-consistency 策略：同一道题请求多次（或多模型），取出现频率
最高的答案作为最终结果，平票时返回 None 由调用方兜底。
"""
from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple


def _normalize(answer) -> str:
    """归一化答案：去除空白、多选字母排序，便于 ABC 与 BCA 视为同一。"""
    if not isinstance(answer, str):
        return ""
    s = answer.strip()
    if not s:
        return ""
    # 纯字母串（多选）按字母排序去重
    if all(c.isalpha() for c in s):
        return "".join(sorted(set(s.upper())))
    return s


def majority_vote(answers: List) -> Optional[str]:
    """返回多数答案；平票或无有效答案时返回 None。"""
    normalized = [_normalize(a) for a in answers if a]
    if not normalized:
        return None
    counter = Counter(normalized)
    top = counter.most_common(2)
    if len(top) == 1:
        return top[0][0]
    if top[0][1] > top[1][1]:
        return top[0][0]
    return None  # 平票


def vote_with_confidence(answers: List) -> Tuple[Optional[str], float]:
    """返回 (多数答案, 置信度)。

    置信度 = 最高频答案出现次数 / 有效答案总数。
    平票时答案为 None，置信度 0.0。
    """
    normalized = [_normalize(a) for a in answers if a]
    if not normalized:
        return None, 0.0
    counter = Counter(normalized)
    top = counter.most_common(2)
    if len(top) == 1:
        return top[0][0], 1.0
    if top[0][1] > top[1][1]:
        return top[0][0], top[0][1] / len(normalized)
    return None, 0.0
