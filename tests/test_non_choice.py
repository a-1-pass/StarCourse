# _*_ coding:utf-8 _*_
"""non_choice 模块单元测试：空缺识别、答案拆分、格式适配、答案生成优先级、主观题模板。"""
from non_choice import (
    FALLBACK_TEXT,
    answer_completion,
    answer_non_question,
    answer_shortanswer,
    answer_subjective,
    build_subjective_system_prompt,
    detect_blank_count,
    detect_subject,
    normalize_completion,
    normalize_shortanswer,
    parse_answer_response,
    split_blank_answers,
)


class TestDetectBlankCount:
    def test_underlines(self):
        assert detect_blank_count("中国的首都是____，最大城市是____。") == 2

    def test_underline_long(self):
        assert detect_blank_count("_______岁至_______岁") == 2

    def test_empty_parens(self):
        assert detect_blank_count("（）是一国经济的立身之本。") == 1
        assert detect_blank_count("(   ) 是系统") == 1

    def test_empty_brackets(self):
        assert detect_blank_count("【  】是根本制度。") == 1

    def test_no_blank_defaults_one(self):
        assert detect_blank_count("没有空缺标记的题干") == 1

    def test_empty_title(self):
        assert detect_blank_count("") == 1


class TestSplitBlankAnswers:
    def test_list_input(self):
        assert split_blank_answers([" 太白 ", "青莲居士"], 2) == ["太白", "青莲居士"]

    def test_json_wrapper(self):
        assert split_blank_answers('{"Answer": ["a", "b"]}', 2) == ["a", "b"]

    def test_json_in_md_wrapper(self):
        assert split_blank_answers('```json\n{"Answer": ["a"]}\n```', 1) == ["a"]

    def test_newline_separated(self):
        assert split_blank_answers("北京\n上海", 2) == ["北京", "上海"]

    def test_hash_separated(self):
        assert split_blank_answers("北京#上海", 2) == ["北京", "上海"]

    def test_semicolon_weak_separator(self):
        assert split_blank_answers("北京；上海", 2) == ["北京", "上海"]

    def test_single_blank_no_split(self):
        # 单空时逗号是句内标点，不应切分
        assert split_blank_answers("1,5亿", 1) == ["1,5亿"]

    def test_clean_prefix(self):
        assert split_blank_answers("答案：北京", 1) == ["北京"]

    def test_numbered_parts(self):
        assert split_blank_answers("1. 北京\n2. 上海", 2) == ["北京", "上海"]

    def test_none(self):
        assert split_blank_answers(None, 2) == []

    def test_empty_string(self):
        assert split_blank_answers("  ", 1) == []


class TestNormalizeCompletion:
    def test_list_to_newline_joined(self):
        assert normalize_completion(["答1", "答2"], "____和____", 2) == "答1\n答2"

    def test_shortfall_padded(self):
        assert normalize_completion("答1", "____和____", 2) == "答1\n"

    def test_overflow_truncated(self):
        assert normalize_completion(["a", "b", "c"], "____和____", 2) == "a\nb"

    def test_from_tiku_string(self):
        assert normalize_completion("a;b", "____和____", 2) == "a\nb"

    def test_empty(self):
        assert normalize_completion(None, "", 1) == ""


class TestNormalizeShortanswer:
    def test_plain(self):
        assert normalize_shortanswer("  内容  ") == "内容"

    def test_list_joined(self):
        assert normalize_shortanswer(["第一点", "第二点"]) == "第一点\n第二点"

    def test_json_wrapper(self):
        assert normalize_shortanswer('{"Answer": ["要点"]} ') == "要点"

    def test_prefix_stripped(self):
        assert normalize_shortanswer("答案：内容") == "内容"

    def test_none(self):
        assert normalize_shortanswer(None) == ""


