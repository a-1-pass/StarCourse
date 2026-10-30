# -*- coding: utf-8 -*-
"""首启向导模块导入与判定逻辑测试。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_first_run_module_imports_cleanly():
    """first_run 模块应能正常导入（曾因 Qt.pyqtSignal 拼写错误而崩溃）。"""
    import src.first_run as fr

    assert callable(fr.should_show_wizard)
    assert hasattr(fr, "FirstRunWizard")


def test_wizard_signal_signature():
    from src.first_run import FirstRunWizard

    assert hasattr(FirstRunWizard, "finished_with_account")


def test_wizard_prefills_existing_settings(qapp, monkeypatch):
    """已有配置时，向导第 3 页应预填当前值而不是写死默认值。"""
    import src.first_run as fr

    monkeypatch.setattr(fr, "read_settings", lambda path: {
        "video-mode": 0,
        "speed": 1.5,
        "max-concurrent-threads": 11,
        "enable-multi-thread": True,
    })
    w = fr.FirstRunWizard()
    try:
        assert w._s_speed.value() == pytest.approx(1.5)
        assert w._s_threads.value() == 11  # 现有值 11 不应被 SpinBox 上限截断
        assert w._s_video_mode.currentData() == 0
    finally:
        w.close()


def test_wizard_falls_back_to_defaults_without_config(qapp, monkeypatch):
    """配置缺失/读取失败时使用安全默认值。"""
    import src.first_run as fr

    def _boom(path):
        raise OSError("no config")

    monkeypatch.setattr(fr, "read_settings", _boom)
    w = fr.FirstRunWizard()
    try:
        assert w._s_speed.value() == pytest.approx(1.0)
        assert w._s_threads.value() == 3
        assert w._s_video_mode.currentData() == 1
    finally:
        w.close()

