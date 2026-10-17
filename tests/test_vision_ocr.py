# _*_ coding:utf-8 _*_
"""视觉模型 OCR 模块测试：mock HTTP 与 mock provider，不依赖真实网络。"""
from __future__ import annotations

import base64

import pytest

import vision_ocr
from vision_ocr import (
    VisionOCR,
    build_ocr_from_config,
    contains_image,
    extract_image_urls,
    ocr_images_in_text,
)

# 1x1 PNG 最小合法字节（魔数足够 _guess_mime 识别即可）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


# ─────────────────────────────────────────────────────────
# 假 provider / 假客户端
# ─────────────────────────────────────────────────────────

class FakeProvider:
    """模拟 OpenAI 兼容 provider：记录请求体并返回预设识别文本。"""

    provider_name = "fake-openai"

    def __init__(self, text="识别到的题目文字", exc=None, configured=True):
        self.text = text
        self.exc = exc
        self.configured = configured
        self.calls = []
        self.config = {
            "api_key": "sk-fake-key-000000",
            "base_url": "https://api.fake.com",
            "model": "fake-vision-model",
            "max_tokens_content": 512,
        }

    def is_configured(self):
        return self.configured

    def _endpoint(self):
        return "https://api.fake.com/v1/chat/completions"

    def _post_json(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.exc is not None:
            raise self.exc
        return {"choices": [{"message": {"content": self.text}}]}


class NonVisionProvider:
    """不具备 OpenAI 兼容视觉接口的 provider（如 claude/wenxin 形态）。"""

    provider_name = "fake-nonvision"

    def is_configured(self):
        return True


class FakeMultiClient:
    def __init__(self, providers):
        self.providers = providers


# ─────────────────────────────────────────────────────────
# 图片检测与提取
# ─────────────────────────────────────────────────────────

class TestImageExtract:
    def test_contains_image(self):
        assert contains_image('题干 <img src="https://img.chaoxing.com/a.jpg"> 后缀')
        assert not contains_image("纯文本题干")
        assert not contains_image("")

    def test_extract_image_urls(self):
        html = ('<p><img src="https://img.chaoxing.com/a.png"></p>'
                "<img alt='x' src='https://img.chaoxing.com/b.jpg'>")
        assert extract_image_urls(html) == [
            "https://img.chaoxing.com/a.png",
            "https://img.chaoxing.com/b.jpg",
        ]
        assert extract_image_urls("无图") == []
        assert extract_image_urls("") == []


# ─────────────────────────────────────────────────────────
# base64 编码与大小限制
# ─────────────────────────────────────────────────────────

class TestDataUri:
    def test_bytes_to_data_uri_mime(self):
        ocr = VisionOCR({"enable": True})
        uri = ocr._to_data_uri(PNG_BYTES)
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == PNG_BYTES

        uri = ocr._to_data_uri(JPEG_BYTES)
        assert uri.startswith("data:image/jpeg;base64,")

    def test_size_limit(self):
        ocr = VisionOCR({"max-image-mb": 0.00001})  # ~10 字节上限
        assert ocr._to_data_uri(PNG_BYTES) is None

    def test_download_via_doget(self, monkeypatch):
        """图片下载走 utils.doGet 且带学习通 Referer。"""
        captured = {}

        class FakeResp:
            status_code = 200
            content = PNG_BYTES

        def fake_do_get(url, headers=None, ifFullBack=False):
            captured["url"] = url
            captured["headers"] = headers
            captured["full"] = ifFullBack
            return FakeResp()

        monkeypatch.setattr(vision_ocr, "doGet", fake_do_get)
        ocr = VisionOCR({})
        uri = ocr._to_data_uri("https://img.chaoxing.com/a.png",
                               headers={"Cookie": "k=v"})
        assert uri is not None
        assert captured["full"] is True
        assert captured["headers"]["Referer"] == "https://mooc1.chaoxing.com/"
        assert captured["headers"]["Cookie"] == "k=v"

    def test_download_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(vision_ocr, "doGet", lambda *a, **k: None)
        ocr = VisionOCR({})
        assert ocr._to_data_uri("https://img.chaoxing.com/a.png") is None


# ─────────────────────────────────────────────────────────
# 视觉消息结构与 provider 链
# ─────────────────────────────────────────────────────────

class TestRecognize:
    def test_vision_message_structure(self):
        provider = FakeProvider(text="图片里的题干")
        ocr = VisionOCR({}, client=FakeMultiClient([provider]))
        assert ocr.recognize(PNG_BYTES) == "图片里的题干"

        body = provider.calls[0]["body"]
        assert body["model"] == "fake-vision-model"
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert provider.calls[0]["headers"]["Authorization"].startswith("Bearer ")

    def test_model_override(self):
        provider = FakeProvider()
        ocr = VisionOCR({"model": "gpt-4o"}, client=FakeMultiClient([provider]))
        ocr.recognize(PNG_BYTES)
        assert provider.calls[0]["body"]["model"] == "gpt-4o"

    def test_fallback_to_next_provider(self):
        """首个 provider 调用失败时降级到链上下一个。"""
        bad = FakeProvider(exc=RuntimeError("boom"))
        good = FakeProvider(text="成功")
        ocr = VisionOCR({}, client=FakeMultiClient([bad, good]))
        assert ocr.recognize(PNG_BYTES) == "成功"
        assert len(bad.calls) == 1 and len(good.calls) == 1

    def test_non_vision_provider_skipped(self):
        """不支持视觉的 provider 直接跳过，不报错。"""
        ocr = VisionOCR({}, client=FakeMultiClient(
            [NonVisionProvider(), FakeProvider(text="ok")]))
        assert ocr.recognize(PNG_BYTES) == "ok"

    def test_all_providers_fail_returns_empty(self):
        ocr = VisionOCR({}, client=FakeMultiClient(
            [FakeProvider(exc=RuntimeError("x")), NonVisionProvider()]))
        assert ocr.recognize(PNG_BYTES) == ""

    def test_unconfigured_provider_skipped(self):
        ocr = VisionOCR({}, client=FakeMultiClient([FakeProvider(configured=False)]))
        assert ocr.recognize(PNG_BYTES) == ""

    def test_prompt_hint_used(self):
        provider = FakeProvider()
        ocr = VisionOCR({}, client=FakeMultiClient([provider]))
        ocr.recognize(PNG_BYTES, prompt_hint="这是一道单选题的选项图")
        body = provider.calls[0]["body"]
        assert body["messages"][0]["content"][0]["text"] == "这是一道单选题的选项图"


# ─────────────────────────────────────────────────────────
# 开关与拼接辅助
# ─────────────────────────────────────────────────────────

class TestSwitchAndGlue:
    def test_build_ocr_disabled(self, monkeypatch):
        monkeypatch.setattr(vision_ocr, "get_vision_ocr_config",
                            lambda: {"enable": False})
        assert build_ocr_from_config() is None

    def test_build_ocr_enabled(self, monkeypatch):
        monkeypatch.setattr(vision_ocr, "get_vision_ocr_config",
                            lambda: {"enable": True, "model": "m", "max-image-mb": 2})
        ocr = build_ocr_from_config(client=FakeMultiClient([]))
        assert isinstance(ocr, VisionOCR)
        assert ocr.model_override == "m"
        assert ocr.max_bytes == 2 * 1024 * 1024

    def test_ocr_images_in_text_appends(self, monkeypatch):
        class FakeResp:
            status_code = 200
            content = PNG_BYTES

        monkeypatch.setattr(vision_ocr, "doGet", lambda *a, **k: FakeResp())
        provider = FakeProvider(text="选项A图片内容")
        ocr = VisionOCR({}, client=FakeMultiClient([provider]))
        text = '题干 <img src="https://img.chaoxing.com/a.png">'
        out = ocr_images_in_text(text, ocr)
        assert out.startswith(text)
        assert "[图片文字: 选项A图片内容]" in out

    def test_ocr_images_in_text_noop(self):
        """ocr 为 None（开关关闭）或无图片时原文返回（零行为变化）。"""
        text = '题干 <img src="https://img.chaoxing.com/a.png">'
        assert ocr_images_in_text(text, None) == text
        assert ocr_images_in_text("纯文本", object()) == "纯文本"

    def test_ocr_failure_keeps_original(self, monkeypatch):
        class FakeResp:
            status_code = 200
            content = PNG_BYTES

        monkeypatch.setattr(vision_ocr, "doGet", lambda *a, **k: FakeResp())
        provider = FakeProvider(exc=RuntimeError("boom"))
        ocr = VisionOCR({}, client=FakeMultiClient([provider]))
        text = '题干 <img src="https://img.chaoxing.com/a.png">'
        assert ocr_images_in_text(text, ocr) == text
