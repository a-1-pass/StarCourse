"""按优先级聚合多个 AI 提供商的容灾客户端。

对外保持与历史单客户端（DeepSeekAI）完全一致的接口契约：
``is_configured`` / ``answer_question`` / ``answer_question_content``
/ ``check_connection``，因此 Work.py / Quiz.py 无需任何改动。

容灾策略：按构造顺序（即配置中的优先级）依次尝试各提供商，
某提供商未配置则跳过；调用返回空或底层失败时自动切换下一个。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .errors import AIProviderError

logger = logging.getLogger("MultiAIClient")


class MultiAIClient:
    """多提供商优先级容灾客户端。"""

    def __init__(self, providers: List):
        self._providers = list(providers)

    @property
    def providers(self) -> List:
        return self._providers

    def provider_names(self) -> List[str]:
        return [p.provider_name for p in self._providers]

    def active_providers(self) -> List:
        return [p for p in self._providers if p.is_configured()]

    def is_configured(self) -> bool:
        return any(p.is_configured() for p in self._providers)

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

    def answer_question(self, question: dict) -> Optional[str]:
        return self._try("answer_question", question)

    def answer_question_content(self, question: dict) -> Optional[List[str]]:
        return self._try("answer_question_content", question)

    def check_connection(self) -> bool:
        """任一已配置提供商连通即视为可用。"""
        for provider in self.active_providers():
            if provider.check_connection():
                return True
        return False
