# -*- coding: utf-8 -*-
"""Answer-submission handler for course work items."""
import copy
import json
import random
import re
import time
from difflib import SequenceMatcher
from hashlib import md5
from urllib.parse import urlencode

import loguru
import requests
from lxml import etree

from classis.Media import Media
from utils import doGet, ses
from non_choice import (
    FALLBACK_TEXT,
    NON_CHOICE_TYPES,
    answer_non_question,
)

# 超星题型码 -> 内部题型名（0-4 基础题型，5-8 主观题扩展码）
_QUESTION_TYPE_MAP = {
    "0": "single", "1": "multiple", "2": "completion", "3": "judgement",
    "4": "shortanswer", "5": "term_explanation", "6": "essay",
    "7": "calculation", "8": "case_analysis",
}


# ── 工具函数 ──────────────────────────────────────────────

def _extract_letters(text: str) -> list:
    """从答案文本中提取选项字母（A、B、C、D等）"""
    if not text:
        return []
    text = text.strip().upper()
    
    # 纯字母串，如 "ABC"、"ABD"
    if re.match(r'^[A-Z]{1,10}$', text):
        return list(text)
    
    # 常见分隔格式：A、B、C / A.B.C / A,B,C / A B C / A) B) C)
    patterns = [
        r'([A-Z])[、,，.。；;：:\s]+',  # A、B、C
        r'([A-Z])\)\s*',  # A) B) C)
        r'([A-Z])\.\s*',  # A. B. C.
        r'选项\s*([A-Z])',  # 选项A
    ]
    
    letters = []
    for pat in patterns:
        found = re.findall(pat, text)
        if found and len(found) >= 1:
            letters.extend(found)
    
    if letters:
        seen = set()
        result = []
        for l in letters:
            if l not in seen and 'A' <= l <= 'Z':
                seen.add(l)
                result.append(l)
        return result
    
    # 最后尝试：文本中独立的大写字母
    single_letters = re.findall(r'(?<![A-Za-z])([A-Z])(?![A-Za-z])', text)
    if single_letters:
        seen = set()
        result = []
        for l in single_letters:
            if l not in seen:
                seen.add(l)
                result.append(l)
        return result
    
    return []


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    char_map = str.maketrans({'⻛': '风', '⻔': '门', '⻋': '车', '⻢': '马'})
    normalized = text.translate(char_map)
    normalized = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', normalized)
    normalized = re.sub(r'\s+', '', normalized)
    normalized = re.sub(r'[，。！？；：,.!?;:()（）\[\]【】""''_/\\|《》<>〈〉…—～~]', '', normalized)
    return normalized.lower()


def _best_option_by_similarity(target: str, options: list, threshold: float = 0.8) -> str:
    if not target or not options:
        return ""
    target_norm = _normalize_text(target)
    if not target_norm:
        return ""
    best_letter, best_score = "", 0.0
    for opt in options:
        if isinstance(opt, dict):
            opt_text = opt.get('text', '')
            opt_letter = opt.get('letter', '')
        else:
            opt_text = opt
            opt_letter = opt[:1] if opt else ""
        opt_norm = _normalize_text(opt_text)
        if not opt_norm:
            continue
        score = SequenceMatcher(None, target_norm, opt_norm).ratio()
        if score > best_score:
            best_score, best_letter = score, opt_letter
    if best_score >= threshold:
        loguru.logger.info(f"      [相似度{threshold:.1f}] 匹配: {best_letter} ({best_score:.2f})")
        return best_letter
    return ""


def _is_subsequence(a, o):
    if not a:
        return False
    if len(a) > len(o):
        return False
    iter_o = iter(o)
    return all(c in iter_o for c in a)


def _clean_res(res):
    cleaned_res = []
    if isinstance(res, str):
        res = [res]
    for c in res:
        cleaned = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*|[.,!?;:，。！？；：]', '', c) if len(c) > 1 else c
        cleaned_res.append(cleaned.strip())
    return cleaned_res


def _multi_cut(answer: str):
    if not answer:
        return []
    # 优先按换行分割
    if "\n" in answer:
        parts = [p.strip() for p in answer.split("\n") if p.strip()]
        if parts:
            return parts
    # 然后尝试中文顿号、逗号、分号
    for char in ["、", "，", "；", ";", ",", "|", "/"]:
        if char in answer:
            parts = [p.strip() for p in answer.split(char) if p.strip()]
            if parts and len(parts) >= 2:
                return parts
    return [answer]


