# -*- coding: utf-8 -*-
"""Question answer cache with atomic file persistence."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional


class CacheDAO:
    """线程安全的JSON文件缓存"""

    DEFAULT_CACHE_FILE = "ai_answer_cache.json"

    def __init__(self, file: str = None):
        self.cache_file = Path(file) if file else Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), self.DEFAULT_CACHE_FILE))
        self._lock = threading.RLock()
        if not self.cache_file.is_file():
            self._write_cache({})

    def _read_cache(self) -> dict:
        try:
            with self._lock:
                if not self.cache_file.is_file():
                    return {}
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        return json.load(fp)
                except json.JSONDecodeError:
                    # 缓存损坏，尝试恢复
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            return json.loads(text[start:end + 1])
                    except Exception:
                        pass
                    # 备份损坏文件
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        import shutil
                        shutil.copy2(self.cache_file, bak_path)
                    except Exception:
                        pass
                    return {}
        except Exception:
            return {}

    def _write_cache(self, data: dict) -> None:
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=2)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
        except Exception:
            pass

    def get_cache(self, question: str) -> Optional[str]:
        """根据题目获取缓存答案"""
        data = self._read_cache()
        return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        """添加题目答案到缓存"""
        with self._lock:
            data = self._read_cache()
            data[question] = answer
            self._write_cache(data)

    def clear_cache(self) -> None:
        """清空缓存"""
        with self._lock:
            self._write_cache({})

    def get_stats(self) -> dict:
        """获取缓存统计"""
        data = self._read_cache()
        return {
            "total_cached": len(data),
            "cache_file": str(self.cache_file)
        }
