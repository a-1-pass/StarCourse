# _*_ coding:utf-8 _*_
"""pytest 全局配置：将 engine 目录与项目根目录加入 sys.path，初始化引擎配置。"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")

for path in (ENGINE, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import GloConfig  # noqa: E402

if not GloConfig.data:
    GloConfig.init_yaml_data()


@pytest.fixture
def fake_ai():
    """按题型返回确定性答案的假 AI 客户端。"""

    class FakeAIClient:
        def __init__(self):
            self.content_calls = []
            self.letter_calls = []

        def is_configured(self):
            return True

        def answer_question_content(self, question):
            self.content_calls.append(question)
            q_type = str(question.get("question_type", ""))
            if q_type in ("single", "单选题"):
                return ["李白"]
            if q_type in ("multiple", "多选题"):
                return ["李白", "杜甫"]
            if q_type in ("completion", "填空题"):
                return ["太白", "青莲居士"]
            if q_type in ("shortanswer", "简答题"):
                return ["豪放飘逸，想象丰富"]
            if q_type in ("term_explanation", "名词解释", "essay", "论述题",
                          "calculation", "计算题", "case_analysis", "案例分析题"):
                return ["（1）要点一（2）要点二（3）要点三"]
            if q_type in ("judgement", "判断题"):
                return ["正确"]
            return ["未知答案"]

        def answer_question(self, question):
            self.letter_calls.append(question)
            q_type = str(question.get("question_type", ""))
            if q_type in ("completion", "填空题"):
                return "太白\n青莲居士"
            if q_type in ("shortanswer", "简答题"):
                return "豪放飘逸，想象丰富"
            return "A"

    return FakeAIClient()


class FakeTiku:
    """按题型返回预设答案的假题库。"""
    DISABLE = False

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def init_tiku(self):
        return True

    def check_connection(self):
        return True

    def judgement_select(self, answer):
        return str(answer) in ("对", "正确", "true", "√", "是")

    def query(self, question):
        self.calls.append(question)
        q_type = str(question.get("type", ""))
        if q_type in self.answers:
            value = self.answers[q_type]
            if isinstance(value, Exception):
                raise value
            return value
        return None


@pytest.fixture
def fake_tiku_cls():
    return FakeTiku