def _random_answer(options_str: str, q_type: str = "single") -> str:
    if q_type == "judgement":
        return "true" if random.choice([True, False]) else "false"
    elif q_type in ("completion", "shortanswer"):
        return "暂无答案"
    if not options_str:
        return ""
    options = options_str.split("\n") if "\n" in options_str else _multi_cut(options_str)
    if q_type == "multiple":
        available = len(options)
        select_count = min(random.randint(2, min(4, available)), available) if available > 1 else available
        selected = random.sample(options, select_count) if select_count > 0 else []
        return "".join(sorted(set(o[:1] for o in selected)))
    elif q_type == "single":
        return random.choice(options)[:1] if options else ""
    return ""


# ── Work 类 ───────────────────────────────────────────────

class Work(Media):
    def __init__(self, attachment: dict, headers, defaults: dict, courseId: str,
                 strategy: str = "first", ai_client=None, tiku=None,
                 clazzId: str = "", userid: str = "", ktoken: str = "",
                 knowledgeid: str = "", cpi: str = ""):
        super().__init__(attachment, headers)
        self.defaults = defaults or {}
        self.courseId = courseId
        # 题库答题模式：题库优先，选择题按首选策略兜底（与 AI 答题路径相互独立）
        if strategy == "tiku":
            strategy = "first"
        self.strategy = strategy
        self.workid = attachment.get("property", {}).get("workid", "") or attachment.get("workid", "")
        self.title = attachment.get("property", {}).get("title", "作业")
        self.enc = attachment.get("enc", "")
        self._ai_client = ai_client
        self._tiku = tiku

        # 关键参数 — adapted from reference implementation
        self.clazzId = clazzId or self.defaults.get("clazzId", "")
        self.userid = userid or self.defaults.get("userid", "")
        self.ktoken = ktoken or self.defaults.get("ktoken", "") or attachment.get("ktoken", "")
        self.knowledgeid = knowledgeid or self.defaults.get("knowledgeid", "")
        self.cpi = cpi or self.defaults.get("cpi", "") or attachment.get("cpi", "")

        self._session = None

    # ── AI 客户端 ────────────────────────────────────────

    def _get_ai_provider_from_config(self) -> str:
        """从配置文件读取默认 AI 提供商"""
        try:
            import json
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            engine_dir = os.path.dirname(os.path.dirname(current_dir))
            src_dir = os.path.dirname(engine_dir)
            project_dir = os.path.dirname(src_dir)
            config_paths = [
                "config.json",
                os.path.join(project_dir, "config.json"),
                os.path.join(src_dir, "config.json"),
                os.path.join(engine_dir, "config.json"),
            ]
            for p in config_paths:
                if p and os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                        return cfg.get("default_provider", "deepseek")
        except Exception:
            pass
        return "deepseek"

    def _get_ai_client(self):
        if self._ai_client is not None:
            return self._ai_client

        # 优先：多模型统一架构（DeepSeek/GPT/Claude/文心/StepFun，按优先级容灾）
        try:
            from ai_providers import build_multi_client
            multi = build_multi_client()
            if multi is not None:
                self._ai_client = multi
                return self._ai_client
        except Exception as e:
            loguru.logger.debug(f"多模型客户端加载失败: {e}")

        provider = self._get_ai_provider_from_config()

        if provider == "stepfun":
            try:
                from src.stepfun_ai import StepFunAI
                client = StepFunAI()
                if client.is_configured():
                    self._ai_client = client
                    return self._ai_client
            except Exception as e:
                loguru.logger.debug(f"StepFun AI 加载失败: {e}")
            try:
                from stepfun_ai import StepFunAI
                client = StepFunAI()
                if client.is_configured():
                    self._ai_client = client
                    return self._ai_client
            except Exception as e:
                loguru.logger.debug(f"StepFun AI (engine) load failed: {e}")

        try:
            from deepseek_ai_enhanced import DeepSeekAI
            self._ai_client = DeepSeekAI()
            return self._ai_client
        except Exception as e:
            loguru.logger.debug(f"增强版AI加载失败: {e}")
        if self._ai_client is None:
            try:
                from src.ai_assistant import DeepSeekAI
                self._ai_client = DeepSeekAI()
            except Exception:
                pass
        return self._ai_client

    # ── 题目获取 — adapted from reference implementation

    def _fetch_questions_from_api(self):
        jobid = self.jobid or self.attachment.get("jobid", "")
        if not jobid:
            loguru.logger.error("没有找到 jobid")
            return None

        workId = jobid.replace("work-", "")
        loguru.logger.info(f"获取题目: workId={workId}, jobid={jobid}")

        params = {
            "api": "1",
            "workId": workId,
            "jobid": jobid,
            "originJobId": jobid,
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(self.knowledgeid),
            "ktoken": self.ktoken,
            "cpi": str(self.cpi),
            "ut": "s",
            "clazzId": str(self.clazzId),
            "type": "",
            "enc": self.enc,
            "mooc2": "1",
            "courseid": str(self.courseId),
        }

        query_string = urlencode(params)
        api_urls = [
            "https://mooc1.chaoxing.com/mooc-ans/api/work",
            "https://mooc1-1.chaoxing.com/mooc-ans/api/work",
            "https://mooc1-2.chaoxing.com/mooc-ans/api/work",
        ]

        for api_url in api_urls:
            try:
                full_url = f"{api_url}?{query_string}"
                resp = doGet(url=full_url, headers=self.headers)

                if not resp:
                    continue
                if '教师未创建完成该测验' in resp:
                    loguru.logger.warning("教师未创建完成该测验，跳过")
                    return None

                # 优先尝试 JSON 解析
                try:
                    data = json.loads(resp)
                    if isinstance(data, dict) and data.get("questions"):
                        # 标准化题目结构，确保每题都有 answerField 和 type
                        type_map = _QUESTION_TYPE_MAP
                        normalized_questions = []
                        for q in data["questions"]:
                            q_id = str(q.get("id", ""))
                            q_type_code = str(q.get("type", q.get("questionType", "0")))
                            q_type = type_map.get(q_type_code, "single")

                            # 确保 answerField 存在
                            if "answerField" not in q or not isinstance(q["answerField"], dict):
                                q["answerField"] = {}
                            q["answerField"].setdefault(f"answer{q_id}", "")
                            q["answerField"].setdefault(f"answertype{q_id}", q_type_code)

                            # 确保 type 字段存在
                            q["type"] = q_type

                            # 确保 options 字段存在
                            if "options" not in q:
                                q["options"] = ""

                            normalized_questions.append(q)

                        data["questions"] = normalized_questions
                        question_ids = [q.get("id", "") for q in normalized_questions if q.get("id")]
                        data["answerwqbid"] = ",".join(str(qid) for qid in question_ids) + ","

                        # 确保必要的提交字段存在
                        data.setdefault("pyFlag", "")
                        data.setdefault("enc", self.enc)
                        data.setdefault("workId", self.workid.replace("work-", "") if self.workid else "")
                        data.setdefault("originJobId", self.jobid)
                        data.setdefault("knowledgeid", str(self.knowledgeid))
                        data.setdefault("cpi", str(self.cpi))
                        data.setdefault("clazzId", str(self.clazzId))
                        data.setdefault("courseid", str(self.courseId))
                        data.setdefault("ut", "s")
                        data.setdefault("mooc2", "1")
                        data.setdefault("api", "1")
                        data.setdefault("type", "")
                        data.setdefault("skipHeader", "true")
                        data.setdefault("needRedirect", "true")
                        data.setdefault("ktoken", self.ktoken)

                        loguru.logger.info(f"JSON获取成功，共 {len(normalized_questions)} 道题目")
                        return data
                except json.JSONDecodeError:
                    pass

                # 正则解析
                parsed = self._parse_response_text(resp)
                if parsed and parsed.get("questions"):
                    loguru.logger.info(f"正则解析成功，共 {len(parsed['questions'])} 道题目")
                    return parsed

                # HTML 解析
                if len(resp) > 10000 and ("singleQuesId" in resp or "Zy_TItle" in resp):
                    html = etree.HTML(resp)
                    questions = html.xpath("//div[contains(@class, 'singleQuesId')]")
                    if questions:
                        loguru.logger.info(f"HTML解析成功，共 {len(questions)} 道题目")
                        return self._parse_html(questions, html)

            except Exception as e:
                loguru.logger.debug(f"API调用失败: {e}")
                continue

        loguru.logger.warning("API获取失败，尝试备用方式...")

        # 备用方式
        for api_url in api_urls:
            try:
                home_url = f"{api_url.replace('/api/work', '/work/doHomeWorkNew')}?courseId={self.courseId}&workId={workId}&jobid={jobid}"
                resp = doGet(url=home_url, headers=self.headers)
                if not resp:
                    continue
                if "没有可处理的题目" in resp:
                    return None
                if "已完成" in resp:
                    return {"questions": []}

                parsed = self._parse_response_text(resp)
                if parsed and parsed.get("questions"):
                    return parsed

                html = etree.HTML(resp)
                questions = html.xpath("//div[contains(@class, 'singleQuesId')]")
                if questions:
                    return self._parse_html(questions, html)
            except Exception as e:
                loguru.logger.debug(f"备用方式失败: {e}")
                continue

        return None

    def _parse_response_text(self, response_text: str):
        """Parse an API response and extract question data."""
        from bs4 import BeautifulSoup
        
        try:
            soup = BeautifulSoup(response_text, "lxml")
        except Exception:
            return None

        form_tag = soup.find("form")
        if not form_tag:
            return None

        form_data = {}
        for inp in form_tag.find_all("input"):
            name = inp.attrs.get("name", "")
            if not name or "answer" in name:
                continue
            form_data[name] = inp.attrs.get("value", "")

        questions = []
        for div_tag in soup.find_all("div", class_="singleQuesId"):
            q_id = div_tag.attrs.get("data", "")
            timu_div = div_tag.find("div", class_="TiMu")
            q_type_code = timu_div.attrs.get("data", "0") if timu_div else "0"

            q_type = _QUESTION_TYPE_MAP.get(q_type_code, "single")

            title_div = div_tag.find("div", class_="Zy_TItle")
            q_title = title_div.get_text(strip=True) if title_div else ""
            q_title = re.sub(r'^\d+[\.\s、]+', '', q_title)

            options_ul = div_tag.find("ul")
            option_items = []
            if options_ul:
                for li in options_ul.find_all("li"):
                    aria_label = li.attrs.get("aria-label", "")
                    text = aria_label or li.get_text(strip=True)
                    option_items.append(text)
            options_str = "\n".join(option_items)

            questions.append({
                "id": q_id,
                "title": q_title,
                "type": q_type,
                "options": options_str,
                "answerField": {
                    f"answer{q_id}": "",
                    f"answertype{q_id}": q_type_code,
                },
            })

        question_ids = [q["id"] for q in questions]
        form_data["answerwqbid"] = ",".join(question_ids) + ","
        form_data["questions"] = questions
        form_data["pyFlag"] = form_data.get("pyFlag", "")

        return form_data

    def _parse_html(self, questions, html):
        """HTML 解析（保留 lxml 方式作为备用）"""
        result = []
        form_tag = html.xpath("//form")
        all_inputs = form_tag[0].xpath(".//input") if form_tag else html.xpath("//input[@type='hidden']")

        extra_fields = {}
        for inp in all_inputs:
            name = inp.get('name', '')
            value = inp.get('value', '')
            if name and "answer" not in name.lower():
                extra_fields[name] = value

        type_map = _QUESTION_TYPE_MAP

        for q_idx, question in enumerate(questions):
            q_id = ""
            title, options_str, q_type, answertype = "", "", "single", "0"

            try:
                data_attr = question.xpath("./@data")
                if data_attr:
                    q_id = str(data_attr[0])

                if not q_id:
                    inputs = question.xpath(".//input[@type='radio' or @type='checkbox']")
                    if inputs:
                        m = re.search(r'(\d+)', inputs[0].get('name', ''))
                        if m:
                            q_id = m.group(1)

                if not q_id:
                    q_id = str(q_idx + 1)

                timu_data = question.xpath(".//div[contains(@class, 'TiMu')]/@data")
                if timu_data:
                    answertype = str(timu_data[0])
                    q_type = type_map.get(answertype, "single")

                title_div = question.xpath(".//div[contains(@class, 'Zy_TItle')]")
                if title_div:
                    title = ''.join(title_div[0].xpath(".//text()")).strip()
                    title = re.sub(r'^\d+[\.\s、]+', '', title)

                options_list = []
                for li in question.xpath(".//ul/li"):
                    text = ''.join(li.xpath(".//text()")).strip()
                    if not text:
                        al = li.xpath("./@aria-label")
                        if al:
                            text = al[0]
                    options_list.append(f"{chr(65 + len(options_list))}. {text}")
                options_str = "\n".join(options_list)

            except Exception as e:
                loguru.logger.debug(f"解析题目失败: {e}")

            result.append({
                "id": q_id,
                "title": title,
                "type": q_type,
                "options": options_str,
                "answerField": {f"answer{q_id}": "", f"answertype{q_id}": answertype},
            })

        question_ids = [q["id"] for q in result]
        extra_fields["answerwqbid"] = ",".join(question_ids) + ","

        return {"questions": result, "pyFlag": "", **extra_fields}

    # ── 答案匹配 — adapted from reference implementation

    def _match_answer(self, q_type: str, answer_text: str, options_str: str) -> str:
        if not answer_text:
            return ""

        # 判断题
        if q_type == "judgement":
            ans_lower = str(answer_text).lower().strip()
            true_words = ["对", "正确", "true", "是", "√", "正确的", "正确答案", "ture"]
            false_words = ["错", "错误", "false", "否", "×", "不正确", "错误的", "fales", "fasle"]
            for t in true_words:
                if t in ans_lower:
                    return "true"
            for f in false_words:
                if f in ans_lower:
                    return "false"
            # 字母匹配：A=对 B=错
            letters = _extract_letters(answer_text)
            if letters:
                if letters[0] in ("A", "T"):
                    return "true"
                if letters[0] in ("B", "F"):
                    return "false"
            return "true" if random.choice([True, False]) else "false"

        if q_type in ("completion", "shortanswer"):
            return str(answer_text)

        options_list = _multi_cut(options_str) if options_str else []
        if not options_list:
            return ""

        structured = [{'letter': chr(65 + i), 'text': opt} for i, opt in enumerate(options_list)]

        # ── 第1层：字母直接匹配（最快、最准）──
        letters = _extract_letters(answer_text)
        valid_letters = [l for l in letters if ord(l) - 65 < len(structured)]
        
        if q_type == "single" and valid_letters:
            loguru.logger.info(f"      [字母匹配] 答案: {valid_letters[0]}")
            return valid_letters[0]
        
        if q_type == "multiple" and valid_letters and len(valid_letters) >= 2:
            result = "".join(sorted(set(valid_letters)))
            loguru.logger.info(f"      [字母匹配] 答案: {result}")
            return result

        # ── 第2层：子序列匹配（精确文本匹配）──
        if q_type == "multiple":
            res_list = _multi_cut(answer_text)
            answer = ""
            for ans_item in res_list:
                ans_cleaned = _clean_res([ans_item])[0]
                ans_norm = _normalize_text(ans_cleaned)
                if not ans_norm:
                    continue
                matched = False
                for opt in structured:
                    opt_norm = _normalize_text(opt['text'])
                    if not opt_norm:
                        continue
                    # 双向子序列检查
                    if _is_subsequence(ans_norm, opt_norm) or _is_subsequence(opt_norm, ans_norm):
                        if opt['letter'] not in answer:
                            answer += opt['letter']
                        matched = True
                        break
                if not matched and ans_norm:
                    # 第3层：高相似度匹配
                    best = _best_option_by_similarity(ans_cleaned, structured, threshold=0.75)
                    if best and best not in answer:
                        answer += best
                    else:
                        # 第4层：中相似度兜底
                        best2 = _best_option_by_similarity(ans_cleaned, structured, threshold=0.5)
                        if best2 and best2 not in answer:
                            loguru.logger.info(f"      [低相似度兜底] 匹配: {best2}")
                            answer += best2
            if answer:
                return "".join(sorted(set(answer)))
            # 多选题如果完全匹配不到，回退到字母匹配（即使只有1个字母）
            if valid_letters:
                return "".join(sorted(set(valid_letters)))
            return ""

        elif q_type == "single":
            t_res = _clean_res([answer_text])
            ans_norm = _normalize_text(t_res[0]) if t_res else ""
            
            if ans_norm:
                for opt in structured:
                    opt_norm = _normalize_text(opt['text'])
                    if not opt_norm:
                        continue
                    # 双向子序列
                    if _is_subsequence(ans_norm, opt_norm) or _is_subsequence(opt_norm, ans_norm):
                        loguru.logger.info(f"      [文本匹配] 答案: {opt['letter']}")
                        return opt['letter']
                
                # 高相似度
                best = _best_option_by_similarity(t_res[0], structured, threshold=0.7)
                if best:
                    return best
                
                # 中相似度兜底
                best2 = _best_option_by_similarity(t_res[0], structured, threshold=0.45)
                if best2:
                    loguru.logger.info(f"      [低相似度兜底] 匹配: {best2}")
                    return best2
            
            # 最后回退：字母匹配
            if valid_letters:
                return valid_letters[0]
            
            return ""

        else:
            return str(answer_text)

    # ── 提交 — adapted from reference implementation

    def _submit_answers(self, questions_data: dict) -> bool:
        submit_data = dict(questions_data)
        if "questions" in submit_data:
            del submit_data["questions"]

        if not submit_data.get("enc") and self.enc:
            submit_data["enc"] = self.enc

        if self.workid and "workId" not in submit_data:
            submit_data["workId"] = self.workid
        if self.jobid and "originJobId" not in submit_data:
            submit_data["originJobId"] = self.jobid

        answer_count = sum(1 for k in submit_data if k.startswith('answer') and not k.startswith('answertype'))
        loguru.logger.info(f"准备提交 {answer_count} 道题目, answerwqbid={submit_data.get('answerwqbid', '')}")
        loguru.logger.info(f"提交数据关键字段: workId={submit_data.get('workId', '')}, originJobId={submit_data.get('originJobId', '')}, pyFlag={submit_data.get('pyFlag', '')}, enc={'有' if submit_data.get('enc') else '无'}")

        if answer_count == 0:
            loguru.logger.warning("没有答案要提交")
            return False

        # 打印前3道题的答案用于调试
        debug_count = 0
        for k, v in sorted(submit_data.items()):
            if k.startswith('answer') and not k.startswith('answertype'):
                loguru.logger.debug(f"  {k} = {str(v)[:80]}")
                debug_count += 1
                if debug_count >= 3:
                    break

        submit_headers = {
            "Host": "mooc1.chaoxing.com",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://mooc1.chaoxing.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
        }

        if self.headers and isinstance(self.headers, dict):
            for k, v in self.headers.items():
                if k.lower() != "cookie":
                    submit_headers[k] = v

        submit_urls = [
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            "https://mooc1-1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            "https://mooc1-2.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            "https://mooc2-ans.chaoxing.com/mooc-ans/work/addStudentWorkNew",
        ]

        from urllib.parse import urlparse
        for submit_url in submit_urls:
            try:
                loguru.logger.info(f"提交到 {submit_url} ...")

                parsed_url = urlparse(submit_url)
                cur_headers = dict(submit_headers)
                cur_headers["Host"] = parsed_url.netloc
                cur_headers["Origin"] = f"{parsed_url.scheme}://{parsed_url.netloc}"

                resp = ses.post(
                    submit_url,
                    data=submit_data,
                    headers=cur_headers,
                    timeout=30,
                    allow_redirects=True,
                )
                if resp.status_code != 200:
                    loguru.logger.warning(f"提交 HTTP {resp.status_code}: {resp.text[:300]}")
                    continue

                result = resp.json()
                loguru.logger.debug(f"提交结果: {json.dumps(result, ensure_ascii=False)}")

                if result.get("status"):
                    loguru.logger.info(f"提交成功 -> {result.get('msg', '')}")
                    return True
                else:
                    msg = result.get('msg', '')
                    loguru.logger.error(f"提交失败 -> {msg}")
                    if "无效的参数" in msg or "code-2" in msg:
                        continue
                    return False

            except Exception as e:
                loguru.logger.warning(f"提交异常 ({submit_url}): {e}")
                continue

        loguru.logger.error("所有提交域名都失败了")
        return False

    # ── 主流程 — adapted from reference implementation

    def do_finish(self):
        if not self.workid:
            loguru.logger.warning("未找到 workid，跳过")
            return True

        questions_data = self._fetch_questions_from_api()
        
        # None = fetching failed entirely
        if questions_data is None:
            loguru.logger.error(f"作业 '{self.title}' 获取题目失败")
            return False
        
        # Empty questions = truly no questions or already done
        if not questions_data.get("questions"):
            loguru.logger.info(f"作业 '{self.title}' 无题目或已完成")
            return True

        questions_data = copy.deepcopy(questions_data)
        questions = questions_data["questions"]
        loguru.logger.info(f"作业 '{self.title}' 共 {len(questions)} 道题目")

        # 题库 / AI 模式选择
        tiku = self._tiku
        if tiku and not tiku.DISABLE:
            loguru.logger.info("使用题库系统答题")
            try:
                if not hasattr(tiku, '_initialized'):
                    tiku.init_tiku()
                    tiku._initialized = True
            except Exception as e:
                loguru.logger.debug(f"题库初始化失败: {e}")
                tiku = None
        elif self.strategy == "ai":
            ai = self._get_ai_client()
            if ai and ai.is_configured():
                loguru.logger.info("使用 DeepSeek AI 答题")
            else:
                loguru.logger.info("AI未配置，回退到选第一个策略")
                self.strategy = "first"

        found_answers = 0

        for q in questions:
            q_id = q.get("id", "")
            q_title = q.get("title", "")
            q_type = q.get("type", "single")
            q_options = q.get("options", "")

            loguru.logger.info(f"  题目: {q_title[:50]}{'...' if len(q_title) > 50 else ''}")

            answer, answer_source = "", "random"

            # 1) 题库
            if tiku and not tiku.DISABLE and not answer:
                try:
                    tiku_answer = tiku.query({"title": q_title, "type": q_type, "options": q_options})
                    if tiku_answer:
                        loguru.logger.info(f"      [题库] 找到: {str(tiku_answer)[:50]}")
                        # 非选择题（填空/主观题）走统一答题链路，复用已查询结果避免重复请求题库；
                        # 选择题走选项字母匹配
                        if q_type in NON_CHOICE_TYPES:
                            matched, _src = answer_non_question(
                                q_type, q_title,
                                ai_client=(self._get_ai_client() if self.strategy == "ai" else None),
                                tiku=tiku,
                                tiku_answer=str(tiku_answer),
                                use_ai=(self.strategy == "ai"),
                            )
                        else:
                            matched = self._match_answer(q_type, str(tiku_answer), q_options)
                        if matched:
                            answer = matched
                            answer_source = "tiku"
                            found_answers += 1
                        else:
                            loguru.logger.warning("      [题库] 答案未能匹配选项")
                except Exception as e:
                    loguru.logger.debug(f"题库查询失败: {e}")

            # 2) AI
            if not answer and self.strategy == "ai":
                ai_client = self._get_ai_client()
                if ai_client and ai_client.is_configured() and q_type in NON_CHOICE_TYPES:
                    # 非选择题（简答/名词解释/论述/计算/案例分析）：模板化提示词，
                    # 单次内容接口作答（题库未命中或题库参考过短场景）
                    try:
                        matched, _src = answer_non_question(
                            q_type, q_title, ai_client=ai_client, use_ai=True
                        )
                        if matched:
                            answer, answer_source = matched, "ai_content"
                            found_answers += 1
                            loguru.logger.info(f"      [AI内容] 匹配: {answer[:50]}")
                    except Exception as e:
                        loguru.logger.warning(f"AI非选择题作答失败: {e}")

                if ai_client and ai_client.is_configured() and q_type not in NON_CHOICE_TYPES:
                    options_list = _multi_cut(q_options) if q_options else []
                    loguru.logger.debug(f"      [AI] 开始答题，题型={q_type}, 选项数={len(options_list) if options_list else 0}")
                    try:
                        if hasattr(ai_client, 'answer_question_content'):
                            ai_res_list = ai_client.answer_question_content({
                                'question_type': q_type,
                                'title': q_title,
                                'options': options_list,
                            })
                            loguru.logger.debug(f"      [AI内容] 返回: {ai_res_list}")
                            if ai_res_list:
                                ans_text = str(ai_res_list[0]) if isinstance(ai_res_list, list) else str(ai_res_list)
                                loguru.logger.debug(f"      [AI内容] 待匹配答案: {ans_text[:100]}")
                                matched = self._match_answer(q_type, ans_text, q_options)
                                loguru.logger.debug(f"      [AI内容] 匹配结果: {matched}")
                                if matched:
                                    answer, answer_source = matched, "ai_content"
                                    found_answers += 1
                                    loguru.logger.info(f"      [AI内容] 匹配: {answer}")
                    except Exception as e:
                        loguru.logger.warning(f"AI内容匹配失败: {e}")
                        import traceback
                        loguru.logger.debug(traceback.format_exc())

                    # 内容接口失败时以字母接口兜底（仅选择题走到此处；减少无效大模型调用）
                    if not answer:
                        try:
                            ai_res = ai_client.answer_question({
                                'question_type': q_type, 'title': q_title, 'options': options_list
                            })
                            loguru.logger.debug(f"      [AI字母] 返回: {ai_res}")
                            if ai_res:
                                matched = self._match_answer(q_type, str(ai_res), q_options)
                                loguru.logger.debug(f"      [AI字母] 匹配结果: {matched}")
                                if matched:
                                    answer, answer_source = matched, "ai"
                                    found_answers += 1
                                    loguru.logger.info(f"      [AI字母] 匹配: {answer}")
                        except Exception as e:
                            loguru.logger.warning(f"AI字母匹配失败: {e}")
                            import traceback
                            loguru.logger.debug(traceback.format_exc())

                if not answer:
                    loguru.logger.warning("      AI未返回有效答案")

            # 3) 兜底：非选择题用占位文本（可后续补充），选择题随机作答
            if not answer:
                if q_type in NON_CHOICE_TYPES:
                    answer = FALLBACK_TEXT
                else:
                    answer = _random_answer(q_options, q_type)
                loguru.logger.info(f"      兜底作答: {answer}")

            # 填写答案
            answer_field = q.get("answerField", {})
            answer_field[f"answer{q_id}"] = answer
            q["answerField"] = answer_field
            q[f"answerSource{q_id}"] = answer_source

        # 覆盖率 / pyFlag
        if questions:
            cover_rate = (found_answers / len(questions)) * 100
            loguru.logger.info(f"题库覆盖率: {cover_rate:.0f}%")

        # 本地答题记录（进度 / 错题集），失败不影响答题主流程
        try:
            from answer_record import get_answer_record_dao
            get_answer_record_dao().record_from_questions(str(self.courseId), self.workid, questions)
        except Exception as e:
            loguru.logger.debug(f"答题记录保存失败: {e}")

        from config import GloConfig
        cover_threshold = GloConfig.data.get("FunConfig", {}).get("deal-mission", {}).get("cover-rate", 0.6) * 100
        submit_mode = GloConfig.data.get("FunConfig", {}).get("deal-mission", {}).get("submit-mode", "auto")
        tiku_enabled = tiku and not tiku.DISABLE

        if submit_mode == "save":
            questions_data["pyFlag"] = "1"
        elif tiku_enabled:
            questions_data["pyFlag"] = "" if cover_rate >= cover_threshold else "1"
        else:
            questions_data["pyFlag"] = ""

        # 组建扁平化提交表单
        for q in questions_data["questions"]:
            q_id = q["id"]
            af = q.get("answerField", {})
            ans = af.get(f"answer{q_id}", "")
            atype = af.get(f"answertype{q_id}", "0")

            if questions_data["pyFlag"] == "1":
                src = q.get(f"answerSource{q_id}", "")
                if src not in ("cover", "tiku", "ai", "ai_content"):
                    ans = ""

            questions_data[f"answer{q_id}"] = ans
            questions_data[f"answertype{q_id}"] = atype

        submit_data = dict(questions_data)
        submit_data.pop("questions", None)

        answer_count = sum(1 for k in submit_data if k.startswith('answer') and not k.startswith('answertype'))
        loguru.logger.info(f"准备提交 {answer_count} 道题目")

        if self._submit_answers(submit_data):
            loguru.logger.info(f"作业 '{self.title}' 提交成功")
            return True
        else:
            loguru.logger.error(f"作业 '{self.title}' 提交失败")
            return False
