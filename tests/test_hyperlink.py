# _*_ coding:utf-8 _*_
"""超链接任务点 Hyperlink 的单元测试。"""
import json
from unittest.mock import patch

import pytest

from engine.classis.Media.Hyperlink import Hyperlink


def _make_attachment(**overrides):
    base = {
        "type": "hyperlink",
        "jobid": "job123",
        "jtoken": "tok456",
        "property": {"url": "https://example.com/page"},
    }
    base.update(overrides)
    return base


def _make_hyperlink(attachment=None, defaults=None, courseId="c1", classId="cl1"):
    attachment = attachment or _make_attachment()
    defaults = defaults or {"knowledgeid": "k1", "clazzId": "cl1"}
    return Hyperlink(attachment, {"Cookie": "x", "User-Agent": "ua"},
                     defaults, courseId, classId)


# ── URL 提取 ──────────────────────────────────────────────

def test_extract_url_from_property_url():
    h = _make_hyperlink()
    assert h._extract_url() == "https://example.com/page"


def test_extract_url_fallback_to_name():
    h = _make_hyperlink(attachment=_make_attachment(
        property={"name": "https://fallback.example.com"}))
    assert h._extract_url() == "https://fallback.example.com"


def test_extract_url_empty_when_none():
    h = _make_hyperlink(attachment=_make_attachment(property={}))
    assert h._extract_url() == ""


# ── 上报 URL 构建 ─────────────────────────────────────────

def test_build_report_url_contains_required_params():
    h = _make_hyperlink()
    url = h._build_report_url()
    assert "ananas/job/url" in url
    assert "jobid=job123" in url
    assert "knowledgeid=k1" in url
    assert "courseid=c1" in url
    assert "clazzid=cl1" in url
    assert "jtoken=tok456" in url
    assert "_dc=" in url


# ── 完成上报 ──────────────────────────────────────────────

def test_report_progress_returns_true_on_status_true():
    h = _make_hyperlink()
    with patch("engine.classis.Media.Hyperlink.doGet",
               return_value=json.dumps({"status": True})) as mock_get:
        assert h._report_progress() is True
        mock_get.assert_called_once()
        # 调用的 URL 应包含上报端点
        call_url = mock_get.call_args[1]["url"]
        assert "ananas/job/url" in call_url


def test_report_progress_returns_false_on_status_false():
    h = _make_hyperlink()
    with patch("engine.classis.Media.Hyperlink.doGet",
               return_value=json.dumps({"status": False})):
        assert h._report_progress() is False


def test_report_progress_returns_false_on_http_error():
    h = _make_hyperlink()
    with patch("engine.classis.Media.Hyperlink.doGet", return_value=None):
        assert h._report_progress() is False


def test_do_finish_opens_link_and_reports():
    h = _make_hyperlink()
    with patch("engine.classis.Media.Hyperlink.doGet",
               return_value=json.dumps({"status": True})) as mock_get:
        assert h.do_finish() is True
        # 应调用两次 doGet：一次打开链接，一次上报进度
        assert mock_get.call_count == 2


def test_do_finish_returns_false_when_no_url():
    h = _make_hyperlink(attachment=_make_attachment(property={}))
    with patch("engine.classis.Media.Hyperlink.doGet") as mock_get:
        assert h.do_finish() is False
        mock_get.assert_not_called()
