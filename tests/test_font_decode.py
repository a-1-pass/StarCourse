# _*_ coding:utf-8 _*_
"""字体反混淆模块测试：fontTools 现场生成微型字体，不依赖真实网络。

构造两个微型 TTF：
- 参考字体：cmap 0x41->glyph A（矩形轮廓 500x700）、0x42->glyph B；
- 混淆字体：模拟 font-cxsecret——码位打乱（0xE000/0xE001）、
  upem 2000 + 坐标等比放大 2 倍（归一化后轮廓与参考字体一致）。
映射表由参考字体生成，decode 应把 \\ue000\\ue001 还原为 AB。
"""
from __future__ import annotations

import io
import json

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

import font_decode
from font_decode import FontDecoder, find_secret_font_url, glyph_outline_hash


# ─────────────────────────────────────────────────────────
# 微型测试字体构造
# ─────────────────────────────────────────────────────────

def _rect_glyph(x0, y0, x1, y1):
    pen = TTGlyphPen(None)
    pen.moveTo((x0, y0))
    pen.lineTo((x1, y0))
    pen.lineTo((x1, y1))
    pen.lineTo((x0, y1))
    pen.closePath()
    return pen.glyph()


def _make_font(cmap, glyphs, upem=1000) -> bytes:
    order = [".notdef"] + list(glyphs.keys())
    fb = FontBuilder(upem)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    all_glyphs = {".notdef": _rect_glyph(0, 0, 0, 0)}
    all_glyphs.update(glyphs)
    fb.setupGlyf(all_glyphs)
    fb.setupHorizontalMetrics({g: (600, 0) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({
        "familyName": "test", "styleName": "Regular",
        "uniqueFontIdentifier": "test", "fullName": "test",
        "psName": "test", "version": "1",
    })
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def ref_font_bytes() -> bytes:
    """参考字体：A=500x700 矩形，B=偏移小矩形。"""
    return _make_font(
        {0x41: "A", 0x42: "B"},
        {"A": _rect_glyph(0, 0, 500, 700), "B": _rect_glyph(50, 50, 450, 650)},
    )


@pytest.fixture(scope="module")
def obfuscated_font_bytes() -> bytes:
    """混淆字体：码位 0xE000/0xE001，upem 2000、坐标等比放大 2 倍。"""
    return _make_font(
        {0xE000: "g1", 0xE001: "g2"},
        {"g1": _rect_glyph(0, 0, 1000, 1400), "g2": _rect_glyph(100, 100, 900, 1300)},
        upem=2000,
    )


@pytest.fixture()
def map_table_path(tmp_path, ref_font_bytes) -> str:
    """用与 tools/build_font_map.py 相同的哈希逻辑生成映射表。"""
    font = TTFont(io.BytesIO(ref_font_bytes))
    table = {
        glyph_outline_hash(font, glyph_name): chr(codepoint)
        for codepoint, glyph_name in (font.getBestCmap() or {}).items()
    }
    path = tmp_path / "font_map_table.json"
    path.write_text(
        json.dumps({"version": 1, "map": table}, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


# ─────────────────────────────────────────────────────────
# 字形哈希
# ─────────────────────────────────────────────────────────

class TestGlyphHash:
    def test_hash_deterministic(self, ref_font_bytes):
        font1 = TTFont(io.BytesIO(ref_font_bytes))
        font2 = TTFont(io.BytesIO(ref_font_bytes))
        assert glyph_outline_hash(font1, "A") == glyph_outline_hash(font2, "A")
        assert glyph_outline_hash(font1, "A") != glyph_outline_hash(font1, "B")

    def test_hash_normalized_across_upem(self, ref_font_bytes, obfuscated_font_bytes):
        """同源字形在不同 unitsPerEm / 码位下哈希应一致（反混淆的根基）。"""
        ref = TTFont(io.BytesIO(ref_font_bytes))
        obf = TTFont(io.BytesIO(obfuscated_font_bytes))
        assert glyph_outline_hash(ref, "A") == glyph_outline_hash(obf, "g1")
        assert glyph_outline_hash(ref, "B") == glyph_outline_hash(obf, "g2")


# ─────────────────────────────────────────────────────────
# 解码与降级
# ─────────────────────────────────────────────────────────

class TestDecode:
    def test_decode_restores_chars(self, map_table_path, obfuscated_font_bytes):
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("\ue000\ue001X", obfuscated_font_bytes) == "ABX"

    def test_unknown_glyph_passthrough(self, map_table_path, obfuscated_font_bytes):
        """映射表未覆盖的字符原样保留。"""
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("中文 123", obfuscated_font_bytes) == "中文 123"

    def test_missing_map_table_degrades(self, tmp_path, obfuscated_font_bytes):
        """映射表不存在：记警告、返回原文、不崩溃。"""
        dec = FontDecoder(map_table_path=str(tmp_path / "not_exists.json"))
        assert dec.decode("\ue000", obfuscated_font_bytes) == "\ue000"

    def test_broken_font_bytes_degrades(self, map_table_path):
        """字体字节损坏：返回原文、不崩溃。"""
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("\ue000", b"not-a-font") == "\ue000"

    def test_empty_text(self, map_table_path, obfuscated_font_bytes):
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("", obfuscated_font_bytes) == ""


# ─────────────────────────────────────────────────────────
# 缓存与下载
# ─────────────────────────────────────────────────────────

class TestCache:
    def test_font_download_cached(self, monkeypatch, map_table_path, obfuscated_font_bytes):
        """同一 URL 重复 decode 只下载一次（内部按内容哈希缓存解析结果）。"""
        calls = []

        class FakeResp:
            status_code = 200
            content = obfuscated_font_bytes

        def fake_do_get(url, headers=None, ifFullBack=False):
            calls.append(url)
            return FakeResp()

        monkeypatch.setattr(font_decode.decoder, "doGet", fake_do_get)
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("\ue000", "https://example.com/f.woff") == "A"
        assert dec.decode("\ue001", "https://example.com/f.woff") == "B"
        # 第二次仍走网络接口取字节（doGet 层有自身重试/延迟策略），
        # 但字体解析结果命中缓存：_font_cache 只有 1 个条目
        assert len(dec._font_cache) == 1
        assert len(calls) == 2

    def test_download_failure_degrades(self, monkeypatch, map_table_path):
        monkeypatch.setattr(font_decode.decoder, "doGet",
                            lambda *a, **k: None)
        dec = FontDecoder(map_table_path=map_table_path)
        assert dec.decode("\ue000", "https://example.com/f.woff") == "\ue000"


# ─────────────────────────────────────────────────────────
# 页面字体 URL 提取与接线入口
# ─────────────────────────────────────────────────────────

PAGE_WITH_FONT = """
<html><head><style>
@font-face {
  font-family: 'font-cxsecret';
  src: url('https://s3.ananas.chaoxing.com/some/font-cxsecret.woff') format('woff');
}
</style></head><body></body></html>
"""


class TestWiring:
    def test_find_font_url(self):
        url = find_secret_font_url(PAGE_WITH_FONT)
        assert url == "https://s3.ananas.chaoxing.com/some/font-cxsecret.woff"

    def test_find_font_url_absent(self):
        assert find_secret_font_url("<html><body>plain</body></html>") is None
        assert find_secret_font_url("") is None

    def test_build_decoder_disabled(self, monkeypatch):
        """开关关闭：返回 None（调用方零行为变化）。"""
        monkeypatch.setattr(font_decode, "get_font_decode_config", lambda: {"enable": False})
        assert font_decode.build_decoder_from_page(PAGE_WITH_FONT) is None

    def test_build_decoder_no_font_in_page(self, monkeypatch):
        monkeypatch.setattr(font_decode, "get_font_decode_config", lambda: {"enable": True})
        assert font_decode.build_decoder_from_page("<html></html>") is None

    def test_build_decoder_enabled(self, monkeypatch, map_table_path, obfuscated_font_bytes):
        """开关开启 + 页面含字体引用：返回已预热的共享解码器。"""
        monkeypatch.setattr(
            font_decode, "get_font_decode_config",
            lambda: {"enable": True, "map-table": map_table_path},
        )
        font_decode._shared_decoder = None  # 隔离模块级单例

        class FakeResp:
            status_code = 200
            content = obfuscated_font_bytes

        monkeypatch.setattr(font_decode.decoder, "doGet",
                            lambda *a, **k: FakeResp())
        dec = font_decode.build_decoder_from_page(PAGE_WITH_FONT)
        assert isinstance(dec, FontDecoder)
        assert dec.default_font == "https://s3.ananas.chaoxing.com/some/font-cxsecret.woff"
        # 预热后 decode 缺省用 default_font，无需再传字体地址
        assert dec.decode("\ue000") == "A"
        font_decode._shared_decoder = None
