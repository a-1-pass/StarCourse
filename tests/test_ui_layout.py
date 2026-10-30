# _*_ coding:utf-8 _*_
"""窗口尺寸自适应：确保初始窗口不超过可用屏幕区域。"""
from src.ui_layout import fit_size


def test_keeps_preferred_on_large_screen():
    assert fit_size(1920, 1080, 1250, 780) == (1250, 780)


def test_clamps_to_small_screen():
    # 1366x768 + 125% 缩放 -> 约 1093x614 可用逻辑像素
    # 仅贴边会让窗口几乎铺满屏幕，因此再按屏幕比例留出边距
    wide, high = fit_size(1093, 614, 1250, 780)
    assert wide == int(1093 * 0.85)
    assert high == int(614 * 0.85)


def test_avoids_nearly_fullscreen_window():
    wide, high = fit_size(1093, 614, 1250, 780)
    assert wide <= 1093 - 100
    assert high <= 614 - 80


def test_custom_fraction():
    assert fit_size(1000, 800, 900, 700, max_fraction=0.5) == (500, 400)


def test_never_below_usable_minimum():
    wide, high = fit_size(400, 300, 1250, 780)
    assert wide == 340
    assert high == 255


def test_degenerate_available_area():
    assert fit_size(0, 0, 800, 600) == (320, 240)
