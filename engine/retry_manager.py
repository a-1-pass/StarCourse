# _*_ coding:utf-8 _*_
# 重试机制和队列管理，adapted from reference implementation
import threading
import time
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from queue import PriorityQueue, Empty
from typing import Any, Callable, Optional, List

import loguru


class TaskStatus(Enum):
    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    RETRY = 4
    NOT_OPEN = 5


@dataclass(order=True)
class RetryTask:
    priority: int
    task: Any = field(compare=False)
    tries: int = 0
    max_tries: int = 5
    status: TaskStatus = TaskStatus.PENDING

    def increment_tries(self) -> bool:
        self.tries += 1
        return self.tries < self.max_tries


class RetryManager:
    def __init__(self, max_retries: int = 5, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.retry_queue: PriorityQueue[RetryTask] = PriorityQueue()
        self.failed_tasks: List[RetryTask] = []
        self.lock = threading.RLock()
        self._running = False
        self._retry_thread: Optional[threading.Thread] = None

    def add_task(self, task: Any, priority: int = 0, max_tries: int = None):
        retry_task = RetryTask(
            priority=priority,
            task=task,
            max_tries=max_tries or self.max_retries
        )
        self.retry_queue.put(retry_task)
        loguru.logger.debug(f"任务已添加到重试队列: {task if len(str(task)) < 50 else str(task)[:50] + '...'}")

    def get_task(self, timeout: float = 1.0) -> Optional[RetryTask]:
        try:
            return self.retry_queue.get(timeout=timeout)
        except Empty:
            return None

    def task_done(self, task: RetryTask, success: bool, is_open: bool = True):
        if success:
            task.status = TaskStatus.SUCCESS
            self.retry_queue.task_done()
            loguru.logger.debug(f"任务完成: {task.task if len(str(task.task)) < 50 else str(task.task)[:50] + '...'}")
        elif not is_open:
            task.status = TaskStatus.NOT_OPEN
            self.retry_queue.task_done()
            loguru.logger.warning(f"任务未开放: {task.task if len(str(task.task)) < 50 else str(task.task)[:50] + '...'}")
        else:
            if task.increment_tries():
                task.status = TaskStatus.RETRY
                loguru.logger.warning(
                    f"任务失败，准备重试 ({task.tries}/{task.max_tries}): "
                    f"{task.task if len(str(task.task)) < 50 else str(task.task)[:50] + '...'}"
                )
                self.retry_queue.put(task)
                self.retry_queue.task_done()
            else:
                task.status = TaskStatus.FAILED
                self.failed_tasks.append(task)
                self.retry_queue.task_done()
                loguru.logger.error(
                    f"任务失败，已达最大重试次数: "
                    f"{task.task if len(str(task.task)) < 50 else str(task.task)[:50] + '...'}"
                )

    def start_retry_thread(self, handler: Callable[[Any], bool]):
        if self._running:
            return

        self._running = True

        def retry_loop():
            while self._running:
                try:
                    task = self.get_task(timeout=1.0)
                    if task:
                        time.sleep(self.retry_delay)
                        try:
                            success = handler(task.task)
                            self.task_done(task, success=success)
                        except Exception as e:
                            loguru.logger.error(f"重试任务执行异常: {e}")
                            self.task_done(task, success=False)
                except Exception as e:
                    loguru.logger.error(f"重试线程异常: {e}")

        self._retry_thread = threading.Thread(target=retry_loop, daemon=True)
        self._retry_thread.start()

    def stop_retry_thread(self):
        self._running = False

    def wait_all_done(self):
        self.retry_queue.join()

    def get_failed_tasks(self) -> List[RetryTask]:
        return list(self.failed_tasks)

    def get_failed_count(self) -> int:
        return len(self.failed_tasks)

    def clear_failed_tasks(self):
        self.failed_tasks.clear()


class RateLimiter:
    def __init__(self, call_interval: float = 0.5, video_log_interval: float = 2.0):
        self.last_call = time.time()
        self.last_video_log = time.time()
        self.lock = threading.RLock()
        self.call_interval = call_interval
        self.video_log_interval = video_log_interval

    def limit_rate(self, random_time: bool = False, random_min: float = 0.0, random_max: float = 1.0):
        with self.lock:
            now = time.time()
            base_wait = max(self.last_call + self.call_interval - now, 0)
            extra_wait = random.uniform(random_min, random_max) if random_time else 0
            call_wait = base_wait + extra_wait
            self.last_call = now + call_wait

        if call_wait > 0:
            time.sleep(call_wait)

    def limit_video_log_rate(self, random_time: bool = False, random_max: float = 2.0):
        with self.lock:
            now = time.time()
            base_wait = max(self.last_video_log + self.video_log_interval - now, 0)
            extra_wait = random.uniform(0, random_max) if random_time else 0
            call_wait = base_wait + extra_wait
            self.last_video_log = now + call_wait

        if call_wait > 0:
            time.sleep(call_wait)


class TaskExecutor:
    def __init__(self, max_workers: int = 5, max_retries: int = 3):
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.retry_manager = RetryManager(max_retries=max_retries)
        self._results: List[Any] = []
        self._errors: List[Exception] = []
        self._lock = threading.RLock()

    def execute(self, tasks: List[Any], handler: Callable[[Any], bool]) -> List[bool]:
        results = []
        threads = []

        def worker(task_queue):
            while True:
                try:
                    task = task_queue.pop(0)
                except IndexError:
                    break

                try:
                    success = False
                    for retry in range(self.max_retries):
                        try:
                            success = handler(task)
                            if success:
                                break
                        except Exception as e:
                            loguru.logger.warning(f"任务执行失败 (重试 {retry + 1}/{self.max_retries}): {e}")
                            if retry < self.max_retries - 1:
                                time.sleep(1 + retry * 0.5)

                    with self._lock:
                        results.append(success)
                        if not success:
                            self._errors.append(Exception(f"Task failed: {task}"))

                except Exception as e:
                    loguru.logger.error(f"任务执行异常: {e}")
                    with self._lock:
                        results.append(False)
                        self._errors.append(e)

        task_queue = list(tasks)
        for _ in range(min(self.max_workers, len(tasks))):
            t = threading.Thread(target=worker, args=(task_queue,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        return results

    def get_errors(self) -> List[Exception]:
        return list(self._errors)

    def has_errors(self) -> bool:
        return len(self._errors) > 0


global_rate_limiter = RateLimiter()
global_retry_manager = RetryManager()


def with_retry(max_retries: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)
                        loguru.logger.warning(
                            f"{func.__name__} 执行失败 (重试 {attempt + 1}/{max_retries}): {e}, "
                            f"等待 {wait_time} 秒后重试"
                        )
                        time.sleep(wait_time)
                    else:
                        loguru.logger.error(f"{func.__name__} 执行失败，已达最大重试次数: {e}")
            raise last_exception if last_exception else Exception("Max retries exceeded")
        return wrapper
    return decorator
