# _*_ coding:utf-8 _*_
"""
阶跃星辰 Step Plan 推理大模型客户端
支持 step-3.7-flash、step-3.5-flash-2603、step-3.5-flash、step-router-v1 等模型
API 路径前缀：/step_plan/v1/...
域名：https://api.stepfun.com
"""
import json
import logging
import re
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("StepFunAI")

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

# 主观题题型标签
SUBJECTIVE_LABELS = ("名词解释", "论述题", "计算题", "案例分析题")

# 主观题作答结构模板（与 engine/non_choice 模块保持一致语义）
SUBJECTIVE_TEMPLATES = {
    "简答题": "先直接给出核心结论；再分3~5点展开，每点采用“关键词＋一句阐释”的形式。",
    "名词解释": "按“定义→核心内涵或特征→意义/典型例子”三段作答，全文控制在100字左右。",
    "论述题": "采用“总—分—总”结构：开篇亮明总观点；主体分3~5个论证点，每点“论点＋理论依据＋展开”；结尾总结升华。",
    "计算题": "严格按步骤作答：①写出公式；②代入数据；③给出计算过程；④明确结果并保留单位。",
    "案例分析题": "按“结论→依据→分析→总结”作答，必须紧扣案例材料，不得脱离案情。",
}

SUPPORTED_MODELS = [
    "step-3.7-flash",
    "step-3.5-flash-2603",
    "step-3.5-flash",
    "step-router-v1",
]

REASONING_EFFORTS = ["low", "medium", "high"]


