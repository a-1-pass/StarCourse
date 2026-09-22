"""
课程操作工作台对话框

为单个账号提供完整的功能工作区：登录、课程列表、章节选择、
任务队列管理、签到处理、资源下载以及实时进度监控。
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from src.account_store import AccountProfile
from src.log_manager import log


# ═══════════════════════════════════════════════════════════════════
#  后台工作线程
# ═══════════════════════════════════════════════════════════════════

class _LoginWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, username: str, password: str):
        super().__init__()
        self._user = username
        self._pass = password
        self.client = None

    def run(self):
        try:
            from src.engine_adapter import EngineAdapter
            self.client = EngineAdapter()
            ok, msg = self.client.login(self._user, self._pass)
            self.finished.emit(ok, msg)
        except Exception as exc:
            self.finished.emit(False, f"登录失败: {exc}")


class _CookieLoginWorker(QThread):
    """使用已保存的 cookie 尝试免密登录"""
    finished = pyqtSignal(bool, str)

    def __init__(self, cookie: str):
        super().__init__()
        self._cookie = cookie
        self.client = None

    def run(self):
        try:
            from src.engine_adapter import EngineAdapter
            self.client = EngineAdapter()
            ok, msg = self.client.login_with_cookie(self._cookie)
            self.finished.emit(ok, msg)
        except Exception as exc:
            self.finished.emit(False, f"Cookie 登录失败: {exc}")


class _CourseWorker(QThread):
    finished = pyqtSignal(bool, list, str)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            ok, courses, msg = self._client.fetch_courses()
            self.finished.emit(ok, courses, msg)
        except Exception as exc:
            self.finished.emit(False, [], f"获取课程失败: {exc}")


class _ChapterWorker(QThread):
    finished = pyqtSignal(bool, list, str)

    def __init__(self, client, course_id, class_id, cpi):
        super().__init__()
        self._client = client
        self._cid = course_id
        self._clid = class_id
        self._cpi = cpi

    def run(self):
        try:
            ok, chapters, msg = self._client.fetch_chapters(self._cid, self._clid, self._cpi)
            self.finished.emit(ok, chapters, msg)
        except Exception as exc:
            self.finished.emit(False, [], f"获取章节失败: {exc}")


class _AutomationWorker(QThread):
    log_signal = pyqtSignal(str)
    finished = pyqtSignal(bool, dict, str)

    def __init__(self, client, course_id, class_id, strategy="first", cpi="",
                 chapter_ids=None, multi_thread=False, max_threads=5, max_retries=2,
                 use_tiku=None):
        super().__init__()
        self._client = client
        self._cid = course_id
        self._clid = class_id
        self._cpi = cpi
        self._strategy = strategy
        self._chapters = chapter_ids
        self._multi = multi_thread
        self._max_threads = max_threads
        self._max_retries = max_retries
        self._use_tiku = use_tiku
        self._running = True

    def run(self):
        import time
        try:
            self.log_signal.emit(f"开始执行自动化任务: 课程ID={self._cid}")

            original_out, original_err = sys.stdout, sys.stderr
            capturer = _StreamCapture(self.log_signal.emit, original_out)
            sys.stdout = capturer
            sys.stderr = capturer

            try:
                ok, stats, msg = False, {}, ""
                attempt = 0
                while attempt <= self._max_retries and self._running:
                    ok, stats, msg = self._client.auto_complete(
                        self._cid, self._clid, self._strategy, self._cpi,
                        on_progress=self.log_signal.emit,
                        chapter_ids=self._chapters,
                        multi_thread=self._multi,
                        max_threads=self._max_threads,
                        use_tiku=self._use_tiku,
                    )
                    if ok:
                        self.log_signal.emit("[完成] 自动化任务执行成功")
                        break
                    attempt += 1
                    if attempt <= self._max_retries and self._running:
                        self.log_signal.emit(f"[重试] 第 {attempt}/{self._max_retries} 次尝试失败，3 秒后重试...")
                        time.sleep(3)
                    else:
                        self.log_signal.emit(f"[失败] 自动化任务在 {attempt - 1} 次重试后仍未成功")
                self.finished.emit(ok, stats, msg)
            finally:
                sys.stdout = original_out
                sys.stderr = original_err
        except Exception as exc:
            self.log_signal.emit(f"[错误] 致命异常: {exc}")
            self.finished.emit(False, {}, str(exc))

    def stop(self):
        self._running = False


class _SignInScanWorker(QThread):
    """扫描全部课程中待签到的活动"""
    finished = pyqtSignal(bool, list, str)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            ok, activities, msg = self._client.scan_sign_ins()
            self.finished.emit(ok, activities, msg)
        except Exception as exc:
            self.finished.emit(False, [], f"扫描失败: {exc}")


class _SignInExecWorker(QThread):
    """执行单次签到"""
    log_signal = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, client, course_id, class_id, activity, location, name):
        super().__init__()
        self._client = client
        self._cid = course_id
        self._clid = class_id
        self._activity = activity
        self._loc = location
        self._name = name

    def run(self):
        try:
            ok, msg = self._client.do_sign_in(self._cid, self._clid, self._activity, self._loc, self._name)
            self.finished.emit(ok, msg)
        except Exception as exc:
            self.finished.emit(False, f"签到失败: {exc}")


class _SignInByTypeWorker(QThread):
    """按签到类型批量签到"""
    log_signal = pyqtSignal(str)
    finished = pyqtSignal(bool, list, str)

    def __init__(self, client, sign_type, location=None):
        super().__init__()
        self._client = client
        self._type = sign_type
        self._loc = location

    def run(self):
        ok, results, msg = self._client.sign_in_by_type(
            self._type, self._loc, on_progress=self.log_signal.emit)
        self.finished.emit(ok, results, msg)


class _DownloadScanWorker(QThread):
    """扫描课程可下载资源"""
    finished = pyqtSignal(bool, list, str)

    def __init__(self, client, course_id, class_id):
        super().__init__()
        self._client = client
        self._cid = course_id
        self._clid = class_id

    def run(self):
        try:
            ok, items, msg = self._client.scan_downloadable(self._cid, self._clid)
            self.finished.emit(ok, items, msg)
        except Exception as exc:
            self.finished.emit(False, [], f"扫描失败: {exc}")


class _DownloadExecWorker(QThread):
    """下载单个文件"""
    log_signal = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)

    def __init__(self, client, course_id, file_info, output_dir):
        super().__init__()
        self._client = client
        self._cid = course_id
        self._info = file_info
        self._dir = output_dir

    def run(self):
        try:
            ok, msg, saved_path = self._client.download_file(self._cid, self._info, self._dir)
            self.finished.emit(ok, msg, self._info.get("name", ""))
        except Exception as exc:
            self.finished.emit(False, str(exc), self._info.get("name", ""))


class _StreamCapture:
    """将 print 输出重定向到回调函数，同时保留原始输出流"""
    def __init__(self, callback, fallback):
        self._cb = callback
        self._fallback = fallback

    def write(self, text):
        if text.strip():
            self._cb(text.rstrip("\n"))
        if self._fallback:
            self._fallback.write(text)

    def flush(self):
        if self._fallback:
            self._fallback.flush()


# ═══════════════════════════════════════════════════════════════════
#  主对话框
# ═══════════════════════════════════════════════════════════════════

class CourseWorkshop(QDialog):
    status_updated = pyqtSignal(str, str, str)

    MODES = ["auto", "signin", "download"]

    def __init__(self, profile: AccountProfile, store=None, parent=None):
        super().__init__(parent)
        self._profile = profile
        self._store = store
        if store is None:
            from src.account_store import AccountStore
            self._store = AccountStore()

        self._client = None
        self._courses = []
        self._current_course = None
        self._chapters = []
        self._queue: list[dict] = []
        self._queue_active = False
        self._queue_paused = False
        self._queue_index = 0

        self._worker: _AutomationWorker | None = None
        self._login_worker: _LoginWorker | None = None
        self._cookie_login_worker: _CookieLoginWorker | None = None
        self._course_worker: _CourseWorker | None = None
        self._chapter_worker: _ChapterWorker | None = None
        self._signin_scan_worker: _SignInScanWorker | None = None
        self._signin_exec_worker: _SignInExecWorker | None = None
        self._dl_scan_worker: _DownloadScanWorker | None = None
        self._dl_exec_worker: _DownloadExecWorker | None = None

        self._log_lines: list[str] = []
        self._signin_activities: list[dict] = []
        self._dl_items: list[dict] = []
        self._dl_all_items: list[dict] = []

        self.setWindowTitle(f"StarCourse — {profile.username}")
        self.resize(1250, 780)
        self.setModal(False)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._build_ui()
        self._auto_login()

    # ── 布局 ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ── 顶部工具栏 ──
        bar = QHBoxLayout()
        self._status_label = QLabel(f"状态: {self._profile.status}")
        self._status_label.setObjectName("status_label")
        bar.addWidget(self._status_label)
        bar.addStretch()

        self._login_btn = QPushButton("登录")
        self._login_btn.setObjectName("detail_btn")
        self._login_btn.clicked.connect(self._on_login)
        bar.addWidget(self._login_btn)

        self._refresh_btn = QPushButton("刷新课程列表")
        self._refresh_btn.setObjectName("detail_btn")
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.clicked.connect(self._on_refresh_courses)
        bar.addWidget(self._refresh_btn)
        root.addLayout(bar)

        # ── 模式切换 ──
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)

        self._mode_btns: dict[str, QPushButton] = {}
        for mode_id, label in [("auto", "自动任务"), ("signin", "签到管理"), ("download", "资源下载")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("mode_btn")
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda checked, m=mode_id: self._switch_mode(m))
            mode_row.addWidget(btn)
            self._mode_btns[mode_id] = btn
        mode_row.addStretch()
        root.addLayout(mode_row)

        # ── 堆叠页面 ──
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_auto_page())     # index 0
        self._stack.addWidget(self._build_signin_page())   # index 1
        self._stack.addWidget(self._build_download_page()) # index 2
        root.addWidget(self._stack, 1)

        # ── 底部 ──
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("detail_btn")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._switch_mode("auto")

    def _switch_mode(self, mode: str):
        for m, btn in self._mode_btns.items():
            btn.setChecked(m == mode)
        page_map = {"auto": 0, "signin": 1, "download": 2}
        self._stack.setCurrentIndex(page_map.get(mode, 0))

    # ═══════════════════════════════════════════════════════════════
    #  页面 0 — 自动任务
    # ═══════════════════════════════════════════════════════════════

    def _build_auto_page(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("main_splitter")
        splitter.addWidget(self._build_left_pane())
        splitter.addWidget(self._build_middle_pane())
        splitter.addWidget(self._build_right_pane())
        splitter.setSizes([340, 300, 380])
        outer.addWidget(splitter, 1)

        return w

    def _build_left_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 课程列表
        course_box = QGroupBox("课程列表")
        cl = QVBoxLayout(course_box)
        self._course_tbl = QTableWidget()
        self._course_tbl.setColumnCount(4)
        self._course_tbl.setHorizontalHeaderLabels(["", "课程名称", "课程ID", "状态"])
        self._course_tbl.horizontalHeader().setStretchLastSection(True)
        self._course_tbl.setColumnWidth(0, 36)
        self._course_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._course_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._course_tbl.setMinimumHeight(150)
        self._course_tbl.itemSelectionChanged.connect(self._on_course_picked)
        self._course_tbl.cellDoubleClicked.connect(self._on_course_double_click)
        cl.addWidget(self._course_tbl)
        v.addWidget(course_box)

        # 章节选择
        ch_box = QGroupBox("章节选择（可选）")
        chl = QVBoxLayout(ch_box)
        ch_row = QHBoxLayout()
        for text, slot in [("全选", self._select_all_chapters), ("取消全选", self._select_no_chapters)]:
            b = QPushButton(text)
            b.setObjectName("detail_btn")
            b.setMinimumWidth(64)
            b.clicked.connect(slot)
            ch_row.addWidget(b)
        ch_row.addStretch()
        self._ch_hint = QLabel("不勾选则处理全部章节")
        self._ch_hint.setStyleSheet("color:#94a3b8;font-size:11px;")
        ch_row.addWidget(self._ch_hint)
        chl.addLayout(ch_row)

        self._chapter_list = QListWidget()
        self._chapter_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._chapter_list.setMinimumHeight(90)
        chl.addWidget(self._chapter_list)
        v.addWidget(ch_box)

        self._add_queue_btn = QPushButton("加入任务队列")
        self._add_queue_btn.setObjectName("detail_btn")
        self._add_queue_btn.setMinimumHeight(32)
        self._add_queue_btn.setEnabled(False)
        self._add_queue_btn.clicked.connect(self._on_add_queue)
        v.addWidget(self._add_queue_btn)

        # 高级设置
        sg = QGroupBox("高级设置")
        sgl = QVBoxLayout(sg)
        sgl.setSpacing(6)

        row_s = QHBoxLayout()
        row_s.addWidget(QLabel("答题方式:"))
        self._strategy_cb = QComboBox()
        # 题库答题与 AI 答题是两条独立功能路径，选择即路径选择
        self._strategy_cb.addItems([
            "题库答题",              # tiku: 仅查询题库，选择题首选兜底，不调用 AI
            "AI 智能答题",           # ai: 仅大模型（主观题模板化作答），不查询题库
            "题库优先 + AI 兜底",    # ai + tiku: 题库未命中时由 AI 补充
            "首选答案",              # first: 本地策略
            "随机选择",              # random: 本地策略
        ])
        self._strategy_cb.setToolTip(
            "题库答题：查询在线题库获取标准答案，速度快、命中率高，不消耗 AI 额度\n"
            "AI 智能答题：由大模型生成答案，支持主观题模板化作答，不查询题库\n"
            "题库优先 + AI 兜底：题库命中直接采用，未命中交给 AI 补充\n"
            "首选答案 / 随机选择：不查询题库、不调用 AI 的本地作答策略"
        )
        row_s.addWidget(self._strategy_cb)
        sgl.addLayout(row_s)

        # 双路径状态提示：随答题方式动态显示题库/AI 就绪情况
        self._mode_hint = QLabel("")
        self._mode_hint.setWordWrap(True)
        self._mode_hint.setStyleSheet("color:#94a3b8;font-size:11px;")
        sgl.addWidget(self._mode_hint)
        self._strategy_cb.currentTextChanged.connect(lambda _: self._refresh_mode_hint())

        row_t = QHBoxLayout()
        self._thread_chk = QCheckBox("多线程")
        self._thread_chk.toggled.connect(lambda v: self._thread_spin.setEnabled(v))
        row_t.addWidget(self._thread_chk)
        row_t.addWidget(QLabel("线程数:"))
        self._thread_spin = QSpinBox()
        self._thread_spin.setRange(1, 15)
        self._thread_spin.setValue(5)
        self._thread_spin.setEnabled(False)
        row_t.addWidget(self._thread_spin)
        row_t.addStretch()
        sgl.addLayout(row_t)
        self._refresh_mode_hint()
        v.addWidget(sg)

        # ── 快捷操作 ──
        qa = QGroupBox("快捷操作")
        qal = QVBoxLayout(qa)
        qal.setSpacing(4)

        btn_row1 = QHBoxLayout()
        for label, tip, slot in [
            ("视频", "仅处理视频/音频任务", self._on_action_video),
            ("测验", "仅作答章节测验", self._on_action_quiz),
            ("作业", "仅提交课后作业", self._on_action_work),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setObjectName("quick_btn")
            b.setMinimumHeight(28)
            b.clicked.connect(slot)
            btn_row1.addWidget(b)

        btn_row2 = QHBoxLayout()
        for label, tip, slot in [
            ("阅读", "仅处理阅读任务", self._on_action_read),
            ("文档", "仅浏览文档/PPT", self._on_action_doc),
            ("电子书", "仅处理电子书任务", self._on_action_book),
            ("讨论", "仅处理课程讨论/话题", self._on_action_bbs),
            ("直播", "仅处理直播回放任务", self._on_action_live),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setObjectName("quick_btn")
            b.setMinimumHeight(28)
            b.clicked.connect(slot)
            btn_row2.addWidget(b)

        btn_row3 = QHBoxLayout()
        btn_row3.addWidget(QLabel("签到:"))
        for label, tip, slot in [
            ("普通", "扫描并执行普通签到", lambda: self._on_signin_by_type(0)),
            ("手势", "扫描并执行手势签到", lambda: self._on_signin_by_type(3)),
            ("位置", "扫描并执行位置签到", lambda: self._on_signin_by_type(4)),
            ("二维码", "扫描并执行二维码签到", lambda: self._on_signin_by_type(1)),
            ("拍照", "扫描并执行拍照签到", lambda: self._on_signin_by_type(2)),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setObjectName("quick_btn")
            b.setMinimumHeight(28)
            b.clicked.connect(slot)
            btn_row3.addWidget(b)
        btn_row3.addStretch()

        qal.addLayout(btn_row1)
        qal.addLayout(btn_row2)
        qal.addLayout(btn_row3)
        v.addWidget(qa)

        return w

    def _build_middle_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        q_box = QGroupBox("任务队列")
        ql = QVBoxLayout(q_box)
        self._queue_list = QListWidget()
        self._queue_list.setMinimumHeight(180)
        ql.addWidget(self._queue_list)

        rm_btn = QPushButton("移除选中项")
        rm_btn.setObjectName("detail_btn")
        rm_btn.setMinimumHeight(30)
        rm_btn.clicked.connect(self._on_remove_queue)
        ql.addWidget(rm_btn)
        v.addWidget(q_box)

        ctrl = QGroupBox("运行控制")
        cl = QVBoxLayout(ctrl)
        cl.setSpacing(6)
        self._start_btn = QPushButton("开始执行")
        self._start_btn.setObjectName("start_btn")
        self._start_btn.setEnabled(False)
        self._start_btn.setMinimumHeight(34)
        self._start_btn.clicked.connect(self._on_start_queue)
        cl.addWidget(self._start_btn)

        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setObjectName("pause_btn")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setMinimumHeight(34)
        self._pause_btn.clicked.connect(self._on_pause)
        cl.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setObjectName("stop_btn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setMinimumHeight(34)
        self._stop_btn.clicked.connect(self._on_stop)
        cl.addWidget(self._stop_btn)
        v.addWidget(ctrl)
        return w

    def _build_right_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        pg = QGroupBox("任务进度")
        pl = QVBoxLayout(pg)
        pl.setSpacing(8)

        self._queue_pbar_label = QLabel("队列: 0 / 0")
        pl.addWidget(self._queue_pbar_label)
        self._queue_pbar = QProgressBar()
        self._queue_pbar.setMaximumHeight(14)
        pl.addWidget(self._queue_pbar)

        pl.addSpacing(4)
        self._cur_course_label = QLabel("当前: --")
        pl.addWidget(self._cur_course_label)
        self._cur_status_label = QLabel("状态: 空闲")
        self._cur_status_label.setStyleSheet("color:#64748b;font-size:12px;")
        pl.addWidget(self._cur_status_label)
        self._cur_pbar = QProgressBar()
        self._cur_pbar.setMaximumHeight(14)
        pl.addWidget(self._cur_pbar)
        v.addWidget(pg)

        lg = QGroupBox("运行日志")
        ll = QVBoxLayout(lg)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(130)
        ll.addWidget(self._log_view)
        v.addWidget(lg)
        return w

    # ═══════════════════════════════════════════════════════════════
    #  页面 1 — 签到管理
    # ═══════════════════════════════════════════════════════════════

    def _build_signin_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # ── 免责声明 ──
        disclaimer = QLabel(
            "⚡ 免责声明：签到功能当前处于测试阶段，可能存在签到数据不准确的情况。"
            "请在正式使用前自行确认签到结果，本工具不对签到失败或数据异常导致的任何后果承担责任。"
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "background:rgba(234,179,8,0.12);color:#fbbf24;"
            "border:1px solid rgba(234,179,8,0.25);border-radius:6px;"
            "padding:8px 12px;font-size:12px;margin-bottom:4px;"
        )
        v.addWidget(disclaimer)

        # ── 工具栏第 1 行: 扫描 / 一键签到 / 位置 ──
        tb = QHBoxLayout()
        self._si_scan_btn = QPushButton("扫描全部课程")
        self._si_scan_btn.setObjectName("start_btn")
        self._si_scan_btn.setMinimumHeight(32)
        self._si_scan_btn.clicked.connect(self._on_signin_scan)
        tb.addWidget(self._si_scan_btn)

        self._si_auto_btn = QPushButton("一键全部签到")
        self._si_auto_btn.setObjectName("detail_btn")
        self._si_auto_btn.setMinimumHeight(32)
        self._si_auto_btn.setEnabled(False)
        self._si_auto_btn.clicked.connect(self._on_signin_auto)
        tb.addWidget(self._si_auto_btn)

        tb.addWidget(QLabel("  纬度:"))
        self._si_lat = QLineEdit("")
        self._si_lat.setPlaceholderText("如: 30.5")
        self._si_lat.setMaximumWidth(90)
        tb.addWidget(self._si_lat)

        tb.addWidget(QLabel("经度:"))
        self._si_lon = QLineEdit("")
        self._si_lon.setPlaceholderText("如: 114.0")
        self._si_lon.setMaximumWidth(90)
        tb.addWidget(self._si_lon)

        tb.addStretch()
        v.addLayout(tb)

        # ── 工具栏第 2 行: 按签到类型操作 ──
        tb2 = QHBoxLayout()
        tb2.addWidget(QLabel("按类型签到:"))
        SIGN_TYPES = [
            (0, "普通", "扫描并执行普通签到"),
            (3, "手势", "扫描并执行手势签到"),
            (4, "位置", "扫描并执行位置签到"),
            (1, "二维码", "扫描并执行二维码签到"),
            (2, "拍照", "扫描并执行拍照签到"),
        ]
        for stype, slabel, stip in SIGN_TYPES:
            b = QPushButton(slabel)
            b.setToolTip(stip)
            b.setObjectName("quick_btn")
            b.setMinimumHeight(28)
            b.clicked.connect(lambda checked, t=stype: self._on_signin_by_type(t))
            tb2.addWidget(b)
        tb2.addStretch()
        v.addLayout(tb2)

        # 活动列表
        self._si_table = QTableWidget()
        self._si_table.setColumnCount(6)
        self._si_table.setHorizontalHeaderLabels(["课程", "活动名称", "签到类型", "状态", "", ""])
        self._si_table.horizontalHeader().setStretchLastSection(True)
        self._si_table.setColumnWidth(0, 150)
        self._si_table.setColumnWidth(1, 180)
        self._si_table.setColumnWidth(2, 80)
        self._si_table.setColumnWidth(3, 80)
        self._si_table.setColumnWidth(4, 80)
        self._si_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._si_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._si_table.setMinimumHeight(200)
        v.addWidget(self._si_table)

        # 签到日志
        self._si_log = QTextEdit()
        self._si_log.setReadOnly(True)
        self._si_log.setMaximumHeight(150)
        v.addWidget(self._si_log)

        return w

    # ═══════════════════════════════════════════════════════════════
    #  页面 2 — 资源下载
    # ═══════════════════════════════════════════════════════════════

    def _build_download_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 课程选择 + 资源类型过滤
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("选择课程:"))
        self._dl_course_cb = QComboBox()
        self._dl_course_cb.setMinimumWidth(250)
        sel_row.addWidget(self._dl_course_cb)

        sel_row.addWidget(QLabel("  资源类型:"))
        self._dl_type_filter = QComboBox()
        self._dl_type_filter.addItems(["全部资源", "媒体文件 (视频/音频)", "课件资料 (PPT/PDF/DOC等)"])
        self._dl_type_filter.currentIndexChanged.connect(self._on_dl_type_filter_changed)
        sel_row.addWidget(self._dl_type_filter)

        self._dl_scan_btn = QPushButton("扫描资源")
        self._dl_scan_btn.setObjectName("start_btn")
        self._dl_scan_btn.setMinimumHeight(30)
        self._dl_scan_btn.clicked.connect(self._on_dl_scan)
        sel_row.addWidget(self._dl_scan_btn)

        sel_row.addStretch()
        v.addLayout(sel_row)

        # 文件列表
        self._dl_table = QTableWidget()
        self._dl_table.setColumnCount(5)
        self._dl_table.setHorizontalHeaderLabels(["", "文件名", "资源类型", "类别", "所属章节"])
        self._dl_table.horizontalHeader().setStretchLastSection(True)
        self._dl_table.setColumnWidth(0, 36)
        self._dl_table.setColumnWidth(1, 260)
        self._dl_table.setColumnWidth(2, 80)
        self._dl_table.setColumnWidth(3, 70)
        self._dl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._dl_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._dl_table.setMinimumHeight(200)
        v.addWidget(self._dl_table)

        # 下载控制
        dl_ctrl = QHBoxLayout()
        dl_ctrl.addWidget(QLabel("保存到:"))
        self._dl_dir_le = QLineEdit(os.path.join(os.path.expanduser("~"), "Downloads", "StarCourse"))
        self._dl_dir_le.setMinimumWidth(300)
        dl_ctrl.addWidget(self._dl_dir_le)

        browse_btn = QPushButton("浏览...")
        browse_btn.setObjectName("detail_btn")
        browse_btn.clicked.connect(self._on_dl_browse_dir)
        dl_ctrl.addWidget(browse_btn)

        self._dl_download_btn = QPushButton("下载选中文件")
        self._dl_download_btn.setObjectName("start_btn")
        self._dl_download_btn.setMinimumHeight(32)
        self._dl_download_btn.clicked.connect(self._on_dl_download)
        dl_ctrl.addWidget(self._dl_download_btn)

        select_all_btn = QPushButton("全选")
        select_all_btn.setObjectName("detail_btn")
        select_all_btn.clicked.connect(self._on_dl_select_all)
        dl_ctrl.addWidget(select_all_btn)

        deselect_btn = QPushButton("取消全选")
        deselect_btn.setObjectName("detail_btn")
        deselect_btn.clicked.connect(self._on_dl_deselect_all)
        dl_ctrl.addWidget(deselect_btn)

        dl_ctrl.addStretch()
        v.addLayout(dl_ctrl)

        # 下载日志
        self._dl_log = QTextEdit()
        self._dl_log.setReadOnly(True)
        self._dl_log.setMaximumHeight(120)
        v.addWidget(self._dl_log)

        return w

    # ═══════════════════════════════════════════════════════════════
    #  登录
    # ═══════════════════════════════════════════════════════════════

    def _auto_login(self):
        # 优先尝试 cookie 免密登录
        if self._profile.cookie:
            self._append_log("检测到已保存的登录会话，尝试免密登录...")
            self._set_status("Cookie 登录中")
            self._cookie_login_worker = _CookieLoginWorker(self._profile.cookie)
            self._cookie_login_worker.finished.connect(self._on_cookie_login_done)
            self._cookie_login_worker.start()
            return

        # 回退到密码登录
        if self._profile.username and self._profile.password:
            self._append_log("正在自动登录...")
            self._set_status("登录中")
            self._kick_login()

    def _on_login(self):
        if not self._profile.username or not self._profile.password:
            QMessageBox.warning(self, "提示", "该账号没有设置登录凭据。")
            return
        self._append_log("正在登录...")
        self._set_status("登录中")
        self._login_btn.setEnabled(False)
        self._kick_login()

    def _kick_login(self):
        self._login_worker = _LoginWorker(self._profile.username, self._profile.password)
        self._login_worker.finished.connect(self._on_login_done)
        self._login_worker.start()

    def _on_login_done(self, ok: bool, msg: str):
        if ok:
            self._client = self._login_worker.client
            self._append_log(msg)
            self._set_status("已登录")
            self._persist_cookie()
            self._after_login_success()
        else:
            self._append_log(f"[错误] {msg}")
            self._set_status("登录失败")
            self._login_btn.setEnabled(True)

    def _on_cookie_login_done(self, ok: bool, msg: str):
        """Cookie 免密登录回调"""
        if ok:
            self._client = self._cookie_login_worker.client
            self._append_log(f"[Cookie] {msg}")
            self._set_status("已登录 (免密)")
            self._persist_cookie()
            self._after_login_success()
        else:
            self._append_log(f"[Cookie] {msg}")
            self._append_log("Cookie 已过期，尝试使用密码重新登录...")
            if self._profile.cookie:
                # 清除过期 cookie 并回退到密码登录
                self._profile.cookie = ""
                if self._store:
                    self._store.remove_cookie(self._profile.username)
            self._kick_login()

    def _persist_cookie(self):
        """将登录成功的 cookie 持久化到 AccountStore"""
        if self._client is None or self._store is None:
            return
        cookie = self._client.cookie
        if cookie:
            self._profile.cookie = cookie
            self._store.update_cookie(self._profile.username, cookie)
            self._append_log("登录会话已保存，下次可免密登录")

    def _after_login_success(self):
        """登录成功后的通用初始化流程"""
        self._login_btn.setEnabled(False)
        self._refresh_btn.setEnabled(True)
        self._dl_scan_btn.setEnabled(True)
        self._si_scan_btn.setEnabled(True)
        self._on_refresh_courses()

    # ═══════════════════════════════════════════════════════════════
    #  课程
    # ═══════════════════════════════════════════════════════════════

    def _on_refresh_courses(self):
        if not self._client:
            QMessageBox.warning(self, "提示", "请先登录。")
            return
        self._append_log("正在获取课程列表...")
        self._course_worker = _CourseWorker(self._client)
        self._course_worker.finished.connect(self._on_courses_done)
        self._course_worker.start()

    def _on_courses_done(self, ok: bool, courses: list, msg: str):
        if not ok:
            self._append_log(f"[错误] {msg}")
            return
        self._courses = courses
        self._course_tbl.setRowCount(0)
        self._dl_course_cb.clear()
        for i, c in enumerate(courses):
            self._course_tbl.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self._course_tbl.setItem(i, 0, chk)
            self._course_tbl.setItem(i, 1, QTableWidgetItem(c.name))
            self._course_tbl.setItem(i, 2, QTableWidgetItem(c.course_id))
            status_text = "进行中" if c.if_open else "已关闭"
            status = QTableWidgetItem(status_text)
            status.setForeground(Qt.GlobalColor.darkGreen if c.if_open else Qt.GlobalColor.gray)
            self._course_tbl.setItem(i, 3, status)
            tag = "进行中" if c.if_open else "已关闭"
            self._dl_course_cb.addItem(f"[{tag}] {c.name}", c)
        self._append_log(f"[完成] 已加载 {len(courses)} 门课程")
        self._add_queue_btn.setEnabled(False)

    def _on_course_picked(self):
        row = self._course_tbl.currentRow()
        if row < 0:
            return
        c = self._courses[row]
        if not c.if_open:
            self._add_queue_btn.setEnabled(False)
            return
        self._current_course = c
        self._add_queue_btn.setEnabled(True)
        self._append_log(f"已选择: {c.name}")
        self._append_log("正在获取章节列表...")
        self._chapter_worker = _ChapterWorker(self._client, c.course_id, c.class_id, c.cpi)
        self._chapter_worker.finished.connect(self._on_chapters_done)
        self._chapter_worker.start()

    def _on_chapters_done(self, ok: bool, chapters: list, msg: str):
        if not ok:
            self._append_log(f"[错误] {msg}")
            return
        self._chapters = chapters
        self._chapter_list.clear()
        for i, ch in enumerate(chapters):
            label = ch.get("name", f"章节 {i + 1}")
            jobs = ch.get("job_count", 0)
            item = QListWidgetItem(f"[{jobs} 任务] {label}" if jobs else label)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, ch.get("chapter_id", ""))
            self._chapter_list.addItem(item)
        self._append_log(f"[完成] {len(chapters)} 个章节")

    def _on_course_double_click(self, row: int, _col: int):
        if row < 0 or row >= len(self._courses):
            return
        c = self._courses[row]
        if not c.if_open:
            QMessageBox.warning(self, "提示", "该课程已关闭，无法处理。")
            return
        self._course_tbl.selectRow(row)
        self._current_course = c
        self._add_queue_btn.setEnabled(True)
        self._enqueue_course(row, chapter_ids=None)
        if not self._queue_active:
            self._on_start_queue()

    # ═══════════════════════════════════════════════════════════════
    #  章节辅助
    # ═══════════════════════════════════════════════════════════════

    def _select_all_chapters(self):
        for i in range(self._chapter_list.count()):
            self._chapter_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _select_no_chapters(self):
        for i in range(self._chapter_list.count()):
            self._chapter_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _selected_chapter_ids(self) -> list[str]:
        result = []
        for i in range(self._chapter_list.count()):
            item = self._chapter_list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                cid = item.data(Qt.ItemDataRole.UserRole)
                if cid:
                    result.append(cid)
        return result

    # ═══════════════════════════════════════════════════════════════
    #  任务队列
    # ═══════════════════════════════════════════════════════════════

    def _on_add_queue(self):
        checked = []
        for r in range(self._course_tbl.rowCount()):
            item = self._course_tbl.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                checked.append(r)
        if not checked:
            r = self._course_tbl.currentRow()
            if r < 0:
                QMessageBox.warning(self, "提示", "请先选择一门课程。")
                return
            checked = [r]

        added = 0
        for r in checked:
            if self._enqueue_course(r):
                added += 1

        for r in range(self._course_tbl.rowCount()):
            item = self._course_tbl.item(r, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

        if added:
            self._append_log(f"[完成] 已添加 {added} 门课程到队列")
            self._refresh_queue_display()
            self._start_btn.setEnabled(True)

    def _enqueue_course(self, row: int, chapter_ids=None) -> bool:
        if row < 0 or row >= len(self._courses):
            return False
        c = self._courses[row]
        if not c.if_open:
            return False
        for task in self._queue:
            if task["course"].course_id == c.course_id:
                return False

        if chapter_ids is None:
            if row == self._course_tbl.currentRow():
                picked = self._selected_chapter_ids()
                chapter_ids = picked if picked else None
            else:
                chapter_ids = None

        entry = {"course": c, "chapter_ids": chapter_ids}
        self._queue.append(entry)

        tag = f"({len(chapter_ids)} 章)" if chapter_ids else "(全部)"
        item = QListWidgetItem(f"{len(self._queue)}. {c.name} {tag}")
        item.setData(Qt.ItemDataRole.UserRole, len(self._queue) - 1)
        self._queue_list.addItem(item)
        return True

    def _on_remove_queue(self):
        row = self._queue_list.currentRow()
        if row < 0:
            return
        if self._queue_active and row == self._queue_index:
            QMessageBox.warning(self, "提示", "当前正在执行的任务无法移除。")
            return
        self._queue.pop(row)
        self._queue_list.takeItem(row)
        for i in range(self._queue_list.count()):
            item = self._queue_list.item(i)
            t = self._queue[i]
            tag = f"({len(t['chapter_ids'])} 章)" if t["chapter_ids"] else "(全部)"
            item.setText(f"{i + 1}. {t['course'].name} {tag}")
            item.setData(Qt.ItemDataRole.UserRole, i)
        if not self._queue:
            self._start_btn.setEnabled(False)
        self._refresh_queue_display()

    # ═══════════════════════════════════════════════════════════════
    #  队列执行
    # ═══════════════════════════════════════════════════════════════

    def _on_start_queue(self):
        if not self._queue:
            QMessageBox.warning(self, "提示", "任务队列为空，请先添加课程。")
            return
        self._queue_active = True
        self._queue_paused = False
        self._queue_index = 0
        self._append_log("=" * 40)
        self._append_log(f"开始执行队列 — 共 {len(self._queue)} 门课程")
        self._set_status("运行中")
        self._store.update_status(self._profile.username, "running")
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._add_queue_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._exec_next()

    def _exec_next(self):
        if not self._queue_active or self._queue_paused:
            return
        if self._queue_index >= len(self._queue):
            self._on_queue_done()
            return

        entry = self._queue[self._queue_index]
        self._refresh_queue_display()
        c = entry["course"]
        ch_ids = entry.get("chapter_ids")

        self._cur_course_label.setText(f"当前: {c.name}")
        self._cur_status_label.setText("状态: 运行中")
        self._cur_pbar.setRange(0, 0)
        self._append_log(f"任务 {self._queue_index + 1}/{len(self._queue)}: {c.name}")

        strategy = self._current_strategy()
        use_tiku = self._use_tiku()
        multi = self._thread_chk.isChecked()
        threads = self._thread_spin.value() if multi else 1

        self._worker = _AutomationWorker(
            self._client, c.course_id, c.class_id,
            strategy, c.cpi, ch_ids, multi, threads, max_retries=2,
            use_tiku=use_tiku,
        )
        self._worker.log_signal.connect(self._append_log)
        self._worker.finished.connect(self._on_task_done)
        self._worker.start()

    # ── 答题方式（题库 / AI 双路径独立开关）──────────────
    def _current_strategy(self) -> str:
        """UI 答题方式 -> engine 策略值。"""
        mode = self._strategy_cb.currentText()
        return {
            "题库答题": "tiku",
            "AI 智能答题": "ai",
            "题库优先 + AI 兜底": "ai",
            "首选答案": "first",
            "随机选择": "random",
        }.get(mode, "first")

    def _use_tiku(self) -> bool:
        """当前答题方式是否启用题库路径（题库/AI 两路径独立维护）。"""
        return self._strategy_cb.currentText() in ("题库答题", "题库优先 + AI 兜底")

    def _refresh_mode_hint(self):
        """随答题方式刷新路径说明与题库/AI 就绪状态。"""
        mode = self._strategy_cb.currentText()
        desc = {
            "题库答题": "题库答题：仅查询在线题库（选择题以首选/随机兜底），不调用 AI，速度最快。",
            "AI 智能答题": "AI 答题：由大模型生成全部答案（主观题按答题模板作答），不查询题库。",
            "题库优先 + AI 兜底": "题库优先 + AI 兜底：题库命中直接采用，未命中时由 AI 补充作答。",
            "首选答案": "首选答案：本地默认策略，不查询题库、不调用 AI。",
            "随机选择": "随机选择：本地随机策略，不查询题库、不调用 AI。",
        }.get(mode, "")
        tips = []
        client = getattr(self, "_client", None)
        status = {}
        if client is not None:
            try:
                status = client.get_answer_mode_status()
            except Exception:
                status = {}
        if mode in ("题库答题", "题库优先 + AI 兜底"):
            if status.get("tiku_ready"):
                tips.append(f"题库: 已就绪 [{status.get('tiku_provider', '')}]")
            else:
                tips.append("题库: 未配置（engine/config.yml 的 TikuConfig）")
        if mode in ("AI 智能答题", "题库优先 + AI 兜底"):
            tips.append("AI: 已配置" if status.get("ai_ready") else "AI: 未配置密钥（config.json）")
        self._mode_hint.setText(desc if not tips else f"{desc}  [ {'  ·  '.join(tips)} ]")

    def _on_task_done(self, ok: bool, stats: dict, msg: str):
        if ok:
            self._append_log("[完成] 任务执行成功")
            for key, cn in [("chapters", "章节"), ("videos", "视频"), ("quizzes", "测验"), ("completed", "已完成"), ("failed", "失败")]:
                if key in stats:
                    self._append_log(f"  {cn}: {stats.get(key, 0)}")
        else:
            self._append_log(f"[失败] 任务执行失败: {msg}")

        self._cur_pbar.setRange(0, 100)
        self._cur_pbar.setValue(100 if ok else 0)
        self._cur_status_label.setText("状态: " + ("已完成" if ok else "失败"))

        self._queue_index += 1
        self._refresh_queue_display()

        if self._queue_active and not self._queue_paused:
            if self._queue_index >= len(self._queue):
                self._on_queue_done()
            else:
                self._exec_next()
        elif self._queue_index >= len(self._queue):
            self._on_queue_done()

    def _on_queue_done(self):
        self._queue_active = False
        self._queue_paused = False
        self._queue_index = 0
        self._refresh_queue_display()
        self._append_log("=" * 40)
        self._append_log("队列执行完毕！")
        self._set_status("空闲")
        self._store.update_status(self._profile.username, "idle")
        self._reset_controls()

    def _on_pause(self):
        if self._worker:
            self._worker.stop()
        self._queue_paused = True
        self._pause_btn.setText("继续")
        try:
            self._pause_btn.clicked.disconnect()
        except TypeError:
            pass
        self._pause_btn.clicked.connect(self._on_resume)
        self._cur_status_label.setText("状态: 已暂停")

    def _on_resume(self):
        self._queue_paused = False
        self._pause_btn.setText("暂停")
        try:
            self._pause_btn.clicked.disconnect()
        except TypeError:
            pass
        self._pause_btn.clicked.connect(self._on_pause)
        self._cur_status_label.setText("状态: 运行中")
        if not self._worker or not self._worker.isRunning():
            self._exec_next()

    def _on_stop(self):
        self._queue_active = False
        self._queue_paused = False
        if self._worker:
            self._worker.stop()
        self._cur_status_label.setText("状态: 已停止")

    def _reset_controls(self):
        self._start_btn.setEnabled(bool(self._queue))
        self._pause_btn.setText("暂停")
        try:
            self._pause_btn.clicked.disconnect()
        except TypeError:
            pass
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._add_queue_btn.setEnabled(bool(self._current_course))
        self._refresh_btn.setEnabled(True)

    # ═══════════════════════════════════════════════════════════════
    #  快捷操作
    # ═══════════════════════════════════════════════════════════════

    def _get_current_course_info(self):
        """验证登录状态和课程选择"""
        if not self._client:
            QMessageBox.warning(self, "提示", "请先登录。")
            return None
        row = self._course_tbl.currentRow()
        if row < 0 or row >= len(self._courses):
            QMessageBox.warning(self, "提示", "请先在左侧课程列表中选择一门课程。")
            return None
        c = self._courses[row]
        if not c.if_open:
            QMessageBox.warning(self, "提示", "该课程已关闭，无法处理。")
            return None
        return c

    def _run_single_task_worker(self, task_type: str):
        """快捷操作通用执行器"""
        c = self._get_current_course_info()
        if not c:
            return

        self._switch_mode("auto")
        task_names = {"video": "视频", "quiz": "测验", "work": "作业",
                      "read": "阅读", "document": "文档", "book": "电子书",
                      "bbs": "讨论", "live": "直播"}
        cn_name = task_names.get(task_type, task_type)
        self._append_log(f"[快捷] 开始处理{cn_name}任务: {c.name}")

        class _SingleTaskWorker(QThread):
            log_signal = pyqtSignal(str)
            finished = pyqtSignal(bool, str)

            def __init__(self, client, course_id, class_id, cpi, task_type,
                         strategy="first", use_tiku=False):
                super().__init__()
                self._client = client
                self._cid = course_id
                self._clid = class_id
                self._cpi = cpi
                self._type = task_type
                self._strategy = strategy
                self._use_tiku = use_tiku
                self._running = True

            def stop(self):
                self._running = False

            def run(self):
                try:
                    self.log_signal.emit("正在获取章节列表...")
                    ok, chapters, msg = self._client.fetch_chapters(self._cid, self._clid, self._cpi)
                    if not ok:
                        self.finished.emit(False, msg)
                        return

                    total = 0
                    done = 0
                    for ch in chapters:
                        kid = ch.get("chapter_id")
                        if not kid or not self._running:
                            continue
                        self.log_signal.emit(f"章节: {ch.get('name', kid)}")
                        ok2, tasks, _ = self._client.fetch_tasks(self._cid, self._clid, kid, self._cpi)
                        if not ok2:
                            continue
                        for t in tasks:
                            if not self._running:
                                break
                            tt = t.get("task_type", "")
                            if tt != self._type:
                                continue
                            total += 1
                            handler_map = {
                                "video":    self._client.run_video_task,
                                "quiz":     lambda task=t: self._client.run_quiz_task(task, self._cid, self._strategy, use_tiku=self._use_tiku),
                                "work":     lambda task=t: self._client.run_work_task(task, self._cid, self._strategy, use_tiku=self._use_tiku),
                                "read":     lambda task=t: self._client.run_read_task(task, self._cid),
                                "document": lambda task=t: self._client.run_document_task(task, self._cid),
                                "book":     lambda task=t: self._client.run_book_task(task, self._cid),
                                "bbs":      lambda task=t: self._client.run_bbs_task(task, self._cid, self._clid, "random"),
                                "live":     lambda task=t: self._client.run_live_task(task, self._cid),
                            }
                            handler = handler_map.get(self._type)
                            if handler:
                                props = t.get("attachment", {}).get("property", {})
                                name = props.get("name", props.get("title", "?"))
                                ok3 = handler(t)
                                done += int(ok3)
                                self.log_signal.emit(f"  {'[完成]' if ok3 else '[失败]'} {name}")

                    self.log_signal.emit(f"处理完毕: 完成 {done}/{total} 个任务")
                    cn = task_names.get(self._type, self._type)
                    self.finished.emit(True, f"已处理 {done}/{total} 个{cn}任务")
                except Exception as exc:
                    self.finished.emit(False, f"执行出错: {exc}")

        self._worker = _SingleTaskWorker(
            self._client, c.course_id, c.class_id, c.cpi, task_type,
            strategy=self._current_strategy(), use_tiku=self._use_tiku(),
        )
        self._worker.log_signal.connect(self._append_log)
        self._worker.finished.connect(self._on_quick_task_done)
        self._worker.start()

        self._cur_pbar.setRange(0, 0)
        self._cur_status_label.setText(f"状态: 处理{cn_name}任务")
        self._set_status(f"处理{cn_name}任务")

    def _on_quick_task_done(self, ok: bool, msg: str):
        self._cur_pbar.setRange(0, 100)
        self._cur_pbar.setValue(100 if ok else 0)
        self._cur_status_label.setText("状态: 空闲")
        self._set_status("空闲")
        self._append_log(f"[快捷] {msg}")

    def _on_action_video(self):
        self._run_single_task_worker("video")

    def _on_action_quiz(self):
        self._run_single_task_worker("quiz")

    def _on_action_work(self):
        self._run_single_task_worker("work")

    def _on_action_read(self):
        self._run_single_task_worker("read")

    def _on_action_doc(self):
        self._run_single_task_worker("document")

    def _on_action_book(self):
        self._run_single_task_worker("book")

    def _on_action_bbs(self):
        self._run_single_task_worker("bbs")

    def _on_action_live(self):
        self._run_single_task_worker("live")

    def _on_action_signin_quick(self):
        self._switch_mode("signin")
        self._on_signin_scan()

    # ═══════════════════════════════════════════════════════════════
    #  签到逻辑
    # ═══════════════════════════════════════════════════════════════

    def _on_signin_scan(self):
        if not self._client:
            QMessageBox.warning(self, "提示", "请先登录。")
            return
        self._si_scan_btn.setEnabled(False)
        self._si_log.append("正在扫描活动签到活动...")
        self._signin_scan_worker = _SignInScanWorker(self._client)
        self._signin_scan_worker.finished.connect(self._on_signin_scan_done)
        self._signin_scan_worker.start()

    def _on_signin_scan_done(self, ok: bool, activities: list, msg: str):
        self._si_scan_btn.setEnabled(True)
        self._si_log.append(f"[{'完成' if ok else '失败'}] {msg}")
        self._signin_activities = activities

        self._si_table.setRowCount(0)
        for i, act in enumerate(activities):
            self._si_table.insertRow(i)
            self._si_table.setItem(i, 0, QTableWidgetItem(act.get("course_name", "")))
            self._si_table.setItem(i, 1, QTableWidgetItem(act.get("activity_name", "")))
            self._si_table.setItem(i, 2, QTableWidgetItem(act.get("sign_type", "普通")))
            self._si_table.setItem(i, 3, QTableWidgetItem("待签到"))

            sign_btn = QPushButton("签到")
            sign_btn.setObjectName("start_btn")
            sign_btn.setMinimumHeight(26)
            sign_btn.clicked.connect(lambda checked, idx=i: self._on_signin_exec(idx))
            self._si_table.setCellWidget(i, 4, sign_btn)

        self._si_auto_btn.setEnabled(len(activities) > 0)

    def _on_signin_auto(self):
        if not self._signin_activities:
            QMessageBox.information(self, "提示", "没有待签到的活动。")
            return
        self._si_auto_btn.setEnabled(False)
        self._si_log.append("开始一键全部签到...")

        class _AutoSignWorker(QThread):
            log_signal = pyqtSignal(str)
            finished = pyqtSignal(bool, str)

            def __init__(self, client):
                super().__init__()
                self._client = client

            def run(self):
                ok, results, msg = self._client.auto_sign_all(on_progress=self.log_signal.emit)
                self.finished.emit(ok, msg)

        self._signin_exec_worker = _AutoSignWorker(self._client)
        self._signin_exec_worker.log_signal.connect(lambda m: self._si_log.append(m))
        self._signin_exec_worker.finished.connect(self._on_signin_auto_done)
        self._signin_exec_worker.start()

    def _on_signin_auto_done(self, ok: bool, msg: str):
        self._si_auto_btn.setEnabled(True)
        self._si_log.append(f"[{'完成' if ok else '失败'}] {msg}")
        self._on_signin_scan()

    def _on_signin_exec(self, idx: int):
        if idx < 0 or idx >= len(self._signin_activities):
            return
        act = self._signin_activities[idx]
        lat = None
        lon = None
        try:
            lat = float(self._si_lat.text()) if self._si_lat.text().strip() else None
            lon = float(self._si_lon.text()) if self._si_lon.text().strip() else None
        except ValueError:
            pass
        location = (lat, lon) if lat is not None and lon is not None else None

        self._si_log.append(f"签到: {act.get('activity_name', '')} ({act.get('sign_type', '')})")

        self._signin_exec_worker = _SignInExecWorker(
            self._client, act.get("course_id", ""), act.get("class_id", ""),
            act.get("raw", act), location, ""
        )
        self._signin_exec_worker.log_signal.connect(lambda m: self._si_log.append(m))
        self._signin_exec_worker.finished.connect(lambda ok2, msg2: self._on_signin_exec_done(ok2, msg2, idx))
        self._signin_exec_worker.start()

    def _on_signin_exec_done(self, ok: bool, msg: str, idx: int):
        self._si_table.setItem(idx, 3, QTableWidgetItem("成功" if ok else "失败"))
        self._si_log.append(f"[{'完成' if ok else '失败'}] {msg}")

    def _on_signin_by_type(self, sign_type: int):
        """按指定签到类型扫描并全部签到"""
        if not self._client:
            QMessageBox.warning(self, "提示", "请先登录。")
            return

        type_labels = {0: "普通签到", 1: "二维码签到", 2: "拍照签到",
                       3: "手势签到", 4: "位置签到"}
        type_name = type_labels.get(sign_type, f"类型{sign_type}")

        # 获取经纬度
        lat_str = self._si_lat.text().strip()
        lon_str = self._si_lon.text().strip()
        location = None
        if lat_str and lon_str:
            try:
                location = (float(lat_str), float(lon_str))
            except ValueError:
                pass

        self._switch_mode("signin")
        self._si_log.clear()
        self._si_log.append(f">>> 开始 {type_name} 批量签到 <<<")

        self._signin_scan_worker = _SignInByTypeWorker(self._client, sign_type, location)
        self._signin_scan_worker.log_signal.connect(lambda m: self._si_log.append(m))
        self._signin_scan_worker.finished.connect(
            lambda ok, results, msg: self._on_signin_by_type_done(ok, results, msg))
        self._si_scan_btn.setEnabled(False)
        self._si_auto_btn.setEnabled(False)
        self._signin_scan_worker.start()

    def _on_signin_by_type_done(self, ok: bool, results: list, msg: str):
        self._si_scan_btn.setEnabled(True)
        self._si_auto_btn.setEnabled(True)
        success = sum(1 for r in results if r.get("status") == "success")
        self._si_log.append(f">>> 完成: {success}/{len(results)} 成功 <<<")
        # 刷新活动列表
        self._on_signin_scan()

    # ═══════════════════════════════════════════════════════════════
    #  下载逻辑
    # ═══════════════════════════════════════════════════════════════

    def _on_dl_scan(self):
        if not self._client:
            QMessageBox.warning(self, "提示", "请先登录。")
            return
        idx = self._dl_course_cb.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, "提示", "请先选择一门课程。")
            return
        c = self._dl_course_cb.itemData(idx)
        if not c or not c.if_open:
            QMessageBox.warning(self, "提示", "所选课程已关闭。")
            return

        self._dl_scan_btn.setEnabled(False)
        self._dl_log.append(f"正在扫描: {c.name} ...")

        self._dl_scan_worker = _DownloadScanWorker(self._client, c.course_id, c.class_id)
        self._dl_scan_worker.finished.connect(self._on_dl_scan_done)
        self._dl_scan_worker.start()

    def _on_dl_scan_done(self, ok: bool, items: list, msg: str):
        self._dl_scan_btn.setEnabled(True)
        self._dl_log.append(f"[{'完成' if ok else '失败'}] {msg}")
        self._dl_all_items = items  # unfiltered
        self._refresh_dl_table()

    def _refresh_dl_table(self):
        """Filter items by resource type and populate table."""
        filter_idx = self._dl_type_filter.currentIndex()
        items = self._dl_all_items
        if filter_idx == 1:
            items = [it for it in items if it.get("resource_kind") == "media"]
        elif filter_idx == 2:
            items = [it for it in items if it.get("resource_kind") == "attachment"]

        self._dl_items = items
        self._dl_table.setRowCount(0)
        for i, item in enumerate(items):
            self._dl_table.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self._dl_table.setItem(i, 0, chk)
            self._dl_table.setItem(i, 1, QTableWidgetItem(item.get("name", "")))
            # 资源类型: 显示 module 值
            self._dl_table.setItem(i, 2, QTableWidgetItem(item.get("type", "")))
            # 类别列: 媒体 / 课件资料
            kind_label = "课件资料" if item.get("resource_kind") == "attachment" else "媒体"
            kind_item = QTableWidgetItem(kind_label)
            if item.get("resource_kind") == "attachment":
                kind_item.setForeground(Qt.GlobalColor.cyan)
            self._dl_table.setItem(i, 3, kind_item)
            self._dl_table.setItem(i, 4, QTableWidgetItem(item.get("chapter_name", "")))

    def _on_dl_type_filter_changed(self):
        self._refresh_dl_table()

    def _on_dl_select_all(self):
        for i in range(self._dl_table.rowCount()):
            item = self._dl_table.item(i, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _on_dl_deselect_all(self):
        for i in range(self._dl_table.rowCount()):
            item = self._dl_table.item(i, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _on_dl_browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择下载目录", self._dl_dir_le.text())
        if d:
            self._dl_dir_le.setText(d)

    def _on_dl_download(self):
        checked = []
        for i in range(self._dl_table.rowCount()):
            item = self._dl_table.item(i, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                checked.append(i)

        if not checked:
            QMessageBox.warning(self, "提示", "请至少选择一个文件。")
            return

        out_dir = self._dl_dir_le.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "提示", "请选择输出目录。")
            return

        idx = self._dl_course_cb.currentIndex()
        c = self._dl_course_cb.itemData(idx)
        if not c:
            return

        self._dl_download_btn.setEnabled(False)
        self._dl_log.append(f"开始下载 {len(checked)} 个文件到 {out_dir} ...")
        self._dl_pending = checked.copy()
        self._dl_failures = 0
        self._dl_successes = 0
        self._dl_download_next(c.course_id, out_dir)

    def _dl_download_next(self, course_id: str, out_dir: str):
        if not self._dl_pending:
            self._dl_download_btn.setEnabled(True)
            self._dl_log.append(f"下载完毕: {self._dl_successes} 成功, {self._dl_failures} 失败")
            return
        i = self._dl_pending.pop(0)
        info = self._dl_items[i]

        self._dl_exec_worker = _DownloadExecWorker(self._client, course_id, info, out_dir)
        self._dl_exec_worker.log_signal.connect(lambda m: self._dl_log.append(m))
        self._dl_exec_worker.finished.connect(
            lambda ok2, msg2, fname: self._on_dl_one_done(ok2, msg2, fname, course_id, out_dir)
        )
        self._dl_exec_worker.start()

    def _on_dl_one_done(self, ok: bool, msg: str, fname: str, course_id: str, out_dir: str):
        if ok:
            self._dl_successes += 1
        else:
            self._dl_failures += 1
        self._dl_log.append(f"  [{'完成' if ok else '失败'}] {fname}: {msg}")
        self._dl_download_next(course_id, out_dir)

    # ═══════════════════════════════════════════════════════════════
    #  辅助方法
    # ═══════════════════════════════════════════════════════════════

    def _refresh_queue_display(self):
        total = len(self._queue)
        cur = self._queue_index
        self._queue_pbar_label.setText(f"队列: {cur} / {total}")
        self._queue_pbar.setValue(int((cur / total) * 100) if total else 0)

    def _set_status(self, status: str):
        self._status_label.setText(f"状态: {status}")
        self.status_updated.emit(self._profile.username, status, "")

    def _append_log(self, message: str):
        self._log_lines.append(message)
        if len(self._log_lines) > 500:
            self._log_lines = self._log_lines[-500:]
        self._log_view.setPlainText("\n".join(self._log_lines))
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )
