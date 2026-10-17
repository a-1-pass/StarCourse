# _*_ coding:utf-8 _*_
"""Quiz/Work 字体反混淆 + 视觉OCR 接线测试。

核心断言：
- 两个开关默认关闭时，取题行为与现状完全一致（零行为变化）；
- 开关开启时，题干/选项先经字体解码与图片OCR再进入答题链。
"""
from __future__ import annotations

from lxml import etree

import font_decode
import vision_ocr
from classis.Media.Quiz import Quiz
from classis.Media.Work import Work

PAGE = """
<html><body><form>
  <div class="singleQuesId" data="9001">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">1. 混淆题干 <img src="https://img.chaoxing.com/q.png"></div>
    </div>
    <ul>
      <li><input type="radio" name="answer9001" value="a">选项甲</li>
      <li><input type="radio" name="answer9001" value="b">选项乙</li>
    </ul>
  </div>
</form></body></html>
"""


def make_quiz(**kwargs):
    attachment = {"property": {"workid": "w1", "title": "测验"}, "jobid": "j1"}
    return Quiz(attachment, {"Cookie": "k=v"}, {}, "1001", **kwargs)


def make_work(**kwargs):
    attachment = {
        "property": {"workid": "w1", "title": "作业"},
        "jobid": "work-1", "enc": "e",
    }
    return Work(attachment, {"Cookie": "k=v"}, {}, "1001", **kwargs)


def _question_elem():
    html = etree.HTML(PAGE)
    return html.xpath("//div[contains(@class, 'singleQuesId')]")[0]


# ─────────────────────────────────────────────────────────
# 开关关闭：零行为变化
# ─────────────────────────────────────────────────────────

class TestDisabledZeroChange:
    def test_quiz_extract_unchanged_when_disabled(self, monkeypatch):
        """helpers 未初始化（默认）时，题干/选项提取结果与原逻辑一致。"""
        monkeypatch.setattr(font_decode, "get_font_decode_config", lambda: {})
        monkeypatch.setattr(vision_ocr, "get_vision_ocr_config", lambda: {})
        quiz = make_quiz()
        quiz._prepare_text_helpers(PAGE)
        assert quiz._font_decoder is None
        assert quiz._vision_ocr is None

        q_info = quiz._extract_question_info(_question_elem(), 0)
        assert q_info["title"].startswith("混淆题干")
        assert "[图片文字:" not in q_info["title"]
        assert [o["text"] for o in q_info["options"]] == ["选项甲", "选项乙"]

    def test_work_decode_ocr_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(font_decode, "get_font_decode_config", lambda: {})
        monkeypatch.setattr(vision_ocr, "get_vision_ocr_config", lambda: {})
        work = make_work()
        work._prepare_text_helpers(PAGE)
        assert work._font_decoder is None
        assert work._vision_ocr is None

        title = '题干 <img src="https://img.chaoxing.com/a.png">'
        assert work._decode_text(title) == title
        assert work._ocr_text(title) == title


# ─────────────────────────────────────────────────────────
# 开关开启：解码 / OCR 生效
# ─────────────────────────────────────────────────────────

class _FakeDecoder:
    """模拟字体解码器：把【】标记内容替换为已还原文本。"""

    def __init__(self):
        self.calls = []

    def decode(self, text, font=None):
        self.calls.append(text)
        return text.replace("混淆题干", "真实题干").replace("选项甲", "真实选项甲")


class _FakeOCR:
    def __init__(self, text="图片中的文字"):
        self.text = text
        self.calls = []

    def recognize(self, url, prompt_hint=None, headers=None):
        self.calls.append({"url": url, "headers": headers})
        return self.text


class TestEnabled:
    def test_quiz_title_decoded_and_ocr_appended(self):
        quiz = make_quiz()
        quiz._font_decoder = _FakeDecoder()
        quiz._vision_ocr = _FakeOCR()

        q_info = quiz._extract_question_info(_question_elem(), 0)
        assert q_info["title"].startswith("真实题干")
        assert "[图片文字: 图片中的文字]" in q_info["title"]
        # 选项同样走字体解码
        assert q_info["options"][0]["text"] == "真实选项甲"
        # OCR 走学习通图床时携带会话头（Cookie/UA）
        assert quiz._vision_ocr.calls[0]["headers"].get("Cookie") == "k=v"

    def test_quiz_disabled_helpers_leave_text_alone(self):
        quiz = make_quiz()
        q_info = quiz._extract_question_info(_question_elem(), 0)
        assert "混淆题干" in q_info["title"]
        assert "[图片文字:" not in q_info["title"]

    def test_work_title_and_options_processed(self):
        work = make_work()
        work._font_decoder = _FakeDecoder()
        work._vision_ocr = _FakeOCR()

        title = work._decode_text(work._ocr_text("混淆题干"))
        assert title == "真实题干"

        options = 'A. 选项甲 <img src="https://img.chaoxing.com/o.png">'
        out = work._decode_text(work._ocr_text(options))
        assert "真实选项甲" in out
        assert "[图片文字: 图片中的文字]" in out

    def test_prepare_helpers_enabled(self, monkeypatch):
        """开关开启 + 页面含混淆字体引用时，helpers 被正确装配。"""
        import font_decode.decoder as decoder_mod

        monkeypatch.setattr(font_decode, "get_font_decode_config",
                            lambda: {"enable": True, "map-table": "x.json"})
        monkeypatch.setattr(vision_ocr, "get_vision_ocr_config",
                            lambda: {"enable": True})
        # 字体下载/解析在预热阶段即失败也不影响装配流程（内部降级）
        monkeypatch.setattr(decoder_mod, "doGet", lambda *a, **k: None)
        font_decode._shared_decoder = None

        quiz = make_quiz()
        quiz._prepare_text_helpers(
            "@font-face{font-family:'font-cxsecret';"
            "src:url('https://x.chaoxing.com/f.woff')}"
        )
        assert quiz._font_decoder is not None
        assert quiz._vision_ocr is not None
        font_decode._shared_decoder = None
