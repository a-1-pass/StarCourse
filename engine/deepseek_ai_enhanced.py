"""
增强版 DeepSeek AI 答题模块
adapted from reference implementation
- 支持 OpenAI SDK 和标准 requests 两种方式
- 返回答案内容（非仅字母），支持相似度匹配
- 带缓存、请求间隔、连接检查
"""
import json
import logging
import os
import re
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger("DeepSeekAI")

# 尝试导入 OpenAI SDK（可选）
try:
    import httpx
    from openai import OpenAI
    OPENAI_SDK_AVAILABLE = True
except ImportError:
    OPENAI_SDK_AVAILABLE = False

from cache_dao import CacheDAO
from non_choice import build_subjective_system_prompt, parse_answer_response

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


class DeepSeekAI:
    """DeepSeek API 客户端（增强版，兼容 reference implementation 设计）"""

    DEFAULT_CONFIG = {
        # 密钥不再硬编码：缺失时明确报错引导用户在 ai_config.json 中配置，而非静默降级
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "max_tokens": 10,
        "temperature": 0.0,
        "use_openai_sdk": False,  # 是否使用 OpenAI SDK
        "min_interval_seconds": 0,  # 请求间隔
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

    def _normalize_type(self, q_type: str) -> str:
        return TYPE_MAP.get(q_type, "单选题")

    def __init__(self, config_path: str = None):
        self.config = self.DEFAULT_CONFIG.copy()
        # README 指引用户创建 config.json；同时兼容旧的 ai_config.json
        self._load_config(config_path or "ai_config.json")
        if not self.config.get("api_key"):
            self._load_config("config.json")
        self._session = requests.Session()
        self._cache = CacheDAO()
        self._last_request_time = None
        self._lock = threading.Lock()

    def _load_config(self, config_path: str) -> bool:
        try:
            # 尝试多个路径
            paths_to_try = [
                config_path,
                os.path.join(os.path.dirname(os.path.abspath(__file__)), config_path),
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), config_path),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), config_path),
            ]
            for p in paths_to_try:
                if p and os.path.exists(p):
                    with open(p, encoding='utf-8') as f:
                        user_config = json.load(f)
                        if "deepseek" in user_config:
                            self.config.update(user_config["deepseek"])
                    return True
            return False
        except Exception:
            return False

    def is_configured(self) -> bool:
        api_key = str(self.config.get("api_key", "")).strip()
        return bool(api_key) and api_key.startswith("sk-") and len(api_key) > 10

    def _wait_for_interval(self):
        """控制请求间隔，防止流控"""
        interval = self.config.get("min_interval_seconds", 0)
        if interval > 0 and self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed < interval:
                sleep_time = interval - elapsed
                time.sleep(sleep_time)
        self._last_request_time = time.time()

    def check_connection(self) -> bool:
        """检查API连接是否可用"""
        try:
            self._wait_for_interval()
            if self.config.get("use_openai_sdk") and OPENAI_SDK_AVAILABLE:
                client = self._get_openai_client()
                completion = client.chat.completions.create(
                    model=self.config["model"],
                    messages=[{"role": "user", "content": "1+1=？只回答数字。"}],
                    max_tokens=10
                )
                return bool(completion.choices and completion.choices[0].message.content)
            else:
                url = f"{self.config['base_url']}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.config['api_key']}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": self.config["model"],
                    "messages": [{"role": "user", "content": "1+1=？只回答数字。"}],
                    "max_tokens": 10,
                    "temperature": 0.0,
                    "stream": False
                }
                resp = self._session.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    result = resp.json()
                    return bool(result.get("choices") and result["choices"][0].get("message", {}).get("content"))
                return False
        except Exception:
            return False

    def _get_openai_client(self) -> Optional["OpenAI"]:
        """获取OpenAI SDK客户端"""
        if not OPENAI_SDK_AVAILABLE:
            return None
        proxy = self.config.get("http_proxy")
        if proxy:
            httpx_client = httpx.Client(proxy=proxy)
            return OpenAI(http_client=httpx_client, base_url=self.config["base_url"], api_key=self.config["api_key"])
        return OpenAI(base_url=self.config["base_url"], api_key=self.config["api_key"])

    def _remove_md_json_wrapper(self, md_str: str) -> str:
        """去除Markdown代码块包装"""
        pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
        match = re.search(pattern, md_str, re.DOTALL)
        return match.group(1).strip() if match else md_str.strip()

    def _call_api_letter(self, prompt: str) -> str | None:
        """调用API，返回字母答案（极简token，兼容旧版）"""
        self._wait_for_interval()
        try:
            url = f"{self.config['base_url']}/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.config["model"],
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT_LETTER},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": self.config.get("max_tokens", 10),
                "temperature": self.config.get("temperature", 0.0),
                "stream": False
            }
            resp = self._session.post(url, headers=headers, json=data, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                return result["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None

    def _call_api_content(self, prompt: str, system_prompt: str) -> str | None:
        """调用API，返回答案内容（需要更多token，支持相似度匹配）"""
        self._wait_for_interval()
        try:
            if self.config.get("use_openai_sdk") and OPENAI_SDK_AVAILABLE:
                client = self._get_openai_client()
                completion = client.chat.completions.create(
                    model=self.config["model"],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=self.config.get("max_tokens_content", 512),
                    temperature=self.config.get("temperature", 0.0),
                )
                return completion.choices[0].message.content
            else:
                url = f"{self.config['base_url']}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.config['api_key']}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": self.config["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": self.config.get("max_tokens_content", 512),
                    "temperature": self.config.get("temperature", 0.0),
                    "stream": False
                }
                resp = self._session.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    result = resp.json()
                    return result["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None

    # ==================== 旧版接口（兼容现有代码） ====================

    def answer_question(self, question: dict) -> str | None:
        """
        旧版接口：返回字母答案（选择/判断题）或文本答案（填空/简答题），带缓存
        保持与现有 Work.py / Quiz.py 兼容
        """
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])

        if not title:
            return None

        # 构建缓存key
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

    def _answer_single(self, title: str, options: list[str]) -> str | None:
        if not options:
            return None
        prompt = self._build_prompt(title, options, False)
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else None
        logger.debug(f"[AI] 单选题返回: response={response}, letter={letter}")
        return letter

    def _answer_multiple(self, title: str, options: list[str]) -> str | None:
        if not options:
            return None
        prompt = self._build_prompt(title, options, True)
        response = self._call_api_letter(prompt)
        letters = self._extract_letters(response) if response else None
        logger.debug(f"[AI] 多选题返回: response={response}, letters={letters}")
        return letters if letters else None

    def _answer_true_false(self, title: str) -> str | None:
        prompt = self._clean_text(title) + "\nA对B错"
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else None
        logger.debug(f"[AI] 判断题返回: response={response}, letter={letter}")
        if letter == "A":
            return "true"
        elif letter == "B":
            return "false"
        return None

    def _answer_completion_text(self, title: str) -> str | None:
        prompt = f"填空题：{self._clean_text(title)}\n请直接填写答案，只输出答案内容。"
        response = self._call_api_content(prompt, self.SYSTEM_PROMPT_COMPLETION)
        return self._clean_answer(response) if response else ""

    def _answer_shortanswer_text(self, title: str) -> str | None:
        prompt = f"简答题：{self._clean_text(title)}\n请简要回答，只输出答案内容。"
        response = self._call_api_content(prompt, self.SYSTEM_PROMPT_SHORTANSWER)
        return self._clean_answer(response) if response else ""

    def _answer_subjective_text(self, title: str, q_type: str) -> str | None:
        """主观题（名词解释/论述/计算/案例分析）按答题模板作答（内容接口）。"""
        code_map = {"名词解释": "term_explanation", "论述题": "essay",
                    "计算题": "calculation", "案例分析题": "case_analysis"}
        system_prompt = build_subjective_system_prompt(code_map.get(q_type, "shortanswer"), title)
        prompt = f"题目：{self._clean_text(title)}"
        response = self._call_api_content(prompt, system_prompt)
        return self._clean_answer(response) if response else ""

    def _clean_answer(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'^["\']|["\']$', '', text)
        text = re.sub(r'^答案[：:]\s*', '', text)
        text = text.strip()
        return text

    def _build_prompt(self, title: str, options: list[str], is_multi: bool) -> str:
        clean_title = self._clean_text(title)
        opt_lines = []
        for i, opt in enumerate(options):
            letter = chr(65 + i)
            opt_lines.append(f"{letter}{self._clean_text(opt)}")
        return clean_title + "\n" + "".join(opt_lines)

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        text = ' '.join(text.split())
        return text

    def _extract_letter(self, response: str) -> str:
        for c in response.upper():
            if c in 'ABCDEFGHIJK':
                return c
        return ""

    def _extract_letters(self, response: str) -> str:
        letters = []
        for c in response.upper():
            if c in 'ABCDEFGHIJK' and c not in letters:
                letters.append(c)
        return ''.join(sorted(letters)) if letters else ""

    # ==================== 新版接口（学习reference implementation，返回答案内容） ====================

    def answer_question_content(self, question: dict) -> list[str] | None:
        """
        新版接口：返回答案内容列表，支持相似度匹配
        adapted from reference implementation
        """
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])

        if not title:
            return None

        # 填空题的空数（由 non_choice 模块预先识别并传入）
        blank_count = 0
        try:
            blank_count = int(question.get("blank_count", 0) or 0)
        except (TypeError, ValueError):
            blank_count = 0

        # 构建缓存key
        cache_key = f"content:{q_type}:{blank_count}:{title}:{','.join(options) if isinstance(options, list) else options}"
        cached = self._cache.get_cache(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        clean_title = self._clean_text(title)

        # 主观题模板作答：payload 携带定制提示词（由 non_choice 构建，含评分规则/结构模板/学科要求）
        if question.get("system_prompt") or question.get("user_content"):
            system_prompt = question.get("system_prompt") or self.SYSTEM_PROMPT_SHORTANSWER
            prompt = question.get("user_content") or f"题目：{clean_title}"
            response = self._call_api_content(prompt, system_prompt)
            result = parse_answer_response(response)
            if result:
                self._cache.add_cache(cache_key, json.dumps(result, ensure_ascii=False))
                return result
            return None

        # 填空题、简答题直接处理
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
            system_prompt = self.SYSTEM_PROMPT_SHORTANSWER
            prompt = f"题目：{clean_title}"
        else:
            # 构建prompt
            options_text = ""
            if options:
                if isinstance(options, list):
                    opt_lines = []
                    for i, opt in enumerate(options):
                        opt_lines.append(f"{chr(65 + i)}. {self._clean_text(opt)}")
                    options_text = "\n".join(opt_lines)
                else:
                    options_text = str(options)

            prompt = f"题目：{clean_title}\n选项：{options_text}"

            # 根据题型选择system prompt
            if q_type == "多选题":
                system_prompt = ("本题为多选题，你必须选择两个或以上选项，请根据题目和选项选择正确答案，"
                               "以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\"答案2\"]}。"
                               "除此之外不要输出任何多余的内容，也不要使用MD语法。")
            elif q_type == "判断题":
                system_prompt = ("本题为判断题，你只能回答正确或者错误，请根据题目回答问题，"
                               "以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。"
                               "除此之外不要输出任何多余的内容，也不要使用MD语法。")
            else:
                system_prompt = ("本题为单选题，你只能选择一个选项，请根据题目和选项选择正确答案，"
                               "以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。"
                               "除此之外不要输出任何多余的内容，也不要使用MD语法。")

        response = self._call_api_content(prompt, system_prompt)
        if not response:
            return None

        try:
            parsed = json.loads(self._remove_md_json_wrapper(response))
            answers = parsed.get("Answer", [])
            if answers:
                result = [str(a).strip() for a in answers if a]
                if result:
                    self._cache.add_cache(cache_key, json.dumps(result, ensure_ascii=False))
                    return result
        except Exception:
            pass

        # JSON 解析失败：模型可能直接输出了纯文本答案，
        # 交由调用方（non_choice 格式适配）按换行/分隔符处理，而非直接丢弃
        plain = self._clean_answer(self._remove_md_json_wrapper(response))
        return [plain] if plain else None

    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        return self._cache.get_stats()

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear_cache()
