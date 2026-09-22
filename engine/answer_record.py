# _*_ coding:utf-8 _*_
"""答题记录本地持久化模块（SQLite，标准库实现，零额外依赖）。

提供三类能力，供 Work / Quiz 及未来 GUI 复用：
1. 答题记录：每题的题干、题型、作答内容、答案来源、时间，按课程+作业去重更新；
2. 刷题进度：按课程/作业统计已答题数、题库/AI 命中数、随机兜底数；
3. 错题集：命中失败（random 来源）或被标记为错误的题目，可导出 JSON 便于同步迁移。

数据文件位于 engine/data/answer_records.db，随项目目录整体迁移即可移植。
"""
import json
import os
import sqlite3
import threading
import time

import loguru

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_DB_PATH = os.path.join(_DB_DIR, "answer_records.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    course_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    q_type TEXT DEFAULT '',
    my_answer TEXT DEFAULT '',
    source TEXT DEFAULT '',
    correct INTEGER DEFAULT -1,   -- -1 未知 / 0 错误 / 1 正确
    UNIQUE(course_id, work_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_records_course ON records(course_id);
CREATE INDEX IF NOT EXISTS idx_records_wrong ON records(course_id, correct);
"""


class AnswerRecordDAO:
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
    def upsert_record(self, course_id, work_id, question_id, title="", q_type="",
                      my_answer="", source="", correct=-1):
        with self._lock:
            self._conn.execute(
                """INSERT INTO records (ts, course_id, work_id, question_id, title, q_type, my_answer, source, correct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(course_id, work_id, question_id) DO UPDATE SET
                     ts=excluded.ts, title=excluded.title, q_type=excluded.q_type,
                     my_answer=excluded.my_answer, source=excluded.source, correct=excluded.correct""",
                (time.time(), str(course_id), str(work_id), str(question_id),
                 str(title or ""), str(q_type or ""), str(my_answer or ""),
                 str(source or ""), int(correct)),
            )
            self._conn.commit()

    def record_from_questions(self, course_id, work_id, questions: list):
        """从 Work/Quiz 的题目结构批量落库（读取 answerField / answerSource）。"""
        count = 0
        for q in questions or []:
            try:
                q_id = str(q.get("id", ""))
                if not q_id:
                    continue
                af = q.get("answerField", {}) or {}
                self.upsert_record(
                    course_id=course_id,
                    work_id=work_id,
                    question_id=q_id,
                    title=q.get("title", ""),
                    q_type=q.get("type", ""),
                    my_answer=af.get(f"answer{q_id}", ""),
                    source=q.get(f"answerSource{q_id}", ""),
                )
                count += 1
            except Exception as e:
                loguru.logger.debug(f"答题记录写入失败: {e}")
        return count

    def set_correct(self, course_id, work_id, question_id, correct: bool):
        """提交后获知对错时回写（供错题重做/成绩校验使用）。"""
        self.upsert_record(course_id, work_id, question_id, correct=1 if correct else 0)

    # ── 查询 ──
    def get_progress(self, course_id=None, work_id=None) -> dict:
        """刷题进度统计：总数、题库/AI 命中数、随机兜底数、已判对错数。"""
        where, params = ["1=1"], []
        if course_id is not None:
            where.append("course_id = ?")
            params.append(str(course_id))
        if work_id is not None:
            where.append("work_id = ?")
            params.append(str(work_id))
        cond = " AND ".join(where)
        with self._lock:
            row = self._conn.execute(
                f"""SELECT COUNT(*),
                           SUM(CASE WHEN source IN ('tiku','ai','ai_content','cover') THEN 1 ELSE 0 END),
                           SUM(CASE WHEN source = 'random' THEN 1 ELSE 0 END),
                           SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END),
                           SUM(CASE WHEN correct = 0 THEN 1 ELSE 0 END)
                    FROM records WHERE {cond}""", params).fetchone()
        total, hit, random_cnt, right, wrong = row
        return {
            "total": total or 0,
            "hit": hit or 0,
            "random": random_cnt or 0,
            "correct": right or 0,
            "wrong": wrong or 0,
        }

    def get_wrong_records(self, course_id=None, limit: int = 200) -> list:
        """错题集：命中失败（random 来源）或被判定为错误的题目。"""
        cond, params = "", []
        if course_id is not None:
            cond = " AND course_id = ?"
            params.append(str(course_id))
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT course_id, work_id, question_id, title, q_type, my_answer, source, correct, ts
                    FROM records WHERE (source = 'random' OR correct = 0){cond}
                    ORDER BY ts DESC LIMIT ?""", params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def export_json(self, path: str, course_id=None) -> int:
        """导出记录为 JSON（进度同步 / 迁移用）。"""
        cond, params = "", []
        if course_id is not None:
            cond = " WHERE course_id = ?"
            params.append(str(course_id))
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT course_id, work_id, question_id, title, q_type, my_answer, source, correct, ts
                    FROM records{cond} ORDER BY ts""", params)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        return len(rows)

    def clear(self, course_id=None):
        cond, params = "", []
        if course_id is not None:
            cond = " WHERE course_id = ?"
            params.append(str(course_id))
        with self._lock:
            self._conn.execute(f"DELETE FROM records{cond}", params)
            self._conn.commit()


_dao = None


def get_answer_record_dao() -> AnswerRecordDAO:
    global _dao
    if _dao is None:
        _dao = AnswerRecordDAO()
    return _dao
