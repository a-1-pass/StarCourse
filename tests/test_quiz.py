# _*_ coding:utf-8 _*_
"""Quiz 全链路测试：HTML 题目解析 -> 填空/简答/单选答案映射。"""
from lxml import etree

from classis.Media.Quiz import Quiz

PAGE = """
<html><body><form>
  <div class="singleQuesId" data="9001">
    <div class="TiMu" data="2">
      <div class="Zy_TItle">1. 中国的首都是____，简称____。</div>
    </div>
    <input type="text" name="answer9001" value="">
    <input type="text" name="answer9001" value="">
  </div>
  <div class="singleQuesId" data="9002">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">2. 1+1=2。</div>
    </div>
    <ul>
      <li aria-label="对"><input type="radio" name="answer9002" value="true"></li>
      <li aria-label="错"><input type="radio" name="answer9002" value="false"></li>
    </ul>
  </div>
  <div class="singleQuesId" data="9003">
    <div class="TiMu" data="4">
      <div class="Zy_TItle">3. 简述李白的诗歌风格。</div>
    </div>
    <textarea name="answer9003"></textarea>
  </div>
  <div class="singleQuesId" data="9004">
    <div class="TiMu" data="2">
      <div class="Zy_TItle">4. 中国的首都：____；简称：____。</div>
    </div>
    <input type="text" name="answer9004" value="">
    <input type="text" name="answer9004_2" value="">
  </div>
  <div class="singleQuesId" data="9005">
    <div class="TiMu" data="6">
      <div class="Zy_TItle">5. 试论述供给侧结构性改革的主要内容。</div>
    </div>
    <textarea name="answer9005"></textarea>
  </div>
</form></body></html>
"""


def make_quiz(tiku=None, strategy="first"):
    attachment = {
        "jobid": "work-1",
        "enc": "e",
        "property": {"workid": "work-1", "title": "章节测验"},
    }
    quiz = Quiz(attachment, {"Cookie": "k=v"}, {}, "1001", strategy=strategy, tiku=tiku)
    html = etree.HTML(PAGE)
    quiz.questions = html.xpath("//div[contains(@class, 'singleQuesId')]")
    return quiz


class TestExtractQuestionInfo:
    def test_completion_detection(self):
        quiz = make_quiz()
        info = quiz._extract_question_info(quiz.questions[0], 0)
        assert info["type_code"] == "2"
        assert info["question_type"] == "填空题"
        assert info["question_id"] == "9001"
        assert len(info["text_inputs"]) == 2
        assert info["title"].startswith("中国的首都是")

    def test_shortanswer_detection(self):
        quiz = make_quiz()
        info = quiz._extract_question_info(quiz.questions[2], 2)
        assert info["type_code"] == "4"
        assert info["question_type"] == "简答题"
        assert info["question_id"] == "9003"
        assert len(info["textareas"]) == 1

    def test_single_choice_detection(self):
        quiz = make_quiz()
        info = quiz._extract_question_info(quiz.questions[1], 1)
        assert info["question_type"] == "单选题"
        assert info["question_id"] == "9002"
        assert len(info["radios"]) == 2


class TestGenerateAnswers:
    def test_full_page_mapping(self, fake_tiku_cls):
        tiku = fake_tiku_cls({
            "completion": "北京；沪",
            "shortanswer": "简答内容",
            "essay": "论述" * 20,
        })
        quiz = make_quiz(tiku=tiku)
        result = quiz._generate_answers(tiku)
        answers = result["answers"]

        # 同名多文本框：list 承载多空（requests 编码为重复键）
        assert answers["answer9001"] == ["北京", "沪"]
        # 单选：first 策略选第一个 radio
        assert answers["answer9002"] == "true"
        # 简答 textarea：整段写入
        assert answers["answer9003"] == "简答内容"
        # 不同名字段：按空位一一映射
        assert answers["answer9004"] == "北京"
        assert answers["answer9004_2"] == "沪"
        # 论述题（题型码 6）：textarea 整段写入
        assert answers["answer9005"] == "论述" * 20

        assert result["question_ids"] == ["9001", "9002", "9003", "9004", "9005"]

    def test_tiku_strategy_choices_fallback_first(self, fake_tiku_cls):
        """题库答题模式：题库命中 + 选择题首选兜底（与 AI 路径独立）。"""
        tiku = fake_tiku_cls({"completion": "北京；沪"})
        quiz = make_quiz(tiku=tiku, strategy="tiku")
        result = quiz._generate_answers(tiku)
        assert result["answers"]["answer9001"] == ["北京", "沪"]
        assert result["answers"]["answer9002"] == "true"

    def test_essay_type_detection(self):
        quiz = make_quiz()
        info = quiz._extract_question_info(quiz.questions[4], 4)
        assert info["type_code"] == "6"
        assert info["question_type"] == "论述题"
        assert info["question_id"] == "9005"

    def test_completion_without_tiku_falls_back(self):
        quiz = make_quiz(tiku=None)  # first 策略、无 AI -> 兜底文本
        result = quiz._generate_answers(None)
        answers = result["answers"]
        assert answers["answer9001"] != ""
        assert answers["answer9002"] == "true"
