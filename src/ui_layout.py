# _*_ coding:utf-8 _*_
"""窗口尺寸自适应工具。

把期望尺寸钳制到当前屏幕可用区域，避免在 1366x768 / 高 DPI 缩放下
窗口初始尺寸超过屏幕。
"""
from __future__ import annotations

MIN_WIDTH = 320
MIN_HEIGHT = 240

#: 初始窗口最多占可用屏幕的比例，避免在小屏上铺满整个屏幕
MAX_SCREEN_FRACTION = 0.85


def fit_size(
    available_width: int,
    available_height: int,
    preferred_width: int,
    preferred_height: int,
    margin: int = 32,
    max_fraction: float = MAX_SCREEN_FRACTION,
) -> tuple[int, int]:
    """返回同时满足以下约束的窗口尺寸（含安全下限）：

    - 不超过 ``available - margin``（不贴边）；
    - 不超过可用屏幕的 ``max_fraction``（小屏上不铺满屏幕）；
    - 不超出期望尺寸。
    """
    width_cap = min(available_width - margin, int(available_width * max_fraction))
    height_cap = min(available_height - margin, int(available_height * max_fraction))
    width = max(MIN_WIDTH, min(preferred_width, width_cap))
    height = max(MIN_HEIGHT, min(preferred_height, height_cap))
    return width, height
