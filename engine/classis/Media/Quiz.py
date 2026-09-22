# _*_ coding:utf-8 _*_
# author: 集成版
import json
import random
import re
from difflib import SequenceMatcher

import loguru
from lxml import etree

from classis.Media import Media
from utils import doGet, doPost
from non_choice import (
    SUBJECTIVE_TYPE_MAP,
    TYPE_LABELS,
    answer_non_question,
)


def _normalize_text(text: str) -> str:
    """文本规范化，用于相似度匹配"""
    if not isinstance(text, str):
        text = str(text)
    char_map = str.maketrans({
        '⻛': '风', '⻔': '门', '⻋': '车', '⻢': '马',
    })
    normalized = text.translate(char_map)
    normalized = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', normalized)
    normalized = re.sub(r'\s+', '', normalized)
    normalized = re.sub(r'[，。！？；：,.!?;:()（）\[\]【】""''_/\\|-]', '', normalized)
    return normalized.lower()


def _best_option_by_similarity(target: str, options: list, threshold: float = 0.8) -> str:
    """Return the best-matching option for a target string."""
    if not target or not options:
        return ""
    target_norm = _normalize_text(target)
    if not target_norm:
        return ""

    best_letter = ""
    best_score = 0.0
    for opt in options:
        opt_text = opt.get('text', '') if isinstance(opt, dict) else str(opt)
        opt_norm = _normalize_text(opt_text)
        if not opt_norm:
            continue
        score = SequenceMatcher(None, target_norm, opt_norm).ratio()
        if score > best_score:
            best_score = score
            best_letter = opt.get('letter', '') if isinstance(opt, dict) else ''

    if best_score >= threshold:
        loguru.logger.info(f"      [相似度兜底] 匹配成功: {best_letter} (score={best_score:.2f})")
        return best_letter
    return ""


def _is_subsequence(a, o):
    """Check whether *a* is a subsequence of *o*."""
    iter_o = iter(o)
    return all(c in iter_o for c in a)


def _clean_res(res):
    """Normalize and de-duplicate result entries."""
    cleaned_res = []
    if isinstance(res, str):
        res = [res]
    for c in res:
        cleaned = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*|[.,!?;:，。！？；：]', '', c) if len(c) > 1 else c
        cleaned_res.append(cleaned.strip())
    return cleaned_res


