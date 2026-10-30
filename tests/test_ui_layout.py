# _*_ coding:utf-8 _*_
"""窗口尺寸自适应：确保初始窗口不超过可用屏幕区域。"""
from src.ui_layout import fit_size


def test_keeps_preferred_on_large_screen():
    assert fit_size(1920, 1080, 1250, 780) == (1250, 780)


def test_clamps_to_small_screen():
    # 1366x768 + 125% 缩放 -> 约 1093x614 可用逻辑像素
    wide, high = fit_size(1093, 614, 1250, 780)
    assert wide == 1061
    assert high == 582


def test_never_below_usable_minimum():
    assert fit_size(400, 300, 1250, 780) == (368, 268)


def test_degenerate_available_area():
    assert fit_size(0, 0, 800, 600) == (320, 240)
