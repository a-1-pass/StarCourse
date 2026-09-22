"""
账号管理面板 — 主界面视图

以表格形式展示所有已保存的账号信息，支持添加、删除、清空、刷新及批量运行操作。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from src.account_store import AccountProfile, AccountStore
from src.log_manager import log


class _AddProfileDialog(QDialog):
    """添加账号弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加账号")
        self.setMinimumWidth(420)
        self.resize(460, 360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        heading = QLabel("添加账号")
        heading.setStyleSheet("color:#2c3e50;font-size:20px;font-weight:bold;")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        sub = QLabel("输入学习通账号凭据")
        sub.setStyleSheet("color:#95a5a6;font-size:12px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)

        self._user = QLineEdit()
        self._user.setPlaceholderText("手机号 / 学号")
        layout.addWidget(self._user)

        self._pass = QLineEdit()
        self._pass.setPlaceholderText("密码")
        self._pass.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._pass)

        self._nick = QLineEdit()
        self._nick.setPlaceholderText("显示名称（可选）")
        layout.addWidget(self._nick)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("确认添加")
        confirm.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(confirm)
        layout.addLayout(row)

    def values(self) -> tuple[str, str, str]:
        return self._user.text().strip(), self._pass.text().strip(), self._nick.text().strip()


class AccountListPanel(QWidget):
    """账号列表主面板"""

    open_detail_requested = pyqtSignal(AccountProfile)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AccountStore()
        self._build_ui()
        self.refresh()

    # ---- UI 构建 ----
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 4)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        for label, slot, obj_name in [
            ("+  添加", self._on_add, "green_btn"),
            ("x  删除", self._on_delete, "red_btn"),
            ("x  清空全部", self._on_clear_all, "red_btn"),
            ("~  刷新", self._on_refresh, "cyan_btn"),
            (">> 批量运行", self._on_batch_run, "amber_btn"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName(obj_name)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        layout.addWidget(self._make_separator())

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["用户名", "显示名称", "状态", "目标课程", "操作"])
        self._table.setObjectName("account_table")
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        for col, w in enumerate([150, 100, 100, 320, 200]):
            self._table.setColumnWidth(col, w)
        self._table.verticalHeader().setDefaultSectionSize(58)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemDoubleClicked.connect(self._on_row_activated)
        layout.addWidget(self._table)
        layout.addWidget(self._make_separator())

    @staticmethod
    def _make_separator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 transparent,stop:0.5 rgba(56,139,253,.2),stop:1 transparent);"
            "max-height:2px;margin:8px 0;"
        )
        return sep

    # ---- 操作 ----
    def _on_add(self):
        dlg = _AddProfileDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        uname, pwd, nick = dlg.values()
        if not uname or not pwd:
            QMessageBox.warning(self, "提示", "用户名和密码不能为空。")
            return

        existing = self._store.get(uname)
        if existing:
            reply = QMessageBox.question(
                self, "账号已存在",
                f"账号「{uname}」已存在，是否更新密码？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._store.update_password(uname, pwd)
                if nick:
                    self._store.update_nickname(uname, nick)
                self.refresh()
            return

        if self._store.add(uname, pwd, nick):
            self.refresh()
            log.info(f"账号已添加: {uname}")

    def _on_delete(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "提示", "请先选择一个或多个账号。")
            return
        names = [self._table.item(r.row(), 0).text() for r in rows]
        preview = "\n".join(f"  - {n}" for n in names[:5])
        if len(names) > 5:
            preview += f"\n  ...（还有 {len(names) - 5} 个）"
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除以下 {len(names)} 个账号？\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for n in names:
                self._store.remove(n)
            self.refresh()

    def _on_clear_all(self):
        all_profiles = self._store.get_all()
        if not all_profiles:
            QMessageBox.information(self, "提示", "没有可清空的账号。")
            return
        preview = "\n".join(f"  - {p.username}" for p in all_profiles[:5])
        if len(all_profiles) > 5:
            preview += f"\n  ...（还有 {len(all_profiles) - 5} 个）"
        reply = QMessageBox.question(
            self, "确认清空",
            f"确定删除全部 {len(all_profiles)} 个账号？\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.clear_all()
            self.refresh()

    def _on_refresh(self):
        self.refresh()

    def _on_batch_run(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "操作提示", "按住 Ctrl 键多选账号，然后点击「批量运行」。")
            return
        to_run = []
        for idx in rows:
            uname = self._table.item(idx.row(), 0).text()
            profile = self._store.get(uname)
            if profile and profile.password and profile.status != "running":
                to_run.append(profile)
        if not to_run:
            QMessageBox.information(self, "提示", "所选账号无法启动（缺少密码或已在运行中）。")
            return
        reply = QMessageBox.question(
            self, "批量运行",
            f"确认启动 {len(to_run)} 个账号？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for p in to_run:
                self.open_detail_requested.emit(p)

    def _on_row_activated(self, item):
        row = item.row()
        uname_item = self._table.item(row, 0)
        if uname_item:
            profile = self._store.get(uname_item.text())
            if profile:
                self.open_detail_requested.emit(profile)

    # ---- 公共方法 ----
    def refresh(self):
        self._store.reload()
        profiles = self._store.get_all()
        self._table.setRowCount(len(profiles))
        for i, p in enumerate(profiles):
            self._table.setItem(i, 0, QTableWidgetItem(p.username))
            self._table.setItem(i, 1, QTableWidgetItem(p.nickname or "-"))
            status_map = {
                "running": ("运行中", Qt.GlobalColor.blue),
                "done":    ("已完成", Qt.GlobalColor.darkGreen),
                "failed":  ("失败",   Qt.GlobalColor.red),
                "idle":    ("空闲",   Qt.GlobalColor.gray),
            }
            text, color = status_map.get(p.status, (p.status, Qt.GlobalColor.gray))
            status_item = QTableWidgetItem(text)
            status_item.setForeground(color)
            self._table.setItem(i, 2, status_item)
            self._table.setItem(i, 3, QTableWidgetItem(p.target_course_name or "未设置"))

            manage = QPushButton("管理")
            manage.setObjectName("amber_btn")
            manage.setFixedHeight(36)
            manage.setProperty("uname", p.username)
            manage.clicked.connect(self._on_manage)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(manage)
            self._table.setCellWidget(i, 4, holder)
        log.info(f"账号列表已刷新 — {len(profiles)} 个账号")

    def _on_manage(self):
        btn = self.sender()
        uname = btn.property("uname")
        profile = self._store.get(uname)
        if profile:
            self.open_detail_requested.emit(profile)

    def update_status(self, username: str, status: str, progress: str = ""):
        self._store.update_status(username, status, progress)
        self.refresh()
