# _*_ coding:utf-8 _*_
"""font-cxsecret 字体反混淆（可插拔模块，config.yml 默认关闭）。

公开 API：
- ``FontDecoder``               解码器主体（decode / 缓存 / 降级）
- ``find_secret_font_url``      从页面 HTML 提取混淆字体地址
- ``get_font_decode_config``    读取 config.yml 开关配置
- ``build_decoder_from_page``   接线用一键入口：开关开启且页面含混淆字体
                                引用时返回共享 FontDecoder 实例，否则 None
"""
from __future__ import annotations

import loguru

from .decoder import (
    DEFAULT_MAP_TABLE,
    FontDecoder,
    find_secret_font_url,
    glyph_outline_hash,
)

__all__ = [
    "DEFAULT_MAP_TABLE",
    "FontDecoder",
    "find_secret_font_url",
    "glyph_outline_hash",
    "get_font_decode_config",
    "build_decoder_from_page",
]


def get_font_decode_config() -> dict:
    """读取 config.yml 的 FunConfig.deal-mission.font-decode 节；读取失败返回空 dict。"""
    try:
        from config import GloConfig
        data = GloConfig.data or {}
        cfg = (data.get("FunConfig", {}) or {}).get("deal-mission", {}) or {}
        section = cfg.get("font-decode", {})
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


# 进程级共享解码器：字体解析结果跨 Quiz/Work 实例复用，避免重复下载
_shared_decoder: FontDecoder = None


def build_decoder_from_page(page_html: str):
    """接线入口：开关开启且页面含 font-cxsecret 字体引用时返回 FontDecoder。

    - 开关关闭 / 页面无混淆字体 / 初始化失败：返回 None（调用方零行为变化）；
    - 字体在此处预下载解析一次，失败同样返回 None（不阻塞答题主流程）。
    """
    cfg = get_font_decode_config()
    if not cfg.get("enable"):
        return None

    font_url = find_secret_font_url(page_html)
    if not font_url:
        return None

    global _shared_decoder
    try:
        if _shared_decoder is None:
            map_table = str(cfg.get("map-table", "") or "").strip() or None
            _shared_decoder = FontDecoder(map_table_path=map_table)
        # 预热：提前下载并解析字体（内部按内容哈希缓存，重复调用近乎零成本）；
        # 用占位字符试跑 decode 触发下载+建映射，失败时 decode 内部已降级不抛异常
        _shared_decoder.default_font = font_url
        _shared_decoder.decode("预", font_url)
        return _shared_decoder
    except Exception as e:
        loguru.logger.debug(f"字体反混淆初始化失败: {e}")
        return None
