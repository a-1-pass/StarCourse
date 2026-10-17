# _*_ coding:utf-8 _*_
"""font-cxsecret 字体映射表生成工具（许可证干净策略：不搬运上游 GPL 数据）。

用法（Git Bash / CMD 均可）：

    py tools/build_font_map.py "C:\\Windows\\Fonts\\simsun.ttc"
    py tools/build_font_map.py "C:\\Windows\\Fonts\\msyh.ttc" -o engine/data/font_map_table.json

原理：参考字体（宋体/微软雅黑等）与超星混淆字体的字形轮廓同源，
对参考字体中每个字符计算与 engine/font_decode 完全一致的轮廓特征哈希，
输出 ``glyph_hash -> 真实字符`` 的 JSON 映射表。运行时 FontDecoder 对页面
下载的混淆字体做同样的哈希，查表即还原真实文字。

注意：
- 参考字体需为本机合法持有的字体文件（Windows 自带字体即可）；
- 若超星更换字体源导致哈希不匹配，解码会自动降级为返回原文（不崩溃），
  换一款同源字体重新生成映射表即可；
- 生成产物为本地数据文件，不分发第三方字体本身。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

# 把 engine 目录加入 sys.path，复用 font_decode 的哈希实现（保证生成/运行一致）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE = os.path.join(_ROOT, "engine")
for _p in (_ENGINE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fontTools.ttLib import TTFont, TTCollection  # noqa: E402

# 直接引用解码模块的哈希函数，避免两套实现漂移；
# font_decode.decoder 仅 import utils（不触发配置之外的副作用），可安全复用
from font_decode.decoder import glyph_outline_hash  # noqa: E402

DEFAULT_OUTPUT = os.path.join(_ENGINE, "data", "font_map_table.json")


def _iter_fonts(path: str):
    """统一处理 .ttf/.otf 单字体与 .ttc 合集（取合集内每个子字体）。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"ttcf":
        collection = TTCollection(io.BytesIO(data))
        for font in collection.fonts:
            yield font
    else:
        yield TTFont(io.BytesIO(data))


def build_map_table(font_path: str) -> dict:
    """对参考字体的全部 cmap 字符计算轮廓哈希，返回 {hash: 字符}。"""
    table = {}
    collisions = 0
    for font in _iter_fonts(font_path):
        cmap = font.getBestCmap() or {}
        for codepoint, glyph_name in cmap.items():
            try:
                char = chr(codepoint)
            except ValueError:
                continue
            outline_hash = glyph_outline_hash(font, glyph_name)
            if not outline_hash:
                continue
            if outline_hash in table and table[outline_hash] != char:
                # 哈希冲突（不同字符同轮廓，如全半形变体）：先到先得，保留首个
                collisions += 1
                continue
            table[outline_hash] = char
    if collisions:
        print(f"提示：{collisions} 个哈希冲突已按先到先得跳过（不影响常规汉字）")
    return table


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 font-cxsecret 字形映射表")
    parser.add_argument("font", help="本机参考字体路径（如 C:\\Windows\\Fonts\\simsun.ttc）")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"输出路径（默认 {DEFAULT_OUTPUT}）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.font):
        print(f"错误：参考字体不存在: {args.font}")
        return 1

    print(f"正在分析参考字体: {args.font} ...")
    table = build_map_table(args.font)
    if not table:
        print("错误：未能从参考字体提取任何字形，请换一款字体重试")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    payload = {
        "version": 1,
        "source": os.path.basename(args.font),
        "count": len(table),
        "map": table,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    print(f"完成：{len(table)} 条映射已写入 {args.output}")
    print("现在可将 config.yml 中 FunConfig.deal-mission.font-decode.enable 置为 true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
