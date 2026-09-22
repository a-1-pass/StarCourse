"""
StarCourse — 超星学习通自动刷课桌面应用

基于 PyQt6 的图形界面客户端，支持多账号管理、课程浏览、章节选择、
任务队列调度、AI 智能答题、自动签到及资源下载。

用法::

    python main.py
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt6.QtWidgets import (
    QApplication, QFrame, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)


class AppMainWindow(QMainWindow):
    """主应用窗口"""

    VERSION = "2.0.0"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("StarCourse — 超星学习通助手")
        self.setMinimumSize(900, 600)
        self.resize(1024, 700)
        self._detail_dialogs: dict[str, object] = {}
        self._build_ui()
        self._load_theme()

    # ---- 界面构建 ----
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central_widget")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部标题栏
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)
        title = QLabel("StarCourse")
        title.setObjectName("header_title")
        h_layout.addWidget(title)
        root.addWidget(header)

        # 主体内容区
        body = QWidget()
        body.setObjectName("content_area")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 18, 22, 14)
        body_layout.setSpacing(12)

        self._stack = QStackedWidget()
        self._account_panel = None

        try:
            from gui.account_panel import AccountListPanel
            from gui.course_workshop import CourseWorkshop
            from src.account_store import AccountProfile

            self._CourseWorkshop = CourseWorkshop
            self._AccountProfile = AccountProfile
            self._account_panel = AccountListPanel()
            self._account_panel.open_detail_requested.connect(self._open_detail)
            self._stack.addWidget(self._account_panel)
            self._platform_ok = True
        except Exception as exc:
            print(f"模块加载失败: {exc}")
            self._platform_ok = False
            err = QLabel(f"模块加载失败: {exc}")
            err.setObjectName("error_label")
            self._stack.addWidget(err)

        body_layout.addWidget(self._stack, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("content_separator")
        body_layout.addWidget(sep)

        self._statusbar = QStatusBar()
        self._statusbar.setObjectName("status_bar")
        self._statusbar.showMessage("就绪")
        body_layout.addWidget(self._statusbar)

        root.addWidget(body)

    def _load_theme(self):
        qss = os.path.join(_PROJECT_ROOT, "theme.qss")
        if os.path.exists(qss):
            with open(qss, "r", encoding="utf-8") as fh:
                self.setStyleSheet(fh.read())

    # ---- 账号详情弹窗 ----
    def _open_detail(self, profile):
        key = f"profile_{profile.username}"
        if key in self._detail_dialogs:
            dlg = self._detail_dialogs[key]
            if dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return
            del self._detail_dialogs[key]

        dlg = self._CourseWorkshop(profile)
        dlg.status_updated.connect(self._on_status_updated)
        dlg.finished.connect(lambda _, k=key: self._on_detail_closed(k))
        self._detail_dialogs[key] = dlg
        dlg.show()
        self._statusbar.showMessage(f"已打开: {profile.username}")

    def _on_status_updated(self, username: str, status: str, progress: str):
        if self._account_panel:
            self._account_panel.update_status(username, status, progress)

    def _on_detail_closed(self, key: str):
        self._detail_dialogs.pop(key, None)
        if self._account_panel:
            self._account_panel.refresh()

    def closeEvent(self, event):
        for dlg in list(self._detail_dialogs.values()):
            dlg.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("StarCourse")
    window = AppMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
