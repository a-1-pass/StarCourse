# _*_ coding:utf-8 _*_
"""窗口尺寸自适应工具。

把期望尺寸钳制到当前屏幕可用区域，避免在 1366x768 / 高 DPI 缩放下
窗口初始尺寸超过屏幕。
"""
from __future__ import annotations

MIN_WIDTH = 320
MIN_HEIGHT = 240


def fit_size(
    available_width: int,
    available_height: int,
    preferred_width: int,
    preferred_height: int,
    margin: int = 32,
) -> tuple[int, int]:
    """返回不超过 ``available - margin`` 的窗口尺寸（含安全下限）。"""
    width = max(MIN_WIDTH, min(preferred_width, available_width - margin))
    height = max(MIN_HEIGHT, min(preferred_height, available_height - margin))
    return width, height
