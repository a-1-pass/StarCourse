# -*- coding: utf-8 -*-
"""日志输出策略测试：只保留关键错误与核心流程信息，去掉冗余 DEBUG 与启动横幅。"""
import logging
import sys
from logging.handlers import RotatingFileHandler

import pytest

from src.log_manager import log


def _file_handlers():
    return [h for h in log._py.handlers if isinstance(h, RotatingFileHandler)]


def _console_handlers():
    # 精确匹配本模块安装的 StreamHandler，排除 pytest 日志插件注入的捕获 handler
    return [h for h in log._py.handlers if type(h) is logging.StreamHandler]


def test_file_handler_keeps_info_level():
    """文件日志只记录 INFO 及以上，DEBUG 不再落盘。"""
    levels = [h.level for h in _file_handlers()]
    assert levels == [logging.INFO]


def test_console_handler_only_shows_warning_and_above():
    """控制台（GUI 日志面板）只显示 WARNING 及以上，避免刷屏。"""
    levels = [h.level for h in _console_handlers()]
    assert levels == [logging.WARNING]


def test_debug_record_is_filtered_from_file(tmp_path):
    """实际写入验证：DEBUG 记录不会进入日志文件。"""
    import logging as _logging
    from logging.handlers import RotatingFileHandler

    logger = _logging.getLogger("starcourse.policy_probe")
    logger.propagate = False
    probe = RotatingFileHandler(str(tmp_path / "probe.log"), encoding="utf-8")
    probe.setLevel(logging.INFO)
    logger.addHandler(probe)

    logger.setLevel(logging.DEBUG)
    logger.debug("debug-should-be-dropped")
    logger.info("info-should-be-kept")
    probe.flush()

    content = (tmp_path / "probe.log").read_text(encoding="utf-8")
    assert "debug-should-be-dropped" not in content
    assert "info-should-be-kept" in content
    logger.removeHandler(probe)
    probe.close()


def test_startup_message_is_a_single_concise_line(tmp_path):
    """启动提示收敛为一行，包含应用名与日志路径。"""
    from src.log_manager import startup_message

    log_path = tmp_path / "starcourse_2026-10-30.log"
    msg = startup_message(log_path)

    assert "\n" not in msg
    assert "StarCourse" in msg
    assert "starcourse_2026-10-30.log" in msg


def test_engine_loguru_handler_defaults_to_info():
    """引擎侧 loguru：控制台保留 INFO 及以上，另写一份 WARNING+ 到文件（便于无控制台时诊断）。"""
    from loguru import logger

    from src.engine_adapter import configure_engine_logging

    configure_engine_logging()
    levels = sorted(h.levelno for h in logger._core.handlers.values())
    assert levels == [logging.INFO, logging.WARNING]
    assert logger.level("INFO").no == logging.INFO


def test_engine_loguru_warning_lands_in_file(tmp_path, monkeypatch):
    """打包后无控制台，引擎侧 WARNING/ERROR 必须落盘，否则故障无法排查。"""
    from loguru import logger

    import src.engine_adapter as ea

    monkeypatch.setattr("src.log_manager.log_directory", lambda: tmp_path)
    ea.configure_engine_logging()
    logger.warning("engine-probe-warning")

    written = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("engine_*.log"))
    assert "engine-probe-warning" in written


def test_engine_logging_configuration_is_idempotent():
    """重复配置不应叠加 handler（否则同一行日志重复输出）。"""
    from loguru import logger

    from src.engine_adapter import configure_engine_logging

    configure_engine_logging()
    configure_engine_logging()
    assert len(logger._core.handlers) == 2


@pytest.fixture(autouse=True)
def _restore_loguru_default_handler():
    """测试结束后恢复 loguru 默认 handler，避免影响其它用例。"""
    yield
    try:
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr)
    except Exception:
        pass