class StepFunAI:
    """阶跃星辰 Step Plan 推理大模型 API 客户端"""

    DEFAULT_CONFIG = {
        "api_key": "",
        "base_url": "https://api.stepfun.com",
        "path_prefix": "/step_plan/v1",
        "model": "step-3.5-flash",
        "max_tokens": 10,
        "max_tokens_content": 512,
        "temperature": 0.0,
        "reasoning_effort": "medium",
        "min_interval_seconds": 0,
        "http_proxy": "",
    }

    SYSTEM_PROMPT_LETTER = "输出答案字母，如A或AB。"
    SYSTEM_PROMPT_CONTENT = "请直接输出答案内容，不要输出多余的解释。"

    def __init__(self, config_path: str = "ai_config.json"):
        self.config = self.DEFAULT_CONFIG.copy()
        self._load_config(config_path)
        # README 指引用户创建 config.json；缺失密钥时再尝试一次
        if not self.config.get("api_key"):
            self._load_config("config.json")
        self._session = requests.Session()
        self._last_request_time = 0

        if self.config.get("http_proxy"):
            proxy = self.config["http_proxy"]
            self._session.proxies = {
                "http": proxy,
                "https": proxy,
            }

    def _load_config(self, config_path: str) -> bool:
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths_to_try = [
            config_path,
            os.path.join(project_root, config_path),
        ]
        for path in paths_to_try:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    if "stepfun" in user_config:
                        stepfun_config = user_config["stepfun"]
                        for key in self.config:
                            if key in stepfun_config:
                                self.config[key] = stepfun_config[key]
                        return True
            except Exception as e:
                logger.warning(f"加载 StepFun 配置失败: {e}")
                continue
        return False

    def is_configured(self) -> bool:
        api_key = self.config.get("api_key", "")
        return bool(api_key) and not api_key.startswith("sk-placeholder")

    def _normalize_type(self, q_type: str) -> str:
        return TYPE_MAP.get(q_type, "单选题")

    def answer_question(self, question: Dict) -> Optional[str]:
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])

        if not title:
            return None

        if q_type == "判断题":
            return self._answer_true_false(title)
        elif q_type == "多选题":
            return self._answer_multiple(title, options)
        elif q_type == "填空题":
            return self._answer_completion(title)
        elif q_type in SUBJECTIVE_LABELS:
            return self._answer_subjective_text(title, q_type)
        elif q_type == "简答题":
            return self._answer_shortanswer(title)
        else:
            return self._answer_single(title, options)

    def answer_question_content(self, question: Dict) -> Optional[List[str]]:
        if not self.is_configured():
            return None

        q_type = self._normalize_type(question.get("question_type", ""))
        title = question.get("title", "")
        options = question.get("options", [])

        if not title:
            return None

        # 主观题模板作答：payload 携带定制提示词（由 engine/non_choice 构建并下发）
        if question.get("system_prompt") or question.get("user_content"):
            system_prompt = question.get("system_prompt") or self.SYSTEM_PROMPT_SHORTANSWER
            user_content = question.get("user_content") or f"题目：{self._clean_text(title)}"
            response = self._call_api_content(user_content, system_prompt)
            return self._parse_content_json(response)

        if q_type in ("填空题", "简答题"):
            return self._answer_text_question(q_type, title, question.get("blank_count", 0))

        clean_title = self._clean_text(title)
        options_text = ""
        if options:
            if isinstance(options, list):
                opt_lines = []
                for i, opt in enumerate(options):
                    opt_lines.append(f"{chr(65 + i)}. {self._clean_text(opt)}")
                options_text = "\n".join(opt_lines)
            else:
                options_text = str(options)

        if q_type == "多选题":
            system_prompt = ("本题为多选题，必须选择两个或以上选项。请根据题目和选项选择正确答案，"
                           "以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\"答案2\"]}。"
                           "只输出json，不要多余内容。")
        elif q_type == "判断题":
            system_prompt = ("本题为判断题，只能回答正确或者错误。请根据题目回答问题，"
                           "以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。"
                           "只输出json，不要多余内容。")
        else:
            system_prompt = ("本题为单选题，只能选择一个选项。请根据题目和选项选择正确答案，"
                           "以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。"
                           "只输出json，不要多余内容。")

        prompt = f"题目：{clean_title}\n选项：{options_text}"
        response = self._call_api_content(prompt, system_prompt)
        if not response:
            return None

        try:
            parsed = json.loads(self._remove_md_json_wrapper(response))
            answers = parsed.get("Answer", [])
            if answers:
                return [str(a).strip() for a in answers if a]
        except Exception:
            pass

        # 纯文本兜底：交由 non_choice 格式适配处理
        plain = self._clean_answer(self._remove_md_json_wrapper(response))
        return [plain] if plain else None

    def _answer_text_question(self, q_type: str, title: str, blank_count=0) -> Optional[List[str]]:
        """填空题/简答题结构化作答：JSON 逐空输出，解析失败回退纯文本。"""
        try:
            blank_count = int(blank_count or 0)
        except (TypeError, ValueError):
            blank_count = 0
        clean_title = self._clean_text(title)

        if q_type == "填空题":
            if blank_count >= 2:
                system_prompt = (
                    f"本题为填空题，共有{blank_count}个空。请按空缺出现的先后顺序逐空作答，"
                    "每个空给出最准确、最简洁的关键词或短语，不要解释。"
                    '以json输出，示例：{"Answer": ["第1空答案","第2空答案"]}。只输出json，不要多余内容。'
                )
            else:
                system_prompt = (
                    "本题为填空题。请填写最准确、最简洁的关键词或短语，不要解释。"
                    '以json输出，示例：{"Answer": ["答案"]}。只输出json，不要多余内容。'
                )
        else:
            system_prompt = (
                "本题为简答题。请简要、完整地回答，以json输出，"
                '示例：{"Answer": ["答案"]}。只输出json，不要多余内容。'
            )

        response = self._call_api_content(f"题目：{clean_title}", system_prompt)
        if not response:
            return None

        unwrapped = self._remove_md_json_wrapper(response)
        try:
            parsed = json.loads(unwrapped)
            answers = parsed.get("Answer", [])
            if answers:
                result = [str(a).strip() for a in answers if str(a).strip()]
                if result:
                    return result
        except Exception:
            pass

        plain = self._clean_answer(unwrapped)
        return [plain] if plain else None

    def _parse_content_json(self, response: str) -> Optional[List[str]]:
        """内容接口返回解析：JSON Answer 包装优先，纯文本兜底。"""
        if not response:
            return None
        unwrapped = self._remove_md_json_wrapper(response)
        try:
            parsed = json.loads(unwrapped)
            answers = parsed.get("Answer", [])
            if answers:
                result = [str(a).strip() for a in answers if str(a).strip()]
                if result:
                    return result
        except Exception:
            pass
        plain = self._clean_answer(unwrapped)
        return [plain] if plain else None

    def _answer_subjective_text(self, title: str, q_type: str) -> Optional[str]:
        """主观题（名词解释/论述/计算/案例分析）按答题模板作答（内容接口）。"""
        template = SUBJECTIVE_TEMPLATES.get(q_type, SUBJECTIVE_TEMPLATES["简答题"])
        system_prompt = (
            f"本题为{q_type}。评分采用踩点给分：答案须覆盖全部得分要点，分点编号作答，"
            f"使用规范学科术语。作答结构要求：{template}"
            '最终以json输出：{"Answer": ["完整答案正文"]}，只输出json，不要多余内容。'
        )
        response = self._call_api_content(f"题目：{self._clean_text(title)}", system_prompt)
        if not response:
            return None
        plain = self._clean_answer(self._remove_md_json_wrapper(response))
        return plain or None

    def _answer_single(self, title: str, options: List[str]) -> Optional[str]:
        if not options:
            return None
        prompt = self._build_prompt(title, options, False)
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else ""
        logger.debug(f"[StepFun] 单选题返回: response={response}, letter={letter}")
        return letter if letter else None

    def _answer_multiple(self, title: str, options: List[str]) -> Optional[str]:
        if not options:
            return None
        prompt = self._build_prompt(title, options, True)
        response = self._call_api_letter(prompt)
        letters = self._extract_letters(response) if response else ""
        logger.debug(f"[StepFun] 多选题返回: response={response}, letters={letters}")
        return letters if letters else None

    def _answer_true_false(self, title: str) -> Optional[str]:
        prompt = self._clean_text(title) + "\nA对B错"
        response = self._call_api_letter(prompt)
        letter = self._extract_letter(response) if response else ""
        logger.debug(f"[StepFun] 判断题返回: response={response}, letter={letter}")
        if letter == "A":
            return "true"
        elif letter == "B":
            return "false"
        return None

    def _answer_completion(self, title: str) -> Optional[str]:
        prompt = f"填空题：{self._clean_text(title)}\n请直接填写答案，只输出答案内容。"
        response = self._call_api_content(prompt, self.SYSTEM_PROMPT_CONTENT)
        return self._clean_answer(response) if response else ""

    def _answer_shortanswer(self, title: str) -> Optional[str]:
        prompt = f"简答题：{self._clean_text(title)}\n请简要回答，只输出答案内容。"
        response = self._call_api_content(prompt, self.SYSTEM_PROMPT_CONTENT)
        return self._clean_answer(response) if response else ""

    def _answer_text_type(self, title: str, q_type: str) -> Optional[str]:
        prompt = f"{q_type}：{self._clean_text(title)}\n请直接回答，只输出答案内容。"
        response = self._call_api_content(prompt, self.SYSTEM_PROMPT_CONTENT)
        return self._clean_answer(response) if response else ""

    def _build_prompt(self, title: str, options: List[str], is_multi: bool) -> str:
        clean_title = self._clean_text(title)
        opt_lines = []
        for i, opt in enumerate(options):
            letter = chr(65 + i)
            opt_lines.append(f"{letter}. {self._clean_text(opt)}")
        return clean_title + "\n" + "\n".join(opt_lines)

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        text = ' '.join(text.split())
        return text

    def _clean_answer(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'^["\']|["\']$', '', text)
        text = re.sub(r'^答案[：:]\s*', '', text)
        text = text.strip()
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

    def _remove_md_json_wrapper(self, md_str: str) -> str:
        pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
        match = re.search(pattern, md_str, re.DOTALL)
        return match.group(1).strip() if match else md_str.strip()

    def _rate_limit_wait(self):
        min_interval = self.config.get("min_interval_seconds", 0)
        if min_interval > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < min_interval:
                wait_time = min_interval - elapsed
                time.sleep(wait_time)

    def _call_api_letter(self, prompt: str) -> Optional[str]:
        return self._call_api(prompt, self.SYSTEM_PROMPT_LETTER, self.config.get("max_tokens", 10))

    def _call_api_content(self, prompt: str, system_prompt: str) -> Optional[str]:
        return self._call_api(prompt, system_prompt, self.config.get("max_tokens_content", 512))

    def _call_api(self, prompt: str, system_prompt: str, max_tokens: int) -> Optional[str]:
        if not self.is_configured():
            return None

        self._rate_limit_wait()

        try:
            base_url = self.config.get("base_url", "https://api.stepfun.com")
            path_prefix = self.config.get("path_prefix", "/step_plan/v1")
            url = f"{base_url}{path_prefix}/chat/completions"

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
                "max_tokens": max_tokens,
                "temperature": self.config.get("temperature", 0.0),
                "stream": False,
            }

            reasoning_effort = self.config.get("reasoning_effort", "")
            if reasoning_effort and reasoning_effort in REASONING_EFFORTS:
                model_name = self.config.get("model", "")
                if model_name == "step-3.7-flash":
                    data["reasoning_effort"] = reasoning_effort

            resp = self._session.post(url, headers=headers, json=data, timeout=60)

            self._last_request_time = time.time()

            if resp.status_code == 200:
                result = resp.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content
            else:
                logger.error(f"StepFun API 请求失败: status={resp.status_code}, body={resp.text[:500]}")
                return None

        except Exception as e:
            logger.error(f"StepFun API 调用异常: {e}")
            return None

    def check_connection(self) -> bool:
        """检查 API 连接是否正常"""
        if not self.is_configured():
            return False
        try:
            result = self._call_api("你好，请回复OK", "你是一个助手", 5)
            return bool(result)
        except Exception:
            return False
