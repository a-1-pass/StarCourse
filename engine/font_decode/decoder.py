# _*_ coding:utf-8 _*_
"""font-cxsecret 动态混淆字体反混淆解码器。

原理
----
学习通章节测验用 ``font-cxsecret`` 动态字体渲染题干/选项：HTML 中的字符
是被打乱的 unicode 码位，真实字形由页面 CSS 引用的 woff/ttf 字体提供。
本模块下载并解析该字体，对每个 cmap 映射的字形计算「轮廓特征哈希」，
再查 ``glyph_hash -> 真实字符`` 映射表还原文本。

许可证干净策略
--------------
映射表不搬运任何上游 GPL 项目的现成数据：
- 用 ``tools/build_font_map.py`` 输入本机参考字体（如宋体/微软雅黑，
  字形轮廓与超星混淆字体同源）自动比对生成；
- 或把自行获得的映射表放到 ``engine/data/font_map_table.json`` 直接使用。
映射表不存在时模块优雅降级：记录警告、原文返回，不崩溃。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re

import loguru

from utils import doGet

try:
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    _HAS_FONTTOOLS = True
except Exception:  # fontTools 未安装时整模块降级（requirements.txt 已声明依赖）
    TTFont = None
    DecomposingRecordingPen = None
    _HAS_FONTTOOLS = False

# 默认映射表路径解析（兼容 PyInstaller 打包）：
# 1. 可写数据目录优先（用户可把 tools/build_font_map.py 的产物放这里）；
# 2. 否则用 bundle / engine 目录下 data/font_map_table.json（只读资源）。
_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_default_map_table() -> str:
    try:
        from runtime_paths import bundle_dir, writable_data_dir
        user_table = os.path.join(writable_data_dir(), "font_map_table.json")
        if os.path.exists(user_table):
            return user_table
        return os.path.join(bundle_dir(), "data", "font_map_table.json")
    except Exception:
        return os.path.join(_ENGINE_DIR, "data", "font_map_table.json")


DEFAULT_MAP_TABLE = _resolve_default_map_table()

# 页面 CSS 中 font-cxsecret 字体引用：@font-face { font-family: 'font-cxsecret'; src: url(...) }
_FONT_URL_RE = re.compile(
    r"font-family\s*:\s*['\"]?font-cxsecret['\"]?[^}]*?url\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def find_secret_font_url(page_html: str):
    """从页面 HTML 中提取 font-cxsecret 混淆字体的下载地址；没有则返回 None。"""
    if not page_html:
        return None
    match = _FONT_URL_RE.search(page_html)
    return match.group(1) if match else None


def glyph_outline_hash(font: "TTFont", glyph_name: str) -> str:
    """计算单个字形的轮廓特征哈希。

    - 用 DecomposingRecordingPen 展开复合字形（component）为实际轮廓；
    - 坐标按 unitsPerEm 归一化到 1000 单位并保留 1 位小数，
      使同源但字号/单位不同的字体（参考字体 vs 混淆字体）哈希一致。
    """
    glyph_set = font.getGlyphSet()
    glyph = glyph_set[glyph_name]
    pen = DecomposingRecordingPen(glyph_set)
    try:
        glyph.draw(pen)
    except Exception:
        return ""
    head = font["head"] if "head" in font else None
    upem = getattr(head, "unitsPerEm", 1000) or 1000
    scale = 1000.0 / upem
    tokens = []
    for op, args in pen.value:
        quantized = []
        for arg in args:
            if isinstance(arg, (int, float)):
                quantized.append(round(arg * scale, 1))
            elif isinstance(arg, (tuple, list)):
                quantized.append(tuple(
                    round(x * scale, 1) if isinstance(x, (int, float)) else x
                    for x in arg
                ))
            else:
                quantized.append(arg)
        tokens.append((op, tuple(quantized)))
    return hashlib.sha1(repr(tokens).encode("utf-8")).hexdigest()


class FontDecoder:
    """混淆字体解码器：``decode(text, font_url_or_bytes) -> str``。

    内部按字体内容哈希缓存解析结果（码位 -> 真实字符 的映射），
    同一字体重复 decode 不会重复下载/解析。
    """

    def __init__(self, map_table_path: str = None):
        self.map_table_path = map_table_path or DEFAULT_MAP_TABLE
        self._table = None          # glyph_hash -> 真实字符（None=未加载，False=不可用）
        self._font_cache = {}       # 字体内容 sha1 -> {码位: 真实字符}
        #: 预热时记住的混淆字体地址，decode() 缺省使用
        self.default_font = None

    # ── 映射表 ──────────────────────────────────────────

    def _load_table(self):
        """懒加载映射表；文件缺失/损坏时置 False 并仅警告一次（优雅降级）。"""
        if self._table is not None:
            return self._table or None
        try:
            with open(self.map_table_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 兼容两种格式：{"version":1,"map":{...}} 或扁平 {hash: char}
            table = raw.get("map") if isinstance(raw, dict) and "map" in raw else raw
            if not isinstance(table, dict):
                raise ValueError("映射表结构非法")
            self._table = {str(k): str(v) for k, v in table.items()}
            loguru.logger.info(f"字体映射表加载成功: {len(self._table)} 条")
        except FileNotFoundError:
            loguru.logger.warning(
                f"字体映射表不存在: {self.map_table_path}，"
                "font-cxsecret 反混淆降级为返回原文。"
                "请运行 tools/build_font_map.py 生成映射表后重试"
            )
            self._table = False
        except Exception as e:
            loguru.logger.warning(f"字体映射表加载失败: {e}，反混淆降级为返回原文")
            self._table = False
        return self._table or None

    # ── 字体获取与解析（带缓存） ─────────────────────────

    def _fetch_font_bytes(self, font_url: str):
        """下载字体文件（走 engine/utils 的 doGet，自动带反检测头）。"""
        resp = doGet(url=font_url, ifFullBack=True)
        if resp is None or getattr(resp, "status_code", 0) != 200:
            return None
        return resp.content

    def _char_map_for_font(self, font_bytes: bytes, table: dict):
        """解析字体，返回 {码位: 真实字符}；结果按字体内容哈希缓存。"""
        key = hashlib.sha1(font_bytes).hexdigest()
        if key in self._font_cache:
            return self._font_cache[key]

        char_map = {}
        try:
            font = TTFont(io.BytesIO(font_bytes))
            cmap = font.getBestCmap() or {}
            for codepoint, glyph_name in cmap.items():
                outline_hash = glyph_outline_hash(font, glyph_name)
                real_char = table.get(outline_hash)
                # 映射表未覆盖的字形保留原字符（部分符号/空白无需还原）
                char_map[codepoint] = real_char if real_char else chr(codepoint)
        except Exception as e:
            loguru.logger.debug(f"混淆字体解析失败: {e}")
            char_map = {}

        self._font_cache[key] = char_map
        return char_map

    # ── 对外接口 ────────────────────────────────────────

    def decode(self, text: str, font_url_or_bytes=None) -> str:
        """还原文本中的混淆字符；任何环节失败均返回原文，不抛异常。

        :param font_url_or_bytes: 字体 URL 或字体字节；缺省时用预热记住的
                                  ``default_font``（接线侧每页预热一次即可）
        """
        if not text:
            return text
        if not _HAS_FONTTOOLS:
            loguru.logger.warning("fontTools 未安装，字体反混淆不可用，返回原文")
            return text
        table = self._load_table()
        if table is None:
            return text

        if font_url_or_bytes is None:
            font_url_or_bytes = self.default_font
        if font_url_or_bytes is None:
            return text

        try:
            if isinstance(font_url_or_bytes, (bytes, bytearray)):
                font_bytes = bytes(font_url_or_bytes)
            else:
                font_bytes = self._fetch_font_bytes(str(font_url_or_bytes))
            if not font_bytes:
                loguru.logger.debug("混淆字体下载失败，返回原文")
                return text

            char_map = self._char_map_for_font(font_bytes, table)
            if not char_map:
                return text
            return "".join(char_map.get(ord(ch), ch) for ch in text)
        except Exception as e:
            loguru.logger.debug(f"字体反混淆失败，返回原文: {e}")
            return text