class TestAnswerGeneration:
    def test_tiku_first(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"completion": "北京\n上海"})
        answer, source = answer_completion("____和____", ai_client=None, tiku=tiku)
        assert answer == "北京\n上海"
        assert source == "tiku"

    def test_ai_fallback(self, fake_ai):
        answer, source = answer_completion("李白的字是____，号____。", ai_client=fake_ai, tiku=None)
        assert answer == "太白\n青莲居士"
        assert source == "ai"

    def test_random_fallback(self):
        answer, source = answer_completion("未知题目", ai_client=None, tiku=None)
        assert source == "random"
        assert answer == FALLBACK_TEXT

    def test_shortanswer_tiku(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"shortanswer": "简答内容"})
        answer, source = answer_shortanswer("简述XX", tiku=tiku)
        assert answer == "简答内容"
        assert source == "tiku"

    def test_tiku_exception_swallowed(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"completion": RuntimeError("网络错误")})
        answer, source = answer_completion("____", ai_client=None, tiku=tiku)
        assert source == "random"

    def test_dispatch_unknown_type(self):
        assert answer_non_question("single", "题干") == ("", "")

    def test_dispatch_by_type_code(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"completion": "答案"})
        answer, source = answer_non_question("2", "____", tiku=tiku)
        assert (answer, source) == ("答案", "tiku")


class TestSubjective:
    """主观题（名词解释/论述/计算/案例分析）模板化作答。"""

    def test_detect_subject(self):
        assert detect_subject("请计算该商品的供给价格弹性") == "math"
        assert detect_subject("试论述合同的有效要件") == "law"
        assert detect_subject("与学科无关的普通题干") == "general"

    def test_build_prompt_contains_template_and_label(self):
        prompt = build_subjective_system_prompt("essay", "试论述市场供求关系")
        assert "论述题" in prompt
        assert "总—分—总" in prompt
        assert "踩点给分" in prompt
        assert "JSON" in prompt

    def test_build_prompt_subject_addenda(self):
        prompt = build_subjective_system_prompt("calculation", "请计算该函数的导数")
        assert "公式" in prompt
        assert "理科" in prompt or "计算类" in prompt

    def test_tiku_long_answer_direct(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"essay": "长" * 40})
        answer, source = answer_subjective("essay", "试论述XX", tiku=tiku)
        assert (answer, source) == ("长" * 40, "tiku")

    def test_tiku_short_answer_enriched_by_ai(self, fake_tiku_cls, fake_ai):
        tiku = fake_tiku_cls({"essay": "短要点"})
        answer, source = answer_subjective("essay", "试论述XX", ai_client=fake_ai, tiku=tiku)
        assert source == "ai"
        # 载荷携带模板提示词与题库参考要点
        payload = fake_ai.content_calls[0]
        assert "system_prompt" in payload
        assert "参考要点" in payload["user_content"]

    def test_short_tiku_without_ai_kept(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"essay": "短要点"})
        answer, source = answer_subjective("essay", "试论述XX", ai_client=None, tiku=tiku)
        assert (answer, source) == ("短要点", "tiku")

    def test_no_tiku_ai_template_generation(self, fake_ai):
        answer, source = answer_subjective("case_analysis", "某案例分析题干", ai_client=fake_ai)
        assert source == "ai"
        assert answer == "（1）要点一（2）要点二（3）要点三"
        assert "案例分析题" in fake_ai.content_calls[0]["system_prompt"]

    def test_dispatch_new_type_codes(self, fake_tiku_cls):
        tiku = fake_tiku_cls({"essay": "长" * 40, "term_explanation": "长" * 40})
        assert answer_non_question("6", "题干", tiku=tiku)[1] == "tiku"
        assert answer_non_question("5", "题干", tiku=tiku)[1] == "tiku"

    def test_parse_answer_response(self):
        assert parse_answer_response('```json\n{"Answer": ["a"]}\n```') == ["a"]
        assert parse_answer_response('{"Answer": ["a", "b"]}') == ["a", "b"]
        assert parse_answer_response("答案：正文") == ["正文"]
        assert parse_answer_response(None) == []
        assert parse_answer_response("") == []
