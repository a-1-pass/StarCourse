"""
StarCourse logging facility.

Provides a singleton logger with automatic rotation and retention policies.
All modules should import ``log`` from here rather than configuring their own handlers.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
import zipfile
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_APP_TAG = "StarCourse"
_LOGGER_TAG = "starcourse"

# ----- rotation / retention knobs -----
_MAX_FILE_MB = 10
_MAX_BACKUPS = 5
_MAX_AGE_DAYS = 7
_FMT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# ----- 日志级别策略：文件记录 INFO+，控制台只显示 WARNING+ 以避免刷屏 -----
_FILE_LEVEL = logging.INFO
_CONSOLE_LEVEL = logging.WARNING


def startup_message(log_path) -> str:
    """返回单行启动提示（应用名 + 日志文件路径）。"""
    return f"{_APP_TAG} started | log: {Path(log_path)}"


def _detect_os() -> str:
    import platform
    return {"windows": "windows", "darwin": "macos"}.get(platform.system().lower(), "linux")


def _log_directory() -> Path:
    """Return (and create) the directory where log files live."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / _APP_TAG
    elif _detect_os() == "macos":
        base = Path.home() / "Library" / "Logs" / _APP_TAG
    else:
        base = Path.home() / f".{_APP_TAG.lower()}"
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _purge_stale_logs(directory: Path, max_days: int = _MAX_AGE_DAYS, max_files: int = _MAX_BACKUPS) -> None:
    """Delete logs older than *max_days*; keep at most *max_files* most-recent ones."""
    try:
        threshold = datetime.now() - timedelta(days=max_days)
        entries: list[tuple[str, datetime]] = []
        for pat in ("*.log", "*.log.*"):
            for path in glob.glob(str(directory / pat)):
                try:
                    entries.append((path, datetime.fromtimestamp(os.path.getmtime(path))))
                except OSError:
                    pass
        # age-based purge
        for path, mtime in entries:
            if mtime < threshold:
                try:
                    os.remove(path)
                except OSError:
                    pass
        # count-based purge (keep newest *max_files*)
        survivors = sorted(
            [(p, m) for p, m in entries if os.path.exists(p)],
            key=lambda x: x[1], reverse=True,
        )
        for path, _ in survivors[max_files:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass  # logging must never crash the app


class _RotatingHandler(RotatingFileHandler):
    """Thin wrapper — ensures ``delay=True`` so the file is created lazily."""
    def __init__(self, filename, maxBytes=0, backupCount=0, encoding=None):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding, delay=True)


class LogManager:
    """
    Singleton log manager.

    Usage::

        from src.log_manager import log
        log.info("something happened")
    """
    _instance: Optional["LogManager"] = None
    _ready = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._ready:
            return
        self._ready = True

        self._py = logging.getLogger(_LOGGER_TAG)
        self._py.setLevel(_FILE_LEVEL)
        self._py.propagate = False

        log_dir = _log_directory()
        _purge_stale_logs(log_dir)

        today = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"{_LOGGER_TAG}_{today}.log"
        max_bytes = _MAX_FILE_MB * 1024 * 1024

        fh = _RotatingHandler(str(log_path), maxBytes=max_bytes, backupCount=_MAX_BACKUPS, encoding="utf-8")
        fh.setLevel(_FILE_LEVEL)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(_CONSOLE_LEVEL)

        fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)
        fh.setFormatter(fmt)
        ch.setFormatter(fmt)

        if not self._py.handlers:
            self._py.addHandler(fh)
            self._py.addHandler(ch)

        self._py.info(startup_message(log_path))

    # ---- public convenience proxies ----
    def debug(self, msg: str): self._py.debug(msg)
    def info(self, msg: str): self._py.info(msg)
    def warning(self, msg: str): self._py.warning(msg)
    def error(self, msg: str): self._py.error(msg)
    def critical(self, msg: str): self._py.critical(msg)
    def exception(self, msg: str): self._py.exception(msg)
    def success(self, msg: str): self._py.info(f"[OK] {msg}")


# Module-level singleton — import this everywhere
log = LogManager()


def child_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the StarCourse namespace."""
    if name:
        return logging.getLogger(f"{_LOGGER_TAG}.{name}")
    return log._py  # type: ignore[attr-defined]


def purge_all_logs() -> int:
    """Delete every log file immediately; return how many were removed."""
    removed = 0
    for pat in ("*.log", "*.log.*"):
        for path in glob.glob(str(_log_directory() / pat)):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def export_logs(dest_dir, log_dir=None) -> str:
    """把日志目录打包为带时间戳的 zip，返回 zip 文件路径（纯逻辑，可被无头测试）。

    :param dest_dir: 导出目标目录（不存在则自动创建）
    :param log_dir: 日志源目录，缺省为运行时日志目录
    :return: 生成的 zip 文件绝对路径
    :raises FileNotFoundError: 日志目录中没有任何日志文件
    """
    src = Path(log_dir) if log_dir else _log_directory()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # 收集日志文件（含轮转备份 *.log.1 等），按文件名排序保证 zip 内容稳定
    files = sorted(
        {p for pat in ("*.log", "*.log.*") for p in src.glob(pat) if p.is_file()},
        key=lambda p: p.name,
    )
    if not files:
        raise FileNotFoundError(f"日志目录中没有可导出的日志文件: {src}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = dest / f"{_LOGGER_TAG}_logs_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    return str(zip_path)
