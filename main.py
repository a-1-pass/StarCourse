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
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFrame, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QStatusBar, QVBoxLayout, QWidget, QMessageBox,
)

from src.ui_layout import fit_size
from src.log_manager import log


class AppMainWindow(QMainWindow):
    """主应用窗口"""

    VERSION = "2.0.0"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("StarCourse — 超星学习通助手")
        self.setMinimumSize(640, 460)
        self._fit_window(1024, 700)
        self._detail_dialogs: dict[str, object] = {}
        self._build_ui()
        self._load_theme()
        self._maybe_show_wizard()

    def _fit_window(self, width: int, height: int):
        """初始尺寸不超过当前屏幕可用区域（适配小屏与高 DPI 缩放）。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(width, height)
            return
        avail = screen.availableGeometry()
        width, height = fit_size(avail.width(), avail.height(), width, height)
        self.resize(width, height)
        self.move(
            avail.x() + (avail.width() - width) // 2,
            avail.y() + (avail.height() - height) // 2,
        )

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
            log.error(f"模块加载失败: {exc}")
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
        # 兼容 PyInstaller 打包：frozen 时从 sys._MEIPASS 读取，开发时从项目根读取
        from src.resource_path import resource_path
        qss = resource_path("theme.qss")
        if os.path.exists(qss):
            with open(qss, "r", encoding="utf-8") as fh:
                self.setStyleSheet(fh.read())

    # ---- 首次启动向导 ----
    def _maybe_show_wizard(self):
        """若无保存账号，弹出首次启动引导向导。"""
        try:
            from src.first_run import should_show_wizard, FirstRunWizard
            if should_show_wizard():
                wizard = FirstRunWizard(self)
                wizard.finished_with_account.connect(self._on_wizard_account_added)
                wizard.exec()
        except Exception as exc:
            log.warning(f"首次启动向导异常: {exc}")

    def _on_wizard_account_added(self, username: str, password: str, nickname: str):
        """向导中添加了账号后刷新主界面列表。"""
        if self._account_panel:
            self._account_panel.refresh()
            # 自动打开该账号的工作台
            from src.account_store import AccountStore
            store = AccountStore()
            profile = store.get(username)
            if profile:
                self._open_detail(profile)

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


def _show_fatal_error(exc_type, exc_value, exc_tb):
    """全局未捕获异常处理：用图形化消息框替代命令行堆栈，降低用户恐慌。"""
    error_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical(f"未捕获异常:\n{error_text}")

    # 如果 QApplication 已存在，尝试弹窗；否则只能打印
    app = QApplication.instance()
    if app is not None:
        QMessageBox.critical(
            None, "StarCourse 运行时错误",
            f"程序遇到未预期的错误，即将退出。\n\n"
            f"错误类型: {exc_type.__name__}\n"
            f"错误信息: {exc_value}\n\n"
            f"建议操作:\n"
            f"1. 重启软件再试一次\n"
            f"2. 删除程序目录下的 profiles.json 后重试\n"
            f"3. 到 GitHub Issues 反馈此问题（可点击「导出日志」）\n\n"
            f"详细日志已写入 logs/ 目录。"
        )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    sys.excepthook = _show_fatal_error

    # 高 DPI 适配
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("StarCourse")
    app.setApplicationVersion(AppMainWindow.VERSION)
    window = AppMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
