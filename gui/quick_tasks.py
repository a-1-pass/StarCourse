# _*_ coding:utf-8 _*_
"""快捷操作的任务收集与并发执行。

不依赖 Qt，便于单元测试；GUI 的后台线程仅做日志转发。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

#: 任务类型 -> 中文名
TASK_TYPE_LABELS = {
    "video": "视频",
    "quiz": "测验",
    "work": "作业",
    "read": "阅读",
    "document": "文档",
    "book": "电子书",
    "bbs": "讨论",
    "live": "直播",
    "hyperlink": "超链接",
}


def task_display_name(task: dict) -> str:
    """任务显示名：优先 name，其次 title。"""
    props = (task or {}).get("attachment", {}).get("property", {}) or {}
    return str(props.get("name") or props.get("title") or "?")


def collect_tasks(
    client,
    course_id: str,
    class_id: str,
    cpi: str,
    task_type: str,
    on_log=None,
    should_stop=None,
) -> list[dict]:
    """遍历章节并收集指定类型的任务点。"""
    log = on_log or (lambda _msg: None)
    if should_stop and should_stop():
        return []

    tasks: list[dict] = []
    ok, chapters, msg = client.fetch_chapters(course_id, class_id, cpi)
    if not ok:
        log(f"[错误] 获取章节失败: {msg}")
        return tasks

    for ch in chapters:
        if should_stop and should_stop():
            break
        chapter_id = ch.get("chapter_id")
        if not chapter_id:
            continue
        log(f"章节: {ch.get('name', chapter_id)}")
        ok2, items, _ = client.fetch_tasks(course_id, class_id, chapter_id, cpi)
        if not ok2:
            continue
        for task in items:
            if should_stop and should_stop():
                break
            if task.get("task_type") != task_type:
                continue
            tasks.append(task)
    return tasks


def build_handler(
    client,
    task_type: str,
    course_id: str,
    class_id: str,
    cpi: str,
    strategy: str = "first",
    use_tiku: bool = False,
    should_stop=None,
):
    """按任务类型绑定引擎处理方法；未知类型返回 None。"""
    handlers = {
        "video": lambda t: client.run_video_task(t),
        "quiz": lambda t: client.run_quiz_task(
            t, course_id, strategy, use_tiku=use_tiku),
        "work": lambda t: client.run_work_task(
            t, course_id, strategy, use_tiku=use_tiku),
        "read": lambda t: client.run_read_task(t, course_id),
        "document": lambda t: client.run_document_task(t, course_id),
        "book": lambda t: client.run_book_task(t, course_id),
        "bbs": lambda t: client.run_bbs_task(t, course_id, class_id, "random"),
        "live": lambda t: client.run_live_task(t, course_id),
        "hyperlink": lambda t: client.run_hyperlink_task(t, course_id),
    }
    return handlers.get(task_type)


def run_tasks(
    tasks: list[dict],
    handler,
    max_workers: int = 5,
    on_log=None,
    should_stop=None,
) -> tuple[int, int]:
    """并发执行任务列表，返回 (成功数, 总数)。

    - ``max_workers`` 小于 1 时按 1 处理；
    - 单个任务的异常只计为失败，不影响其它任务；
    - ``should_stop`` 为真时不再派发新任务。
    """
    log = on_log or (lambda _msg: None)
    if not tasks:
        return 0, 0
    if should_stop and should_stop():
        log("[停止] 任务已取消")
        return 0, 0

    workers = max(1, int(max_workers or 1))
    workers = min(workers, len(tasks))
    done = 0
    lock = threading.Lock()

    def _run(task: dict) -> None:
        nonlocal done
        if should_stop and should_stop():
            return
        name = task_display_name(task)
        try:
            ok = bool(handler(task))
        except Exception as exc:  # 单任务异常不影响其它任务
            ok = False
            log(f"  [失败] {name}: {exc}")
        else:
            log(f"  {'[完成]' if ok else '[失败]'} {name}")
        if ok:
            with lock:
                done += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_run, tasks))
    return done, len(tasks)


def run_quick_action(
    client,
    course_id: str,
    class_id: str,
    cpi: str,
    task_type: str,
    strategy: str = "first",
    use_tiku: bool = False,
    max_workers: int = 5,
    on_log=None,
    should_stop=None,
) -> tuple[bool, int, int]:
    """快捷操作入口：收集 + 并发处理指定类型任务。"""
    log = on_log or (lambda _msg: None)
    label = TASK_TYPE_LABELS.get(task_type, task_type)
    log(f"[快捷] 开始处理{label}任务")
    log(f"正在获取章节列表...（并发 {max(1, int(max_workers or 1))} 线程）")

    tasks = collect_tasks(client, course_id, class_id, cpi, task_type,
                          on_log=on_log, should_stop=should_stop)
    if not tasks:
        log(f"[完成] 未找到可处理的{label}任务")
        return True, 0, 0

    handler = build_handler(client, task_type, course_id, class_id, cpi,
                            strategy=strategy, use_tiku=use_tiku)
    if handler is None:
        log(f"[错误] 不支持的快捷任务类型: {task_type}")
        return False, 0, 0

    done, total = run_tasks(tasks, handler, max_workers=max_workers,
                            on_log=on_log, should_stop=should_stop)
    log(f"[完成] {label}任务: 完成 {done}/{total}")
    return True, done, total
