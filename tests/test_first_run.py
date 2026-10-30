# -*- coding: utf-8 -*-
"""首启向导模块导入与判定逻辑测试。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_first_run_module_imports_cleanly():
    """first_run 模块应能正常导入（曾因 Qt.pyqtSignal 拼写错误而崩溃）。"""
    import src.first_run as fr

    assert callable(fr.should_show_wizard)
    assert hasattr(fr, "FirstRunWizard")


def test_wizard_signal_signature():
    from PyQt6.QtCore import pyqtBoundSignal  # noqa: F401

    from src.first_run import FirstRunWizard

    assert hasattr(FirstRunWizard, "finished_with_account")
