# _*_ coding:utf-8 _*_
"""章节级学习进度持久化模块（SQLite，标准库实现，零额外依赖）。

复用 [engine/answer_record.py](engine/answer_record.py) 的路径推导、
`threading.Lock`、单例 `get_*_dao()` 模式，与之**同库多表**共存于
`engine/data/answer_records.db`，避免新增数据库文件、迁移成本。

仅做章节级粒度（按 `user_id + course_id + chapter_id` 落库）。
单视频级断点续看由 `Video.study()` 的 `attachment.playTime` 处理，
此处不侵入 Media 类，避免过度工程。

支持的查询：
- `save_chapter_done`：章末写入（done / partial）
- `get_completed_chapters`：已通过章节 id 集合（用于 stage 模式跳过）
- `get_resume_position`：续学起点（最近一个 partial 章节的 id；全为 done 时返回 None）
- `clear_course_progress`：清空指定课程的进度
"""
import os
import sqlite3
import threading
import time

import loguru

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_DB_PATH = os.path.join(_DB_DIR, "answer_records.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chapter_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    chapter_name TEXT DEFAULT '',
    task_total INTEGER DEFAULT 0,
    task_done INTEGER DEFAULT 0,
    status TEXT DEFAULT 'done',
    UNIQUE(user_id, course_id, chapter_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_user_course ON chapter_progress(user_id, course_id);
CREATE INDEX IF NOT EXISTS idx_progress_status ON chapter_progress(user_id, course_id, status);
"""


class CourseProgressDAO:
    """章节级进度的读写访问对象。

    线程安全：所有公共方法通过 `self._lock` 串行化；SQLite 连接以
    `check_same_thread=False` 打开，配合外层锁使用。
    """

    def __init__(self, db_path: str = None):
        self._db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ── 写入 ──
    def save_chapter_done(self, user_id, course_id, chapter_id, chapter_name="",
                          task_total=0, task_done=0, status="done") -> None:
        """幂等写入章节进度。status ∈ {'done', 'partial'}。

        - 'done'：本章全部任务点通过（闯关门禁通过）
        - 'partial'：本章有任务点失败（闯关未通过，应停止后续章节）
        """
        with self._lock:
            self._conn.execute(
                """INSERT INTO chapter_progress
                       (ts, user_id, course_id, chapter_id, chapter_name, task_total, task_done, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, course_id, chapter_id) DO UPDATE SET
                     ts=excluded.ts, chapter_name=excluded.chapter_name,
                     task_total=excluded.task_total, task_done=excluded.task_done,
                     status=excluded.status""",
                (time.time(), str(user_id), str(course_id), str(chapter_id),
                 str(chapter_name or ""), int(task_total), int(task_done),
                 str(status or "done")),
            )
            self._conn.commit()

    # ── 查询 ──
    def get_completed_chapters(self, user_id, course_id) -> set:
        """返回已通过（status='done'）的 chapter_id 集合。"""
        with self._lock:
            cur = self._conn.execute(
                """SELECT chapter_id FROM chapter_progress
                   WHERE user_id = ? AND course_id = ? AND status = 'done'""",
                (str(user_id), str(course_id)))
            return {row[0] for row in cur.fetchall()}

    def get_resume_position(self, user_id, course_id):
        """续学起点：返回最近一个 status='partial' 的 chapter_id；
        若无 partial 则返回 None（表示所有已记录章节均为 done，可从头开始或跳过）。

        选择 partial 而非 done 作为起点的理由：
        - done 章节已被 `get_completed_chapters` 标记跳过；
        - partial 章节未通过，需重新尝试。
        """
        with self._lock:
            cur = self._conn.execute(
                """SELECT chapter_id FROM chapter_progress
                   WHERE user_id = ? AND course_id = ? AND status = 'partial'
                   ORDER BY ts DESC LIMIT 1""",
                (str(user_id), str(course_id)))
            row = cur.fetchone()
            return row[0] if row else None

    def clear_course_progress(self, user_id, course_id) -> None:
        """清空指定课程的章节进度（review 模式或用户主动重置时调用）。"""
        with self._lock:
            self._conn.execute(
                """DELETE FROM chapter_progress
                   WHERE user_id = ? AND course_id = ?""",
                (str(user_id), str(course_id)))
            self._conn.commit()

    def list_chapter_progress(self, user_id, course_id) -> list:
        """调试用：返回某课程所有章节进度记录。"""
        with self._lock:
            cur = self._conn.execute(
                """SELECT chapter_id, chapter_name, task_total, task_done, status, ts
                   FROM chapter_progress
                   WHERE user_id = ? AND course_id = ?
                   ORDER BY ts""",
                (str(user_id), str(course_id)))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


_dao = None
_dao_lock = threading.Lock()


def get_course_progress_dao() -> "CourseProgressDAO":
    """进程级单例 DAO。"""
    global _dao
    if _dao is None:
        with _dao_lock:
            if _dao is None:
                _dao = CourseProgressDAO()
    return _dao


def reset_course_progress_dao_for_test(db_path: str = None) -> "CourseProgressDAO":
    """测试专用：用临时库重置单例，返回新实例。生产代码勿调用。"""
    global _dao
    with _dao_lock:
        if _dao is not None:
            try:
                _dao.close()
            except Exception as e:
                loguru.logger.debug(f"close prior dao failed: {e}")
        _dao = CourseProgressDAO(db_path=db_path)
    return _dao
