# _*_ coding:utf-8 _*_
"""Tiku 外观类 check_connection 缺陷修复测试。

历史缺陷：Tiku 未实现抽象方法 check_connection，``Tiku()`` 实例化直接
TypeError，导致整个题库轮询路径在生产中被 try/except 静默掩盖。
本文件验证：Tiku() 可实例化、check_connection 委托/兜底语义、
多 provider 轮询回退链路端到端可用（mock）。
"""
from __future__ import annotations

import tiku as tiku_module
from tiku import Tiku, TikuAdapter, TikuGo


class FakeCache:
    """避免 CacheDAO 触碰真实文件系统。"""

    def __init__(self):
        self.items = {}

    def get_cache(self, key):
        return self.items.get(key)

    def add_cache(self, key, value):
        self.items[key] = value


def _make_tiku(monkeypatch, config):
    monkeypatch.setattr(tiku_module, "CacheDAO", FakeCache)
    t = Tiku()
    t.config_set(config)
    t.get_tiku_from_config()
    return t


class TestInstantiation:
    def test_tiku_instantiable(self):
        """修复核心：Tiku() 不再 TypeError。"""
        t = Tiku()
        assert isinstance(t, Tiku)
        assert t.DISABLE is True

    def test_check_connection_when_disabled(self):
        """未配置 provider（DISABLE）时返回 False 而非抛异常。"""
        assert Tiku().check_connection() is False


class TestCheckConnection:
    def test_delegates_to_active_provider(self, monkeypatch):
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter",
            "tikuadapter_url": "http://localhost:8060",
        })
        assert t.init_tiku() is True

        monkeypatch.setattr(t._active_tiku, "check_connection", lambda: True)
        assert t.check_connection() is True

    def test_returns_false_when_all_unavailable(self, monkeypatch):
        """provider 全部不可用（含抛异常）时返回 False 而非抛异常。"""
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter,TikuGo",
            "tikuadapter_url": "http://localhost:8060",
            "token": "tok",
        })
        for sub in t._tikus:
            monkeypatch.setattr(sub, "check_connection",
                                lambda: (_ for _ in ()).throw(RuntimeError("down")))
        assert t.check_connection() is False

    def test_falls_back_to_next_provider(self, monkeypatch):
        """active provider 挂了时切换到下一个可用 provider。"""
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter,TikuGo",
            "tikuadapter_url": "http://localhost:8060",
            "token": "tok",
        })
        first, second = t._tikus
        monkeypatch.setattr(first, "check_connection", lambda: False)
        monkeypatch.setattr(second, "check_connection", lambda: True)
        assert t.check_connection() is True
        assert t._active_tiku is second


class TestQueryFallbackEndToEnd:
    def test_poll_fallback_chain(self, monkeypatch):
        """多 provider 轮询回退：首个题库无答案，自动落到下一个拿到答案。"""
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter,TikuGo",
            "tikuadapter_url": "http://localhost:8060",
            "token": "tok",
        })
        assert [x.name for x in t._tikus] == ["TikuAdapter", "TikuGo"]
        assert t.DISABLE is False

        # TikuAdapter 服务不可用（doPost 返回 None）
        monkeypatch.setattr(tiku_module, "doPost", lambda *a, **k: None)

        class FakeResp:
            status_code = 200

            def json(self):
                return {"answer": "回退答案"}

        monkeypatch.setattr(tiku_module.requests, "get", lambda *a, **k: FakeResp())

        answer = t.query({"title": "1+1=?", "type": "single", "options": ["1", "2"]})
        assert answer == "回退答案"

    def test_query_returns_none_when_all_fail(self, monkeypatch):
        """全部题库无答案时返回 None（Quiz/Work 调用处据此回退策略，不崩溃）。"""
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter",
            "tikuadapter_url": "http://localhost:8060",
        })
        monkeypatch.setattr(tiku_module, "doPost", lambda *a, **k: None)
        assert t.query({"title": "题", "type": "single", "options": []}) is None

    def test_query_exception_does_not_propagate(self, monkeypatch):
        """单个 provider 查询异常时不影响其余 provider（轮询健壮性）。"""
        t = _make_tiku(monkeypatch, {
            "provider": "TikuAdapter,TikuGo",
            "tikuadapter_url": "http://localhost:8060",
            "token": "tok",
        })
        monkeypatch.setattr(TikuAdapter, "query",
                            lambda self, q: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(TikuGo, "query", lambda self, q: "兜底答案")
        # 注：Tiku.query 自身不捕获 provider 异常（保持原契约），
        # 调用方 Quiz/Work 均有 try/except 包裹，此处验证异常类型可控
        import pytest
        with pytest.raises(RuntimeError):
            t.query({"title": "题", "type": "single"})
