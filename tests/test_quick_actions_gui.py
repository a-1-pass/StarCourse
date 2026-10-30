# -*- coding: utf-8 -*-
"""快捷操作面板 GUI 行为测试：签到入口已从快捷操作移除，签到页仍保留按类型签到。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

SIGN_TOOLTIPS = [
    "扫描并执行普通签到",
    "扫描并执行手势签到",
    "扫描并执行位置签到",
    "扫描并执行二维码签到",
    "扫描并执行拍照签到",
]


class _StubStore:
    """只实现窗口初始化/登录流程会调用的存储接口，避免真实读写磁盘。"""

    def update_cookie(self, *args, **kwargs):
        pass

    def remove_cookie(self, *args, **kwargs):
        pass

    def update_status(self, *args, **kwargs):
        pass

    def update_progress(self, *args, **kwargs):
        pass


@pytest.fixture(scope="module")
def workshop():
    app = QApplication.instance() or QApplication([])
    from gui.course_workshop import CourseWorkshop
    from src.account_store import AccountProfile

    dlg = CourseWorkshop(AccountProfile(username="", password=""), store=_StubStore())
    yield dlg
    dlg.close()
    app.processEvents()


def test_quick_actions_no_longer_expose_signin_entry(workshop):
    """快捷操作区不再出现“签到:”分组标签。"""
    labels = [lbl.text() for lbl in workshop.findChildren(QLabel)]
    assert "签到:" not in labels


def test_quick_signin_dead_code_removed():
    """仅供快捷入口使用的 _on_action_signin_quick 已删除。"""
    from gui.course_workshop import CourseWorkshop

    assert not hasattr(CourseWorkshop, "_on_action_signin_quick")


def test_signin_page_keeps_five_signin_types(workshop):
    """签到管理页的“按类型签到”按钮不随快捷入口一起被删。"""
    tips = [btn.toolTip() for btn in workshop.findChildren(QPushButton)]
    for tip in SIGN_TOOLTIPS:
        assert tips.count(tip) == 1, f"{tip} 应只在签到页出现一次，实际 {tips.count(tip)} 次"
