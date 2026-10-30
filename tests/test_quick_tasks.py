# _*_ coding:utf-8 _*_
"""快捷操作：任务收集与并发执行（不依赖 Qt）。"""
import threading
import time

from gui.quick_tasks import (
    collect_tasks,
    run_quick_action,
    run_tasks,
    task_display_name,
)


class FakeClient:
    def __init__(self, chapters=None, tasks_by_chapter=None, fail_types=()):
        self._chapters = chapters or []
        self._tasks = tasks_by_chapter or {}
        self._fail_types = set(fail_types)
        self.calls = []
        self._lock = threading.Lock()

    def fetch_chapters(self, course_id, class_id, cpi):
        return True, self._chapters, "ok"

    def fetch_tasks(self, course_id, class_id, chapter_id, cpi):
        return True, self._tasks.get(chapter_id, []), "ok"

    def run_video_task(self, task):
        return self._record("video", task)

    def run_quiz_task(self, task, course_id="", strategy="first", use_tiku=False):
        return self._record("quiz", task)

    def _record(self, kind, task):
        with self._lock:
            self.calls.append((kind, task_display_name(task)))
        return kind not in self._fail_types


def _task(task_type, name):
    return {
        "task_type": task_type,
        "attachment": {"property": {"name": name, "title": name}},
    }


CHAPTERS = [
    {"chapter_id": "c1", "name": "第一章"},
    {"chapter_id": "c2", "name": "第二章"},
    {"chapter_id": "", "name": "无ID章节"},
]
TASKS = {
    "c1": [_task("video", "v1"), _task("quiz", "q1"), _task("read", "r1")],
    "c2": [_task("video", "v2"), _task("video", "v3")],
}


def test_collect_filters_by_task_type_and_skips_bad_chapters():
    client = FakeClient(CHAPTERS, TASKS)
    tasks = collect_tasks(client, "cid", "clid", "cpi", "video")
    assert [task_display_name(t) for t in tasks] == ["v1", "v2", "v3"]


def test_collect_stops_when_should_stop():
    client = FakeClient(CHAPTERS, TASKS)
    tasks = collect_tasks(client, "cid", "clid", "cpi", "video",
                          should_stop=lambda: True)
    assert tasks == []


def test_run_tasks_executes_all_and_counts_failures():
    client = FakeClient(CHAPTERS, TASKS, fail_types=("video",))
    items = [_task("video", f"v{i}") for i in range(4)]
    done, total = run_tasks(
        items, client.run_video_task, max_workers=2,
        on_log=lambda m: None,
    )
    assert total == 4
    assert done == 0


def test_run_tasks_uses_requested_concurrency():
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    def handler(task):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return True

    done, total = run_tasks(
        [_task("video", f"v{i}") for i in range(8)],
        handler, max_workers=4, on_log=lambda m: None,
    )
    assert (done, total) == (8, 8)
    assert 1 < state["peak"] <= 4


def test_run_tasks_clamps_single_worker():
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    def handler(task):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return True

    run_tasks([_task("video", f"v{i}") for i in range(3)],
              handler, max_workers=0, on_log=lambda m: None)
    assert state["peak"] == 1


def test_run_tasks_skips_when_stop_requested():
    logs = []
    done, total = run_tasks(
        [_task("video", f"v{i}") for i in range(5)],
        lambda task: True, max_workers=2,
        on_log=logs.append, should_stop=lambda: True,
    )
    assert done == 0 and total == 0


def test_run_quick_action_reports_summary():
    client = FakeClient(CHAPTERS, TASKS)
    logs = []
    ok, done, total = run_quick_action(
        client, "cid", "clid", "cpi", "video",
        max_workers=2, on_log=logs.append,
    )
    assert ok is True
    assert (done, total) == (3, 3)
    assert any("3/3" in line for line in logs)
    assert {name for _, name in client.calls} == {"v1", "v2", "v3"}


def test_run_quick_action_without_tasks_is_ok():
    client = FakeClient(CHAPTERS, TASKS)
    logs = []
    ok, done, total = run_quick_action(
        client, "cid", "clid", "cpi", "document",
        max_workers=3, on_log=logs.append,
    )
    assert ok is True and total == 0
    assert any("未找到" in line for line in logs)


def test_run_quick_action_tolerates_handler_exception():
    class BrokenClient(FakeClient):
        def run_quiz_task(self, task, course_id="", strategy="first", use_tiku=False):
            raise RuntimeError("boom")

    client = BrokenClient(CHAPTERS, TASKS)
    ok, done, total = run_quick_action(
        client, "cid", "clid", "cpi", "quiz",
        max_workers=2, on_log=lambda m: None,
    )
    assert ok is True and total == 1 and done == 0
