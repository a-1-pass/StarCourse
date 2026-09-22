"""AI 答题统一提供商基类。

设计要点
--------
1. 各厂商适配器只需实现两个方法：
   - ``is_configured()``      判断该提供商是否已完成必要配置
   - ``_do_chat(...)``        发送一次对话补全并返回模型文本
2. 答题分派、Prompt 构造、答案清洗、JSON 解析、缓存、请求间隔等
   通用能力全部在本基类实现，保证各厂商答题行为一致。
3. HTTP 请求统一走 ``_post_json``，自动完成错误分类与重试，
   抛出 :mod:`ai_providers.errors` 中的标准异常。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import requests

from cache_dao import CacheDAO
from non_choice import build_subjective_system_prompt, parse_answer_response

from .errors import (
    AIAuthError,
    AIProviderError,
    AIRateLimitError,
    AIRequestError,
    AIResponseError,
)

logger = logging.getLogger("AIProvider")

# 题型名（内部标识 / 中文名均可）-> 规范中文名
TYPE_MAP = {
    "single": "单选题",
    "单选题": "单选题",
    "multiple": "多选题",
    "多选题": "多选题",
    "judgement": "判断题",
    "判断题": "判断题",
    "completion": "填空题",
    "填空题": "填空题",
    "shortanswer": "简答题",
    "简答题": "简答题",
    "term_explanation": "名词解释",
    "名词解释": "名词解释",
    "essay": "论述题",
    "论述题": "论述题",
    "calculation": "计算题",
    "计算题": "计算题",
    "case_analysis": "案例分析题",
    "案例分析题": "案例分析题",
}

SYSTEM_PROMPT_LETTER = """你是答题助手。请仔细阅读题目和选项，选择正确答案。
输出规则：
- 单选题：只输出一个大写字母，如 A
- 多选题：输出所有正确选项的大写字母，如 ABC，按字母顺序排列
- 判断题：正确输出 A，错误输出 B
- 不要输出任何其他文字、解释或标点
- 直接输出字母即可"""

SYSTEM_PROMPT_CONTENT = """请根据题目和选项选择正确答案，以JSON格式输出选项的具体内容。
示例回答：{"Answer": ["正确选项内容"]}。
除此之外不要输出任何多余的内容，也不要使用MD语法。"""

SYSTEM_PROMPT_COMPLETION = """本题为填空题，请根据题目直接填写答案内容，以JSON格式输出。
示例回答：{"Answer": ["答案内容"]}。
除此之外不要输出任何多余的内容，也不要使用MD语法。"""

SYSTEM_PROMPT_SHORTANSWER = """本题为简答题，请根据题目简要回答，以JSON格式输出答案内容。
示例回答：{"Answer": ["答案内容"]}。
除此之外不要输出任何多余的内容，也不要使用MD语法。"""


class AIProviderBase(ABC):
    """所有 AI 答题提供商的抽象基类。"""

    #: 提供商标识，子类覆盖
    provider_name: str = "base"

    def __init__(self, config: dict = None, cache: CacheDAO = None):
        self.config = dict(config or {})
        # 统一超时与重试（可被各提供商配置覆盖）
        self.timeout = float(self.config.get("timeout", 30))
        self.max_retries = int(self.config.get("max_retries", 0))
        self._session = requests.Session()
        self._cache = cache if cache is not None else CacheDAO()
        self._last_request_time: Optional[float] = None
        self._lock = threading.Lock()
        #: 最近一次底层调用错误（供 MultiAIClient 切换时记录原因）
        self.last_error: Optional[str] = None

    # ─────────────────────────────────────────────────────
    # 子类契约
    # ─────────────────────────────────────────────────────

    @abstractmethod
    def is_configured(self) -> bool:
        """是否已完成必要配置（Key 等）。"""

    @abstractmethod
    def _do_chat(self, system: str, user: str,
                 max_tokens: int, temperature: float) -> str:
        """发送一次对话补全，返回模型输出文本。失败抛出 AIProviderError 子类。"""

    # ─────────────────────────────────────────────────────
    # 统一 HTTP：错误分类 + 重试
    # ─────────────────────────────────────────────────────

    def _post_json(self, url: str, headers: dict, body: dict) -> dict:
        """POST JSON 请求，返回解析后的 dict。

        - 超时 / 连接错误 / 429 / 5xx 按 ``max_retries`` 自动重试；
        - 401 / 403 / 4xx 业务错误立即抛出标准异常，不做无意义重试。
        """
        last_error: Optional[AIProviderError] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(
                    url, headers=headers, json=body, timeout=self.timeout
                )
            except requests.Timeout as e:
                last_error = AIRequestError(
                    f"{self.provider_name} 请求超时: {e}", timeout=True
                )
            except requests.ConnectionError as e:
                last_error = AIRequestError(
                    f"{self.provider_name} 连接失败: {e}", retryable=True
                )
            except requests.RequestException as e:
                last_error = AIRequestError(f"{self.provider_name} 请求异常: {e}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as e:
                        raise AIResponseError(
                            f"{self.provider_name} 响应不是合法 JSON: {e}"
                        )
                if resp.status_code in (401, 403):
                    raise AIAuthError(
                        f"{self.provider_name} 认证失败(HTTP {resp.status_code})"
                    )
                if resp.status_code == 429:
                    last_error = AIRateLimitError(
                        f"{self.provider_name} 触发限流(HTTP 429)"
                    )
                elif 500 <= resp.status_code < 600:
                    last_error = AIRequestError(
                        f"{self.provider_name} 服务端错误 HTTP {resp.status_code}",
                        retryable=True,
                    )
                else:
                    snippet = ""
                    try:
                        snippet = resp.text[:200]
                    except Exception:
                        pass
                    raise AIResponseError(
                        f"{self.provider_name} HTTP {resp.status_code}: {snippet}"
                    )

            # 可重试错误：指数退避后继续；否则立即抛出
            if last_error is not None and not last_error.retryable:
                raise last_error
            if attempt < self.max_retries:
                time.sleep(0.5 * (attempt + 1))

        assert last_error is not None
        raise last_error

    # ─────────────────────────────────────────────────────
    # 通用工具
    # ─────────────────────────────────────────────────────

    def _normalize_type(self, q_type: str) -> str:
        return TYPE_MAP.get(q_type, "单选题")

    def _wait_for_interval(self):
        """控制最小请求间隔，防止流控。"""
        interval = float(self.config.get("min_interval_seconds", 0) or 0)
        if interval > 0 and self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self._last_request_time = time.time()

    def check_connection(self) -> bool:
        """检查 API 连通性（模板方法，子类无需重写）。"""
        if not self.is_configured():
            return False
        try:
            return bool(self._do_chat("你是答题助手。", "1+1=？只回答数字。", 10, 0.0))
        except AIProviderError as e:
            logger.debug(f"[{self.provider_name}] 连接检查失败: {e}")
            return False

    def _remove_md_json_wrapper(self, md_str: str) -> str:
        """去除 Markdown 代码块包装。"""
        import re
        pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
        match = re.search(pattern, md_str, re.DOTALL)
        return match.group(1).strip() if match else md_str.strip()

    # ─────────────────────────────────────────────────────
    # 底层模型调用（带间隔 + 异常收敛）
    # ─────────────────────────────────────────────────────

    def _call_api_letter(self, prompt: str) -> Optional[str]:
        """字母接口：极简 token，失败收敛为 None（单客户端安全）。"""
        self._wait_for_interval()
        try:
            return self._do_chat(
                SYSTEM_PROMPT_LETTER,
                prompt,
                int(self.config.get("max_tokens", 10)),
                float(self.config.get("temperature", 0.0)),
            )
        except AIProviderError as e:
            self.last_error = str(e)
            logger.debug(f"[{self.provider_name}] letter 调用失败: {e}")
            return None

    def _call_api_content(self, prompt: str, system_prompt: str) -> Optional[str]:
        """内容接口：需要更多 token，失败收敛为 None。"""
        self._wait_for_interval()
        try:
            return self._do_chat(
                system_prompt,
                prompt,
                int(self.config.get("max_tokens_content", 512)),
                float(self.config.get("temperature", 0.0)),
            )
        except AIProviderError as e:
            self.last_error = str(e)
            logger.debug(f"[{self.provider_name}] content 调用失败: {e}")
            return None

    # ─────────────────────────────────────────────────────
    # 对外答题接口（与历史 DeepSeekAI 契约一致）
    # ─────────────────────────────────────────────────────

    def answer_question(self, question: dict) -> Optional[str]:
        """选择/判断题返回字母（或 true/false）；填空/简答等返回文本。带缓存。"""
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])
        if not title:
            return None

        cache_key = f"{q_type}:{title}:{','.join(options) if isinstance(options, list) else options}"
        cached = self._cache.get_cache(cache_key)
        if cached:
            return cached

        if q_type == "判断题":
            result = self._answer_true_false(title)
        elif q_type == "多选题":
            result = self._answer_multiple(title, options)
        elif q_type == "填空题":
            result = self._answer_completion_text(title)
        elif q_type in ("名词解释", "论述题", "计算题", "案例分析题"):
            result = self._answer_subjective_text(title, q_type)
        elif q_type == "简答题":
            result = self._answer_shortanswer_text(title)
        else:
            result = self._answer_single(title, options)

        if result:
            self._cache.add_cache(cache_key, result)
        return result

    def answer_question_content(self, question: dict) -> Optional[List[str]]:
        """内容接口：返回答案文本列表，供非选择题格式适配与相似度匹配。"""
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])
        if not title:
            return None

        try:
            blank_count = int(question.get("blank_count", 0) or 0)
        except (TypeError, ValueError):
            blank_count = 0

        cache_key = (
            f"content:{q_type}:{blank_count}:{title}:"
            f"{','.join(options) if isinstance(options, list) else options}"
        )
        cached = self._cache.get_cache(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        clean_title = self._clean_text(title)

        # 主观题模板作答：payload 携带 non_choice 构建的定制提示词
        if question.get("system_prompt") or question.get("user_content"):
            system_prompt = question.get("system_prompt") or SYSTEM_PROMPT_SHORTANSWER
            prompt = question.get("user_content") or f"题目：{clean_title}"
            response = self._call_api_content(prompt, system_prompt)
            result = parse_answer_response(response)
            if result:
                self._cache.add_cache(
                    cache_key, json.dumps(result, ensure_ascii=False)
                )
                return result
            return None

        # 填空题 / 简答题
        if q_type == "填空题":
            if blank_count >= 2:
                system_prompt = (
                    f"本题为填空题，共有{blank_count}个空。请按空缺在题目中出现的先后顺序逐空作答，"
                    "每个空给出最准确、最简洁的答案（关键词或短语，不要整句、不要解释）。"
                    "以JSON输出，数组元素与各空一一对应，"
                    '示例回答：{"Answer": ["第1空答案","第2空答案"]}。'
                    "除此之外不要输出任何多余的内容，也不要使用MD语法。"
                )
            else:
                system_prompt = (
                    "本题为填空题。请直接填写最准确、最简洁的答案（关键词或短语，不要整句、不要解释）。"
                    '以JSON输出，示例回答：{"Answer": ["答案"]}。'
                    "除此之外不要输出任何多余的内容，也不要使用MD语法。"
                )
            prompt = f"题目：{clean_title}"
        elif q_type == "简答题":
            system_prompt = SYSTEM_PROMPT_SHORTANSWER
            prompt = f"题目：{clean_title}"
        else:
            options_text = ""
            if options:
                if isinstance(options, list):
                    opt_lines = [
                        f"{chr(65 + i)}. {self._clean_text(opt)}"
                        for i, opt in enumerate(options)
                    ]
                    options_text = "\n".join(opt_lines)
                else:
                    options_text = str(options)

            prompt = f"题目：{clean_title}\n选项：{options_text}"

            if q_type == "多选题":
                system_prompt = (
                    "本题为多选题，你必须选择两个或以上选项，请根据题目和选项选择正确答案，"
                    '以json格式输出正确的选项内容，示例回答：{"Answer": ["答案1","答案2"]}。'
                    "除此之外不要输出任何多余的内容，也不要使用MD语法。"
                )
            elif q_type == "判断题":
                system_prompt = (
                    "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，"
                    '以json格式输出正确的答案，示例回答：{"Answer": ["正确"]}。'
                    "除此之外不要输出任何多余的内容，也不要使用MD语法。"
                )
            else:
                system_prompt = (
                    "本题为单选题，你只能选择一个选项，请根据题目和选项选择正确答案，"
                    '以json格式输出正确的选项内容，示例回答：{"Answer": ["答案"]}。'
                    "除此之外不要输出任何多余的内容，也不要使用MD语法。"
                )

        response = self._call_api_content(prompt, system_prompt)
        if not response:
            return None

        try:
            parsed = json.loads(self._remove_md_json_wrapper(response))
            answers = parsed.get("Answer", [])
            if answers:
                result = [str(a).strip() for a in answers if a]
                if result:
                    self._cache.add_cache(
                        cache_key, json.dumps(result, ensure_ascii=False)
                    )
                    return result
        except Exception:
            pass

        # JSON 解析失败：模型可能直接输出纯文本，交由调用方按分隔符处理
        plain = self._clean_answer(self._remove_md_json_wrapper(response))
        return [plain] if plain else None

    # ─────────────────────────────────────────────────────
    # 各题型内部实现
    # ─────────────────────────────────────────────────────

    def _answer_single(self, title: str, options: list) -> Optional[str]:
        if not options:
            return None
        prompt = self._build_prompt(title, options, False)
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else None
        logger.debug(f"[{self.provider_name}] 单选题: response={response}, letter={letter}")
        return letter

    def _answer_multiple(self, title: str, options: list) -> Optional[str]:
        if not options:
            return None
        prompt = self._build_prompt(title, options, True)
        response = self._call_api_letter(prompt)
        letters = self._extract_letters(response) if response else None
        logger.debug(f"[{self.provider_name}] 多选题: response={response}, letters={letters}")
        return letters if letters else None

    def _answer_true_false(self, title: str) -> Optional[str]:
        prompt = self._clean_text(title) + "\nA对B错"
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else None
        logger.debug(f"[{self.provider_name}] 判断题: response={response}, letter={letter}")
        if letter == "A":
            return "true"
        if letter == "B":
            return "false"
        return None

    def _answer_completion_text(self, title: str) -> Optional[str]:
        prompt = f"填空题：{self._clean_text(title)}\n请直接填写答案，只输出答案内容。"
        response = self._call_api_content(prompt, SYSTEM_PROMPT_COMPLETION)
        return self._clean_answer(response) if response else ""

    def _answer_shortanswer_text(self, title: str) -> Optional[str]:
        prompt = f"简答题：{self._clean_text(title)}\n请简要回答，只输出答案内容。"
        response = self._call_api_content(prompt, SYSTEM_PROMPT_SHORTANSWER)
        return self._clean_answer(response) if response else ""

    def _answer_subjective_text(self, title: str, q_type: str) -> Optional[str]:
        """主观题（名词解释/论述/计算/案例分析）按答题模板作答。"""
        code_map = {
            "名词解释": "term_explanation",
            "论述题": "essay",
            "计算题": "calculation",
            "案例分析题": "case_analysis",
        }
        system_prompt = build_subjective_system_prompt(
            code_map.get(q_type, "shortanswer"), title
        )
        prompt = f"题目：{self._clean_text(title)}"
        response = self._call_api_content(prompt, system_prompt)
        return self._clean_answer(response) if response else ""

    # ─────────────────────────────────────────────────────
    # 文本处理
    # ─────────────────────────────────────────────────────

    def _build_prompt(self, title: str, options: list, is_multi: bool) -> str:
        clean_title = self._clean_text(title)
        opt_lines = [
            f"{chr(65 + i)}{self._clean_text(opt)}" for i, opt in enumerate(options)
        ]
        return clean_title + "\n" + "".join(opt_lines)

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    def _clean_answer(self, text: str) -> str:
        import re
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'^["\']|["\']$', '', text)
        text = re.sub(r'^答案[：:]\s*', '', text)
        return text.strip()

    def _extract_letter(self, response: str) -> str:
        for c in str(response).upper():
            if c in "ABCDEFGHIJK":
                return c
        return ""

    def _extract_letters(self, response: str) -> str:
        letters = []
        for c in str(response).upper():
            if c in "ABCDEFGHIJK" and c not in letters:
                letters.append(c)
        return "".join(sorted(letters)) if letters else ""

    # ─────────────────────────────────────────────────────
    # 缓存辅助
    # ─────────────────────────────────────────────────────

    def get_cache_stats(self) -> dict:
        return self._cache.get_stats()

    def clear_cache(self) -> None:
        self._cache.clear_cache()
