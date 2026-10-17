# _*_ coding:utf-8 _*_
"""TikuAdapter 题库接入测试：mock doPost 验证 query / 解析 / 异常回退。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import tiku as tiku_module
from tiku import Tiku, TikuAdapter


# ─────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────

class FakeCache:
    """避免 CacheDAO 触碰真实文件系统。"""

    def __init__(self):
        self.items = {}

    def get_cache(self, key):
        return self.items.get(key)

    def add_cache(self, key, value):
        self.items[key] = value


def make_resp(status=200, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    return resp


def make_adapter(monkeypatch, url="http://localhost:8060", resp=None, exc=None):
    """构造 TikuAdapter 并接管其 doPost 与缓存。"""
    monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
    adapter = TikuAdapter({"tikuadapter_url": url, "delay": 0})
    calls = []

    def fake_do_post(url, headers=None, data=None, ifFullBack=False):
        calls.append({"url": url, "headers": headers, "data": data})
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(tiku_module, "doPost", fake_do_post)
    return adapter, calls


# ─────────────────────────────────────────────────────────
# 配置与初始化
# ─────────────────────────────────────────────────────────

class TestInit:
    def test_disabled_when_url_empty(self, monkeypatch):
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        adapter = TikuAdapter({"tikuadapter_url": ""})
        assert adapter.init_tiku() is False

    def test_enabled_when_url_set(self, monkeypatch):
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        adapter = TikuAdapter({"tikuadapter_url": "http://localhost:8060"})
        assert adapter.init_tiku() is True

    def test_search_url_completion(self, monkeypatch):
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        a = TikuAdapter({"tikuadapter_url": "http://localhost:8060"})
        assert a._search_url() == "http://localhost:8060/adapter-service/search"
        b = TikuAdapter({"tikuadapter_url": "http://localhost:8060/"})
        assert b._search_url() == "http://localhost:8060/adapter-service/search"
        # 已带端点与查询参数时原样使用
        c = TikuAdapter({"tikuadapter_url": "http://localhost:8060/adapter-service/search?use=local,icodef&icodefToken=abc"})
        assert c._search_url() == "http://localhost:8060/adapter-service/search?use=local,icodef&icodefToken=abc"


class TestMapType:
    @pytest.mark.parametrize("q_type,expected", [
        ("single", 0), ("单选题", 0),
        ("multiple", 1), ("多选题", 1),
        ("completion", 2), ("填空题", 2),
        ("judgement", 3), ("判断题", 3),
        ("shortanswer", 4), ("简答题", 4),
        (0, 0), (3, 3), ("未知题型", 0), (None, 0), (99, 0),
    ])
    def test_type_mapping(self, monkeypatch, q_type, expected):
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        adapter = TikuAdapter({"tikuadapter_url": "http://x"})
        assert adapter._map_type(q_type) == expected


# ─────────────────────────────────────────────────────────
# query 请求与解析
# ─────────────────────────────────────────────────────────

class TestQuery:
    def test_success_best_answer(self, monkeypatch):
        resp = make_resp(200, {
            "question": "题", "type": 1,
            "answer": {"bestAnswer": ["特殊侵权责任", "过错推定责任"],
                       "answerText": "特殊侵权责任#过错推定责任"},
        })
        adapter, calls = make_adapter(monkeypatch, resp=resp)
        answer = adapter.query({"title": "违反安全保障义务责任属于（）",
                                "options": ["公平责任", "特殊侵权责任", "过错推定责任", "连带责任"],
                                "type": "multiple"})
        assert answer == "特殊侵权责任#过错推定责任"
        # 请求协议校验：端点、JSON 体字段与题型码
        assert len(calls) == 1
        assert calls[0]["url"] == "http://localhost:8060/adapter-service/search"
        body = json.loads(calls[0]["data"].decode("utf-8"))
        assert body["question"] == "违反安全保障义务责任属于（）"
        assert body["options"] == ["公平责任", "特殊侵权责任", "过错推定责任", "连带责任"]
        assert body["type"] == 1

    def test_answer_text_fallback(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": [], "answerText": "对"}})
        adapter, _ = make_adapter(monkeypatch, resp=resp)
        assert adapter.query({"title": "判断题题干", "type": "judgement"}) == "对"

    def test_options_string_split_to_list(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": ["A"]}})
        adapter, calls = make_adapter(monkeypatch, resp=resp)
        adapter.query({"title": "题", "options": "A.甲\nB.乙\n", "type": "single"})
        body = json.loads(calls[0]["data"].decode("utf-8"))
        assert body["options"] == ["A.甲", "B.乙"]

    def test_no_answer_returns_none(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": [], "answerText": ""}})
        adapter, _ = make_adapter(monkeypatch, resp=resp)
        assert adapter.query({"title": "无结果题"}) is None

    def test_http_error_returns_none(self, monkeypatch):
        adapter, _ = make_adapter(monkeypatch, resp=make_resp(500, {}))
        assert adapter.query({"title": "题"}) is None

    def test_network_exception_returns_none(self, monkeypatch):
        adapter, _ = make_adapter(monkeypatch, exc=ConnectionError("网络不可达"))
        assert adapter.query({"title": "题"}) is None

    def test_do_post_none_returns_none(self, monkeypatch):
        adapter, _ = make_adapter(monkeypatch, resp=None)
        assert adapter.query({"title": "题"}) is None

    def test_cache_hit_skips_http(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": ["A"]}})
        adapter, calls = make_adapter(monkeypatch, resp=resp)
        adapter.cache.add_cache("已缓存题", "缓存答案")
        assert adapter.query({"title": "已缓存题"}) == "缓存答案"
        assert calls == []

    def test_success_caches_answer(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": ["A"]}})
        adapter, _ = make_adapter(monkeypatch, resp=resp)
        adapter.query({"title": "新题"})
        assert adapter.cache.get_cache("新题") == "A"

    def test_empty_title_or_url_returns_none(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": ["A"]}})
        adapter, calls = make_adapter(monkeypatch, url="", resp=resp)
        assert adapter.query({"title": "题"}) is None
        adapter2, calls2 = make_adapter(monkeypatch, resp=resp)
        assert adapter2.query({"title": "  "}) is None
        assert calls == [] and calls2 == []


class TestCheckConnection:
    def test_ok(self, monkeypatch):
        resp = make_resp(200, {"answer": {"bestAnswer": []}})
        adapter, _ = make_adapter(monkeypatch, resp=resp)
        assert adapter.check_connection() is True

    def test_missing_answer_field(self, monkeypatch):
        adapter, _ = make_adapter(monkeypatch, resp=make_resp(200, {"error": "x"}))
        assert adapter.check_connection() is False

    def test_no_url(self, monkeypatch):
        adapter, _ = make_adapter(monkeypatch, url="", resp=make_resp(200, {"answer": {}}))
        assert adapter.check_connection() is False


# ─────────────────────────────────────────────────────────
# 轮询回退集成（Tiku 注册表）
#
# 历史缺陷：Tiku 外观类曾未实现抽象方法 check_connection，
# 实例化直接 TypeError；该缺陷已修复，此处直接使用 Tiku()。
# ─────────────────────────────────────────────────────────

def _make_tiku():
    return Tiku()


class TestFallbackIntegration:
    def test_registered_in_tiku_classes(self):
        assert "TikuAdapter" in Tiku.TIKU_CLASSES
        assert Tiku.TIKU_CLASSES["TikuAdapter"] is TikuAdapter

    def test_provider_loads_via_config(self, monkeypatch):
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        t = _make_tiku()
        t.config_set({"provider": "TikuAdapter", "tikuadapter_url": "http://localhost:8060"})
        t.get_tiku_from_config()
        assert t.DISABLE is False
        assert isinstance(t._tikus[0], TikuAdapter)
        assert t.init_tiku() is True

    def test_empty_url_skipped_in_fallback(self, monkeypatch):
        # url 为空时 init_tiku 失败，轮询应跳过该题库
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        t = _make_tiku()
        t.config_set({"provider": "TikuAdapter", "tikuadapter_url": ""})
        t.get_tiku_from_config()
        assert t.init_tiku() is False

    def test_fallback_to_next_provider(self, monkeypatch):
        # TikuAdapter 查询失败时回退到下一个题库
        monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
        t = _make_tiku()
        t.config_set({
            "provider": "TikuAdapter,TikuGo",
            "tikuadapter_url": "http://localhost:8060",
            "token": "tok",
        })
        t.get_tiku_from_config()
        assert [x.name for x in t._tikus] == ["TikuAdapter", "TikuGo"]

        def fake_do_post(url, headers=None, data=None, ifFullBack=False):
            return None  # TikuAdapter 服务不可用

        monkeypatch.setattr(tiku_module, "doPost", fake_do_post)

        class FakeSessionResp:
            status_code = 200

            def json(self):
                return {"answer": "回退答案"}

        monkeypatch.setattr(tiku_module.requests, "get", lambda *a, **k: FakeSessionResp())
        assert t.query({"title": "题"}) == "回退答案"
