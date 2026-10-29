"""按优先级聚合多个 AI 提供商的容灾客户端。

对外保持与历史单客户端（DeepSeekAI）完全一致的接口契约：
``is_configured`` / ``answer_question`` / ``answer_question_content``
/ ``check_connection``，因此 Work.py / Quiz.py 无需任何改动。

容灾策略：按构造顺序（即配置中的优先级）依次尝试各提供商，
某提供商未配置则跳过；调用返回空或底层失败时自动切换下一个。

可选多数投票（self-consistency）：当 ``vote_times > 0`` 时，
对每个已配置提供商重复调用 ``vote_times`` 次，收集所有非空答案后
取多数；平票时回退到第一个非空答案。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .errors import AIProviderError
from .voting import majority_vote

logger = logging.getLogger("MultiAIClient")


class MultiAIClient:
    """多提供商优先级容灾客户端。"""

    def __init__(self, providers: List, vote_times: int = 0):
        self._providers = list(providers)
        self._vote_times = max(0, int(vote_times))

    @property
    def providers(self) -> List:
        return self._providers

    def provider_names(self) -> List[str]:
        return [p.provider_name for p in self._providers]

    def active_providers(self) -> List:
        return [p for p in self._providers if p.is_configured()]

    def is_configured(self) -> bool:
        return any(p.is_configured() for p in self._providers)

    # ── 容灾链（投票关闭时使用） ──────────────────────────

    def _try(self, method: str, question: dict):
        seen_error = False
        for provider in self._providers:
            if not provider.is_configured():
                continue
            try:
                result = getattr(provider, method)(question)
            except AIProviderError as e:
                # 基类通常已收敛为 None，此处为防御性兜底
                seen_error = True
                logger.info(
                    f"[MultiAI] {provider.provider_name} 异常，切换下一个: {e}"
                )
                continue
            if result:
                return result
            if provider.last_error:
                seen_error = True
                logger.info(
                    f"[MultiAI] {provider.provider_name} 无结果"
                    f"（{provider.last_error}），切换下一个"
                )
            else:
                logger.debug(
                    f"[MultiAI] {provider.provider_name} 返回空，切换下一个"
                )
        if seen_error:
            logger.warning("[MultiAI] 已尝试所有可用提供商，均未取得答案")
        return None

    # ── 多数投票（vote_times > 0 时使用） ─────────────────

    def _vote(self, method: str, question: dict):
        answers: List = []
        first_answer = None
        for provider in self._providers:
            if not provider.is_configured():
                continue
            for _ in range(self._vote_times):
                try:
                    result = getattr(provider, method)(question)
                except AIProviderError as e:
                    logger.info(
                        f"[MultiAI] {provider.provider_name} 投票调用异常: {e}"
                    )
                    continue
                if result:
                    answers.append(result)
                    if first_answer is None:
                        first_answer = result
        winner = majority_vote(answers)
        if winner is not None:
            logger.debug(
                f"[MultiAI] 投票结果: {winner} "
                f"（共 {len(answers)} 个有效答案）"
            )
            return winner
        if first_answer is not None:
            logger.debug("[MultiAI] 投票平票，回退到第一个非空答案")
            return first_answer
        logger.warning("[MultiAI] 投票未取得任何有效答案")
        return None

    def answer_question(self, question: dict) -> Optional[str]:
        if self._vote_times > 0:
            return self._vote("answer_question", question)
        return self._try("answer_question", question)

    def answer_question_content(self, question: dict) -> Optional[List[str]]:
        # 内容接口答案较长，投票意义不大，保持容灾链
        return self._try("answer_question_content", question)

    def check_connection(self) -> bool:
        """任一已配置提供商连通即视为可用。"""
        for provider in self.active_providers():
            if provider.check_connection():
                return True
        return False