class Quiz(Media):
    def __init__(self, attachment: dict, headers, defaults: dict, courseId: str, ai_client=None, strategy: str = "first", tiku=None):
        super().__init__(attachment, headers)
        self._original_headers = dict(headers)
        self.defaults = defaults
        self.courseId = courseId
        self.workid = attachment.get("property", {}).get("workid", "") or attachment.get("workid", "")
        self.title = attachment.get("property", {}).get("title", "作业")
        self.enc = attachment.get("enc", "")
        self.work_enc = ""
        self._ai_client = ai_client
        # 题库答题模式：题库优先，选择题按首选策略兜底（与 AI 答题路径相互独立）
        if strategy == "tiku":
            strategy = "first"
        self.strategy = strategy
        self._tiku = tiku
        self.questions = []

    def get_status(self) -> dict:
        quiz_urls = [
            f"https://mooc1-1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId={self.courseId}&workId={self.workid}&jobid={self.jobid}",
            f"https://mooc1-2.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId={self.courseId}&workId={self.workid}&jobid={self.jobid}",
            f"https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId={self.courseId}&workId={self.workid}&jobid={self.jobid}",
        ]

        _headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'X-Requested-With': 'XMLHttpRequest'
        }
        _headers.update(self._original_headers)
        loguru.logger.debug(f"请求头 Cookie: {_headers.get('Cookie', '')[:100]}...")

        for url in quiz_urls:
            try:
                response = doGet(url=url, headers=_headers)
                if response:
                    if len(response) < 2000:
                        loguru.logger.debug(f"作业页面内容(前1000字): {response[:1000]}")
                    status = self._parse_status(response)
                    if status:
                        return status
            except Exception as e:
                loguru.logger.debug(f"获取作业状态失败: {e}")
                continue

        return {"completed": False, "questions": 0, "message": "无法获取作业状态"}

    def _parse_status(self, response_text: str) -> dict:
        loguru.logger.debug(f"开始解析作业页面，长度: {len(response_text)}")
        loguru.logger.debug(f"页面预览 (前1000字): {response_text[:1000]}")
        
        if "没有可处理的题目" in response_text:
            loguru.logger.debug("页面显示: 没有可处理的题目")
            return {"completed": True, "questions": 0, "message": "没有可处理的题目"}

        if "用户登录" in response_text:
            loguru.logger.warning("页面显示: 用户登录页面（Cookie 可能已过期或无效）")
            return None

        if "已完成" in response_text:
            loguru.logger.debug("页面显示: 已完成")
            return {"completed": True, "questions": 0, "message": "作业已完成"}

        if "workEnc" in response_text:
            work_enc_match = re.search(r'workEnc\s*=\s*"([^"]+)"', response_text)
            self.work_enc = work_enc_match.group(1) if work_enc_match else ""
            loguru.logger.debug(f"获取到 workEnc: {self.work_enc[:20] if self.work_enc else '空'}...")

            try:
                html = etree.HTML(response_text)
                questions = html.xpath(
                    "//div[contains(@class, 'MuQuesTi') or contains(@class, 'question') or contains(@class, 'QuesTi') or contains(@class, 'TiMu') or contains(@class, 'ques')]"
                )
                self.questions = questions

                loguru.logger.debug(f"使用XPath检测到题目数量: {len(questions)}")

                if questions:
                    return {
                        "completed": False,
                        "questions": len(questions),
                        "work_enc": self.work_enc,
                        "message": f"检测到 {len(questions)} 道题目"
                    }
                else:
                    loguru.logger.debug("未检测到题目元素，尝试查找其他格式...")
                    radios = html.xpath("//input[@type='radio']")
                    checkboxes = html.xpath("//input[@type='checkbox']")
                    loguru.logger.debug(f"页面中 radio 数量: {len(radios)}, checkbox 数量: {len(checkboxes)}")
                    
                    if radios or checkboxes:
                        loguru.logger.debug("页面中有选择题选项，可能是不同的页面结构")
                        return {
                            "completed": False,
                            "questions": 1,
                            "work_enc": self.work_enc,
                            "message": "检测到选择题选项"
                        }
            except Exception as e:
                loguru.logger.debug(f"解析HTML时出错: {e}")
                pass

        loguru.logger.debug("无法解析作业状态")
        return None

    def do_finish(self) -> bool:
        status = self.get_status()
        loguru.logger.debug(f"作业状态: {status}")

        if status.get("completed"):
            loguru.logger.info(f"作业 '{self.title}' 已完成，跳过 (原因: {status.get('message')})")
            return True

        questions_count = status.get("questions", 0)
        if questions_count == 0:
            loguru.logger.warning(f"作业 '{self.title}' 没有检测到题目，状态: {status.get('message')}")
            loguru.logger.warning(f"   workid: {self.workid}, jobid: {self.jobid}")
            return True

        loguru.logger.info(f"作业 '{self.title}' 共 {questions_count} 道题目")
        loguru.logger.info(f"   已解析 questions 数量: {len(self.questions)}")

        tiku = self._tiku
        if tiku and not tiku.DISABLE:
            loguru.logger.info(f"   使用题库系统答题")
            try:
                if not hasattr(tiku, '_initialized'):
                    tiku.init_tiku()
                    tiku._initialized = True
            except Exception as e:
                loguru.logger.debug(f"题库初始化失败: {e}")
                tiku = None
        else:
            if self.strategy == "ai":
                ai = self._ai_client
                if ai and ai.is_configured():
                    loguru.logger.info("   使用 DeepSeek AI 答题")
                else:
                    loguru.logger.info("   AI未配置，回退到选第一个策略")
                    self.strategy = "first"
            elif self.strategy == "random":
                loguru.logger.info("   使用随机选择策略")
            else:
                loguru.logger.info("   使用选第一个策略")

        answers_result = self._generate_answers(tiku)
        answers = answers_result.get('answers', {})
        question_ids = answers_result.get('question_ids', [])

        if not answers:
            loguru.logger.info(f"作业 '{self.title}' 没有可提交的答案")
            return True

        return self._submit_answers(answers, question_ids)

    def _generate_answers(self, tiku=None) -> dict:
        answers = {}
        question_ids = []

        for q_idx, question in enumerate(self.questions):
            q_info = self._extract_question_info(question, q_idx)
            radios = q_info['radios']
            checkboxes = q_info['checkboxes']
            text_inputs = q_info['text_inputs']
            textareas = q_info['textareas']

            if not radios and not checkboxes and not text_inputs and not textareas:
                continue

            question_id = q_info.get('question_id', str(q_idx + 1))
            question_ids.append(question_id)
            loguru.logger.info(f"   题目 {q_idx + 1} (ID: {question_id}, {q_info['question_type']}): {q_info['title'][:50]}...")

            # 非选择题（填空 / 简答）走独立答题链路
            if not radios and not checkboxes:
                self._fill_non_choice(q_info, tiku, answers)
                continue

            q_type = "judgement" if q_info['is_true_false'] else ("multiple" if checkboxes else "single")

            selected = None

            if tiku and not tiku.DISABLE:
                loguru.logger.info(f"      [题库模式] 搜索题库...")
                try:
                    options_list = [opt['text'] for opt in q_info['options']]
                    tiku_answer = tiku.query({
                        "title": q_info['title'],
                        "type": q_type,
                        "options": options_list
                    })

                    if tiku_answer:
                        loguru.logger.info(f"      [题库模式] 找到答案: {tiku_answer[:50] if len(tiku_answer) > 50 else tiku_answer}")

                        structured_options = q_info['options']
                        answer = ""

                        if q_type == "multiple":
                            res_list = tiku_answer.split('#') if '#' in tiku_answer else [tiku_answer]
                            for _a in _clean_res(res_list):
                                ans_cleaned = _a
                                matched = False
                                for opt in structured_options:
                                    if _is_subsequence(ans_cleaned, opt['text']):
                                        answer += opt['letter']
                                        matched = True
                                        break
                                if not matched and ans_cleaned:
                                    best_letter = _best_option_by_similarity(ans_cleaned, structured_options, threshold=0.8)
                                    if best_letter and best_letter not in answer:
                                        answer += best_letter
                            answer = "".join(sorted(set(answer)))
                        elif q_type == "single":
                            t_res = _clean_res([tiku_answer])
                            matched = False
                            for opt in structured_options:
                                if _is_subsequence(t_res[0] if t_res else tiku_answer, opt['text']):
                                    answer = opt['letter']
                                    matched = True
                                    break
                            if not answer and t_res:
                                answer = _best_option_by_similarity(t_res[0], structured_options, threshold=0.8)
                        elif q_type == "judgement":
                            if tiku and hasattr(tiku, 'judgement_select'):
                                answer = "true" if tiku.judgement_select(tiku_answer) else "false"
                            else:
                                true_list = ["对", "正确", "true", "是", "√", "1", "a"]
                                false_list = ["错", "错误", "false", "否", "×", "0", "b"]
                                ans_lower = str(tiku_answer).lower()
                                if any(t in ans_lower for t in true_list):
                                    answer = "true"
                                elif any(f in ans_lower for f in false_list):
                                    answer = "false"
                                else:
                                    answer = "true" if random.choice([True, False]) else "false"
                        else:
                            answer = tiku_answer

                        if answer:
                            loguru.logger.info(f"      [题库模式] 匹配成功：{answer}")
                            selected = self._select_by_answer_letters(q_info, answer)
                except Exception as e:
                    loguru.logger.debug(f"      [题库模式] 搜索失败: {e}")

            if not selected and self.strategy == "ai":
                loguru.logger.info(f"      [AI模式] 调用DeepSeek AI答题...")
                selected = self._select_by_ai(q_info)
                if selected:
                    answers.update(selected)
                    continue
                else:
                    loguru.logger.info(f"      [AI模式] AI未返回有效答案，回退到选第一个策略")

            if selected:
                answers.update(selected)
                continue

            if radios:
                if self.strategy == "random":
                    import random
                    selected = random.choice(radios)
                else:
                    selected = radios[0]
                qname = selected.get('name', f'q{q_idx}')
                qvalue = selected.get('value', '')
                answers[qname] = qvalue
                loguru.logger.info(f"      选择: {qvalue}")

            elif checkboxes:
                if self.strategy == "first":
                    for cb in checkboxes:
                        qname = cb.get('name', f'q{q_idx}')
                        qvalue = cb.get('value', '')
                        answers[qname] = qvalue
                    loguru.logger.info(f"      选择: 全部选项")
                else:
                    import random
                    if checkboxes:
                        count = max(1, random.randint(1, len(checkboxes)))
                        selected = random.sample(checkboxes, count)
                        for s in selected:
                            qname = s.get('name', f'q{q_idx}')
                            qvalue = s.get('value', '')
                            answers[qname] = qvalue
                        loguru.logger.info(f"      选择: 随机 {count} 个选项")

        return {
            'answers': answers,
            'question_ids': question_ids
        }

    def _select_by_answer_letters(self, q_info: dict, answer_letters: str) -> dict:
        """根据答案字母选择选项"""
        result = {}
        options = q_info['options']
        
        for letter in answer_letters:
            for opt in options:
                if opt['letter'] == letter.upper():
                    result[opt['name']] = opt['value']
                    break

        if answer_letters in ["true", "false"]:
            for opt in options:
                opt_lower = opt['text'].lower()
                if answer_letters == "true":
                    if opt_lower in ['对', '正确', 'true', '是', '√'] or '对' in opt['text']:
                        result[opt['name']] = opt['value']
                        break
                else:
                    if opt_lower in ['错', '错误', 'false', '否', '×'] or '错' in opt['text']:
                        result[opt['name']] = opt['value']
                        break

        return result

    def _extract_question_info(self, question, q_idx: int) -> dict:
        radios = question.xpath(".//input[@type='radio']")
        checkboxes = question.xpath(".//input[@type='checkbox']")
        text_inputs_elems = question.xpath(".//input[@type='text']")
        textarea_elems = question.xpath(".//textarea")

        title = ""
        options = []
        is_true_false = False
        question_id = str(q_idx + 1)
        type_code = ""

        try:
            # 题型代码：题目节点自身可能是 div.TiMu（@data 即题型码），
            # 也可能是外层包裹节点（题型码在内层 TiMu 上）
            own_data = question.xpath("./@data")
            own_data_val = str(own_data[0]) if own_data else ""
            if own_data_val in ("0", "1", "2", "3", "4", "5", "6", "7", "8"):
                type_code = own_data_val
            if not type_code:
                inner_codes = question.xpath(".//div[contains(@class, 'TiMu')]/@data")
                if inner_codes and str(inner_codes[0]) in ("0", "1", "2", "3", "4", "5", "6", "7", "8"):
                    type_code = str(inner_codes[0])

            # 题目 ID：优先从答案控件名（answer{qid}）中提取，避免把题型码误当题目 ID
            answer_elems = radios or checkboxes or text_inputs_elems
            if answer_elems:
                name_match = re.search(r'(\d+)', answer_elems[0].get('name', ''))
                if name_match:
                    question_id = name_match.group(1)
                    loguru.logger.debug(f"题目 #{q_idx} 从控件名提取ID: {question_id}")

            if question_id == str(q_idx + 1):
                if own_data_val.isdigit() and own_data_val not in ("0", "1", "2", "3", "4", "5", "6", "7", "8"):
                    question_id = own_data_val
                    loguru.logger.debug(f"题目 #{q_idx} data属性ID: {question_id}")
                elif textarea_elems:
                    name_match = re.search(r'(\d+)', textarea_elems[0].get('name', ''))
                    if name_match:
                        question_id = name_match.group(1)

            title_div = question.xpath(".//div[contains(@class, 'q_title') or contains(@class, 'QuesTiT') or contains(@class, 'quesTitle')]")
            if title_div:
                title_text = ''.join(title_div[0].xpath(".//text()")).strip()
                title = re.sub(r'^\d+[\.\s、]+', '', title_text)

            if not title:
                all_text = ''.join(question.xpath(".//text()")).strip()
                title = re.sub(r'^\d+[\.\s、]+', '', all_text[:200])

            input_elems = radios if radios else checkboxes
            for idx, inp in enumerate(input_elems):
                value = inp.get('value', '')
                option_text = ""

                parent = inp.getparent()
                if parent is not None:
                    siblings = parent.xpath(".//text()")
                    if siblings:
                        option_text = ''.join(siblings).strip()

                if not option_text:
                    label = inp.xpath("./following-sibling::*[1]//text() | ./following-sibling::text()[1]")
                    if label:
                        option_text = ''.join(label).strip()

                if idx == 0 and len(input_elems) == 2:
                    opt_lower = option_text.lower()
                    if opt_lower in ['对', '正确', 'true', '是', '√'] or '对' in option_text:
                        is_true_false = True

                options.append({
                    'value': value,
                    'text': option_text,
                    'letter': chr(65 + idx),
                    'name': inp.get('name', f'q{q_idx}')
                })

        except Exception as e:
            loguru.logger.debug(f"提取题目信息失败: {e}")

        # 非选择题控件清单
        text_inputs = [{
            'name': inp.get('name', f'q{q_idx}_text_{idx}'),
            'value': inp.get('value', ''),
        } for idx, inp in enumerate(text_inputs_elems)]
        textareas = [{
            'name': ta.get('name', f'q{q_idx}_textarea'),
        } for ta in textarea_elems]

        if type_code == "2":
            question_type = '填空题'
        elif type_code in SUBJECTIVE_TYPE_MAP:
            question_type = TYPE_LABELS.get(SUBJECTIVE_TYPE_MAP[type_code], '简答题')
        elif is_true_false:
            question_type = '判断题'
        elif checkboxes:
            question_type = '多选题'
        elif textareas:
            question_type = '简答题'
        elif text_inputs:
            question_type = '填空题'
        else:
            question_type = '单选题'

        return {
            'title': title,
            'options': options,
            'radios': radios,
            'checkboxes': checkboxes,
            'text_inputs': text_inputs,
            'textareas': textareas,
            'type_code': type_code,
            'is_true_false': is_true_false,
            'question_type': question_type,
            'question_id': question_id,
        }

    def _fill_non_choice(self, q_info: dict, tiku, answers: dict):
        """为填空题/简答题生成答案并映射到页面字段。

        - 填空题的多个文本框通常共用同一个 name（answer{qid}），浏览器提交同名字段；
          本方法用 list 承载同名字段，由 requests 编码为重复键；
          若各空 name 不同，则按空缺顺序一一映射。
        - 简答题通常为单个 textarea（或单个文本框），直接写入整段答案。
        """
        type_code = q_info.get('type_code', '')
        text_inputs = q_info.get('text_inputs', [])
        textareas = q_info.get('textareas', [])

        # 主观题（简答/名词解释/论述/计算/案例分析）按题型码分派；
        # 无题型码时按控件形态回退（textarea→简答，文本框→填空）
        if type_code in SUBJECTIVE_TYPE_MAP:
            q_type = SUBJECTIVE_TYPE_MAP[type_code]
        elif textareas:
            q_type = "shortanswer"
        else:
            q_type = "completion"

        answer, source = answer_non_question(
            q_type,
            q_info['title'],
            ai_client=self._ai_client,
            tiku=tiku,
            use_ai=(self.strategy == "ai"),
        )
        loguru.logger.info(f"      [{source}] {q_info['question_type']}答案: {str(answer)[:60]}")

        if q_type == "completion":
            if not text_inputs:
                return
            blanks = answer.split("\n") if answer else [""]
            names = [inp['name'] for inp in text_inputs]
            distinct_names = list(dict.fromkeys(names))

            if len(distinct_names) == len(names):
                # 各空字段名不同：按位置映射，不足补空，超出截断
                values = (blanks + [""] * (len(names) - len(blanks)))[:len(names)]
                for name, value in zip(names, values):
                    answers[name] = value
            else:
                # 同名字段：单空写标量，多空写 list（requests 会编码为重复键）
                shared_name = names[0]
                answers[shared_name] = blanks[0] if len(blanks) == 1 else blanks
        else:
            if textareas:
                answers[textareas[0]['name']] = answer
            elif text_inputs:
                answers[text_inputs[0]['name']] = answer

    def _select_by_ai(self, q_info: dict) -> dict:
        if not self._ai_client or not self._ai_client.is_configured():
            loguru.logger.warning(f"      [AI模式] AI客户端未配置，跳过AI答题")
            return None

        options_text = [opt['text'] for opt in q_info['options']]
        if not options_text:
            loguru.logger.warning(f"      [AI模式] 没有检测到选项，跳过AI答题")
            return None

        loguru.logger.info(f"      [AI模式] 题目: {q_info['title'][:40]}{'...' if len(q_info['title']) > 40 else ''}")
        loguru.logger.info(f"      [AI模式] 选项数: {len(options_text)}")

        selected = {}
        selected_letters = []

        # 优先尝试增强版内容匹配（adapted from reference implementation
        try:
            if hasattr(self._ai_client, 'answer_question_content'):
                loguru.logger.info(f"      [AI模式] 使用内容匹配策略")
                ai_res_list = self._ai_client.answer_question_content({
                    'question_type': q_info['question_type'],
                    'title': q_info['title'],
                    'options': options_text
                })

                if ai_res_list and isinstance(ai_res_list, list):
                    loguru.logger.info(f"      [AI模式] AI返回内容: {ai_res_list}")

                    for ans_content in ai_res_list:
                        ans_cleaned = _clean_res([ans_content])[0] if ans_content else ""
                        matched = False
                        for opt in q_info['options']:
                            if _is_subsequence(ans_cleaned, opt['text']):
                                if opt['name'] not in selected:
                                    selected[opt['name']] = opt['value']
                                    selected_letters.append(f"{opt['letter']} ({opt['text'][:20]})")
                                    matched = True
                                    break
                        if not matched and ans_cleaned:
                            best_letter = _best_option_by_similarity(ans_cleaned, q_info['options'], threshold=0.8)
                            if best_letter:
                                for opt in q_info['options']:
                                    if opt['letter'] == best_letter and opt['name'] not in selected:
                                        selected[opt['name']] = opt['value']
                                        selected_letters.append(f"{opt['letter']} ({opt['text'][:20]})")
                                        break

                    if selected:
                        loguru.logger.info(f"      [AI模式] 内容匹配选项: {', '.join(selected_letters)}")
                        return selected
                    else:
                        loguru.logger.warning(f"      [AI模式] 内容未能匹配，回退到字母匹配")
        except Exception as e:
            loguru.logger.debug(f"      [AI模式] 内容匹配失败: {e}")

        # 回退到旧版字母匹配
        ai_answer = self._ai_client.answer_question({
            'question_type': q_info['question_type'],
            'title': q_info['title'],
            'options': options_text
        })

        if not ai_answer:
            loguru.logger.warning(f"      [AI模式] AI返回空答案")
            return None

        loguru.logger.info(f"      [AI模式] AI返回字母: '{ai_answer}'")

        for opt in q_info['options']:
            if opt['letter'] in ai_answer.upper():
                selected[opt['name']] = opt['value']
                selected_letters.append(f"{opt['letter']} ({opt['text'][:20]})")

        if selected:
            loguru.logger.info(f"      [AI模式] 匹配选项: {', '.join(selected_letters)}")
        else:
            loguru.logger.warning(f"      [AI模式] 无法匹配AI答案中的选项: {ai_answer}")

        if not selected:
            loguru.logger.info(f"      [AI模式] 使用默认选项: {q_info['options'][0]['letter']}")
            selected[q_info['options'][0]['name']] = q_info['options'][0]['value']

        return selected

    def _submit_answers(self, answers: dict, question_ids: list) -> bool:
        loguru.logger.info(f"准备提交 {len(answers)} 个答案，共 {len(question_ids)} 道题目")
        
        answerwqbid = ",".join(question_ids) + "," if question_ids else ""
        loguru.logger.info(f"answerwqbid: {answerwqbid}")

        submit_urls = [
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            "https://mooc1-1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            "https://mooc1-2.chaoxing.com/mooc-ans/work/addStudentWorkNew",
        ]

        submit_data = {
            'courseId': self.courseId,
            'classId': self.defaults.get("clazzId", ""),
            'workId': self.workid,
            'jobid': self.jobid,
            'workEnc': self.work_enc,
            'pyFlag': '',
        }
        
        if self.enc:
            submit_data['enc'] = self.enc
            loguru.logger.info(f"使用 enc 值: {self.enc[:20]}...")
        
        if self.jobid and 'originJobId' not in submit_data:
            submit_data['originJobId'] = self.jobid
            loguru.logger.info(f"添加 originJobId 字段: {self.jobid}")
        
        if answerwqbid:
            submit_data['answerwqbid'] = answerwqbid
            loguru.logger.info(f"添加 answerwqbid 字段: {answerwqbid}")

        for key, value in answers.items():
            submit_data[key] = value

        loguru.logger.debug(f"提交字段: {list(submit_data.keys())}")
        loguru.logger.debug(f"答案字段数: {len(answers)}")

        from urllib.parse import urlparse
        
        for submit_url in submit_urls:
            parsed_url = urlparse(submit_url)
            origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            _headers = {
                "Host": parsed_url.netloc,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": origin,
                "Referer": origin,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            }
            _headers.update(self.headers)

            try:
                loguru.logger.info(f"尝试提交到: {submit_url}")
                submit_rsp = doPost(url=submit_url, data=submit_data, headers=_headers)
                
                if not submit_rsp:
                    loguru.logger.warning(f"提交响应为空，尝试下一个URL...")
                    continue
                
                loguru.logger.debug(f"提交响应: {submit_rsp[:500]}")

                try:
                    result = json.loads(submit_rsp)
                    loguru.logger.info(f"提交结果解析: {result}")
                    
                    if result.get("status") or result.get("success") or result.get("code") == 1:
                        loguru.logger.info(f"✅ 作业 '{self.title}' 提交成功 -> {result.get('msg', '无消息')}")
                        return True
                    else:
                        loguru.logger.error(f"❌ 提交失败 -> {result.get('msg', '未知错误')}")
                        continue
                except json.JSONDecodeError:
                    loguru.logger.debug(f"响应不是JSON格式: {submit_rsp[:200]}")

                if submit_rsp and ("成功" in submit_rsp or "success" in submit_rsp.lower() or "true" in submit_rsp.lower()):
                    if '"status":true' in submit_rsp or '"success":true' in submit_rsp:
                        loguru.logger.info(f"✅ 作业 '{self.title}' 提交成功（文本匹配）")
                        return True
            except Exception as e:
                loguru.logger.error(f"提交异常: {e}")
                import traceback
                loguru.logger.error(f"异常堆栈: {traceback.format_exc()}")
                continue

        loguru.logger.error(f"❌ 作业 '{self.title}' 所有提交URL都失败了")
        return False
