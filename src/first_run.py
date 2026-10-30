"""
StarCourse — 首次启动引导向导

新用户打开软件时自动弹出，三步完成初始设置：
1. 欢迎页（功能概览）
2. 添加第一个账号（手机号/学号 + 密码）
3. 快速配置（视频倍速、并发数、学习模式）

所有配置均内置默认值，用户可一路「下一步」跳过。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from src.account_store import AccountStore
from src.runtime_settings import save_settings, read_settings, config_yml_path, SETTING_SPECS


class FirstRunWizard(QDialog):
    """首次启动引导向导（3 页）"""

    finished_with_account = pyqtSignal(str, str, str)  # username, password, nickname

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 StarCourse")
        self.setMinimumSize(560, 440)
        self.resize(600, 480)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self._page_idx = 0
        self._settings = self._load_existing_settings()
        self._build_ui()

    # ── 现有配置预填 ──────────────────────────────────────────

    def _load_existing_settings(self) -> dict:
        """读取现有 config.yml 作为向导预填值；缺失/读取失败时用安全默认值。"""
        defaults = {"video-mode": 1, "speed": 1.0, "max-concurrent-threads": 3}
        try:
            current = read_settings(config_yml_path())
        except Exception:
            return defaults
        return {
            "video-mode": current.get("video-mode", defaults["video-mode"]),
            "speed": current.get("speed", defaults["speed"]),
            "max-concurrent-threads": current.get(
                "max-concurrent-threads", defaults["max-concurrent-threads"]
            ),
        }

    # ── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部标题栏（品牌色）
        header = QFrame()
        header.setObjectName("wizard_header")
        header.setFixedHeight(64)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0ea5e9, stop:1 #6366f1);"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("🌟  StarCourse")
        title.setStyleSheet("color:white;font-size:20px;font-weight:bold;")
        hl.addWidget(title)
        sub = QLabel("超星学习通自动刷课助手")
        sub.setStyleSheet("color:rgba(255,255,255,0.85);font-size:13px;")
        hl.addWidget(sub)
        hl.addStretch()
        root.addWidget(header)

        # 步骤指示器
        step_bar = QFrame()
        step_bar.setFixedHeight(40)
        step_bar.setStyleSheet("background:#0f172a;")
        sl = QHBoxLayout(step_bar)
        sl.setContentsMargins(24, 0, 24, 0)
        self._step_labels: list[QLabel] = []
        for i, text in enumerate(["1. 欢迎", "2. 添加账号", "3. 运行设置"], 1):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{'#38bdf8' if i == 1 else '#64748b'};"
                "font-size:12px;font-weight:bold;"
            )
            self._step_labels.append(lbl)
            sl.addWidget(lbl)
            if i < 3:
                sep = QLabel("→")
                sep.setStyleSheet("color:#334155;margin:0 8px;")
                sl.addWidget(sep)
        sl.addStretch()
        root.addWidget(step_bar)

        # 页面堆叠
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_page_welcome())
        self._stack.addWidget(self._build_page_account())
        self._stack.addWidget(self._build_page_settings())
        root.addWidget(self._stack, 1)

        # 底部按钮栏
        btn_bar = QFrame()
        btn_bar.setFixedHeight(56)
        btn_bar.setStyleSheet("background:#0f172a;border-top:1px solid #1e293b;")
        bl = QHBoxLayout(btn_bar)
        bl.setContentsMargins(24, 0, 24, 0)
        bl.addStretch()

        self._btn_back = QPushButton("上一步")
        self._btn_back.setObjectName("detail_btn")
        self._btn_back.setMinimumWidth(90)
        self._btn_back.clicked.connect(self._on_back)
        self._btn_back.setEnabled(False)
        bl.addWidget(self._btn_back)

        self._btn_next = QPushButton("下一步")
        self._btn_next.setObjectName("start_btn")
        self._btn_next.setMinimumWidth(110)
        self._btn_next.clicked.connect(self._on_next)
        bl.addWidget(self._btn_next)

        root.addWidget(btn_bar)

    # ── 页面 1: 欢迎 ────────────────────────────────────────

    def _build_page_welcome(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(16)

        intro = QLabel(
            "<h2 style='color:#e2e8f0;margin-bottom:4px;'>三步上手，全自动刷课</h2>"
            "<p style='color:#94a3b8;line-height:1.6;'>"
            "StarCourse 是专为超星学习通打造的桌面自动化工具。\n"
            "无需安装浏览器、无需编写脚本，添加账号后即可一键完成视频、测验、签到等任务。"
            "</p>"
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        features = [
            ("🖥️  纯桌面客户端", "PyQt6 图形界面，多账号独立管理"),
            ("⚡  五种签到一键完成", "普通 / 手势 / 位置 / 二维码 / 拍照"),
            ("🎯  章节定点刷取", "勾选想刷的章节，不刷全部"),
            ("🧠  AI 智能答题", "DeepSeek / GPT / Claude 多模型容灾"),
            ("📥  资源批量下载", "课件、PPT、视频一键导出"),
        ]
        for icon_title, desc in features:
            row = QHBoxLayout()
            it = QLabel(f"<b style='color:#38bdf8;'>{icon_title}</b>")
            it.setMinimumWidth(160)
            row.addWidget(it)
            d = QLabel(f"<span style='color:#94a3b8;'>{desc}</span>")
            row.addWidget(d)
            row.addStretch()
            v.addLayout(row)

        v.addStretch()

        hint = QLabel(
            "<span style='color:#64748b;font-size:12px;'>"
            "💡 提示：软件已内置默认运行参数，可直接「下一步」跳过设置。"
            "</span>"
        )
        hint.setWordWrap(True)
        v.addWidget(hint)
        return w

    # ── 页面 2: 添加账号 ────────────────────────────────────

    def _build_page_account(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(16)

        title = QLabel("<h3 style='color:#e2e8f0;'>添加第一个学习通账号</h3>")
        v.addWidget(title)

        sub = QLabel(
            "<p style='color:#94a3b8;'>"
            "输入你的学习通手机号/学号和密码。信息仅保存在本地，不会上传到任何服务器。"
            "</p>"
        )
        sub.setWordWrap(True)
        v.addWidget(sub)

        form = QVBoxLayout()
        form.setSpacing(12)

        self._acc_user = QLineEdit()
        self._acc_user.setPlaceholderText("手机号 / 学号")
        self._acc_user.setMinimumHeight(36)
        form.addWidget(self._acc_user)

        self._acc_pass = QLineEdit()
        self._acc_pass.setPlaceholderText("密码")
        self._acc_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._acc_pass.setMinimumHeight(36)
        form.addWidget(self._acc_pass)

        self._acc_nick = QLineEdit()
        self._acc_nick.setPlaceholderText("显示名称（可选，如「大号」「小号」）")
        self._acc_nick.setMinimumHeight(36)
        form.addWidget(self._acc_nick)

        self._acc_skip = QCheckBox("暂时跳过，稍后手动添加")
        self._acc_skip.setStyleSheet("color:#94a3b8;")
        self._acc_skip.toggled.connect(self._on_skip_toggled)
        form.addWidget(self._acc_skip)

        v.addLayout(form)
        v.addStretch()
        return w

    def _on_skip_toggled(self, checked: bool):
        for le in (self._acc_user, self._acc_pass, self._acc_nick):
            le.setEnabled(not checked)

    # ── 页面 3: 运行设置 ────────────────────────────────────

    def _build_page_settings(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(16)

        title = QLabel("<h3 style='color:#e2e8f0;'>运行参数（可选）</h3>")
        v.addWidget(title)

        sub = QLabel(
            "<p style='color:#94a3b8;'>"
            "以下设置已按「安全且高效」自动填好，新手建议直接点「完成」。"
            "</p>"
        )
        sub.setWordWrap(True)
        v.addWidget(sub)

        grid = QVBoxLayout()
        grid.setSpacing(14)

        # 视频模式
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("<b style='color:#e2e8f0;'>视频完成方式:</b>"))
        self._s_video_mode = QComboBox()
        self._s_video_mode.addItem("等时长刷取（推荐，更稳）", 1)
        self._s_video_mode.addItem("立即完成（ fastest ）", 0)
        idx = self._s_video_mode.findData(self._settings["video-mode"])
        self._s_video_mode.setCurrentIndex(idx if idx >= 0 else 0)
        row1.addWidget(self._s_video_mode)
        row1.addStretch()
        grid.addLayout(row1)

        # 倍速
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("<b style='color:#e2e8f0;'>视频倍速:</b>"))
        self._s_speed = QDoubleSpinBox()
        self._s_speed.setRange(1.0, 2.0)
        self._s_speed.setSingleStep(0.1)
        self._s_speed.setValue(min(2.0, max(1.0, float(self._settings["speed"]))))
        self._s_speed.setSuffix(" x")
        row2.addWidget(self._s_speed)
        row2.addWidget(QLabel("<span style='color:#64748b;font-size:12px;'>建议不超过 2.0</span>"))
        row2.addStretch()
        grid.addLayout(row2)

        # 并发
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("<b style='color:#e2e8f0;'>并发线程数:</b>"))
        self._s_threads = QSpinBox()
        self._s_threads.setRange(1, 32)
        self._s_threads.setValue(min(32, max(1, int(self._settings["max-concurrent-threads"]))))
        row3.addWidget(self._s_threads)
        row3.addWidget(QLabel("<span style='color:#64748b;font-size:12px;'>新手建议 3，稳定后再加</span>"))
        row3.addStretch()
        grid.addLayout(row3)

        # 学习模式
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("<b style='color:#e2e8f0;'>学习模式:</b>"))
        self._s_mode = QComboBox()
        self._s_mode.addItem("闯关模式（默认，逐章解锁）", "stage")
        self._s_mode.addItem("顺序学习（一次性遍历）", "sequential")
        self._s_mode.addItem("并发学习（多章同时）", "concurrent")
        row4.addWidget(self._s_mode)
        row4.addStretch()
        grid.addLayout(row4)

        # 答题方式
        row5 = QHBoxLayout()
        row5.addWidget(QLabel("<b style='color:#e2e8f0;'>答题方式:</b>"))
        self._s_answer = QComboBox()
        self._s_answer.addItem("首选答案（本地策略，不联网）", "first")
        self._s_answer.addItem("随机选择（本地策略，不联网）", "random")
        row5.addWidget(self._s_answer)
        row5.addStretch()
        grid.addLayout(row5)

        v.addLayout(grid)

        # 风险提示
        warn = QLabel(
            "<p style='color:#fbbf24;font-size:12px;margin-top:8px;'>"
            "⚠️ 使用自动化工具存在被平台风控的风险，请理性使用。建议倍速≤2.0、并发≤5、避免深夜高频运行。"
            "</p>"
        )
        warn.setWordWrap(True)
        v.addWidget(warn)
        v.addStretch()
        return w

    # ── 导航逻辑 ────────────────────────────────────────────

    def _on_next(self):
        if self._page_idx == 0:
            self._goto_page(1)
        elif self._page_idx == 1:
            if not self._acc_skip.isChecked():
                if not self._acc_user.text().strip() or not self._acc_pass.text().strip():
                    QMessageBox.warning(self, "提示", "请输入手机号/学号和密码，或勾选「暂时跳过」。")
                    return
            self._goto_page(2)
        elif self._page_idx == 2:
            self._apply_settings()
            if not self._acc_skip.isChecked():
                self._save_account()
            self.accept()

    def _on_back(self):
        if self._page_idx > 0:
            self._goto_page(self._page_idx - 1)

    def _goto_page(self, idx: int):
        self._page_idx = idx
        self._stack.setCurrentIndex(idx)
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(
                f"color:{'#38bdf8' if i == idx else '#64748b'};"
                "font-size:12px;font-weight:bold;"
            )
        self._btn_back.setEnabled(idx > 0)
        self._btn_next.setText("完成" if idx == 2 else "下一步")

    # ── 数据持久化 ──────────────────────────────────────────

    def _save_account(self):
        store = AccountStore()
        user = self._acc_user.text().strip()
        pwd = self._acc_pass.text().strip()
        nick = self._acc_nick.text().strip()
        store.add(user, pwd, nick)
        self.finished_with_account.emit(user, pwd, nick)

    def _apply_settings(self):
        """把向导中的设置写回 config.yml（只改 GUI 暴露的参数，保留其余默认值）。"""
        try:
            values = {
                "video-mode": self._s_video_mode.currentData(),
                "speed": self._s_speed.value(),
                "max-concurrent-threads": self._s_threads.value(),
                "enable-multi-thread": self._s_threads.value() > 1,
            }
            path = config_yml_path()
            save_settings(path, values)
        except Exception:
            pass


# ── 便捷函数 ──────────────────────────────────────────────

def should_show_wizard() -> bool:
    """判断是否需要展示首次启动向导：无任何已保存账号时触发。"""
    try:
        store = AccountStore()
        return len(store.get_all()) == 0
    except Exception:
        return True
