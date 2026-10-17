# _*_ coding:utf-8 _*_
"""日志导出 export_logs 的无头测试：临时目录造日志文件，验证 zip 生成与内容。"""
from __future__ import annotations

import re
import zipfile

import pytest

from src.log_manager import export_logs


@pytest.fixture
def log_dir(tmp_path):
    """构造一个含日志文件（含轮转备份）的临时日志目录。"""
    d = tmp_path / "logs"
    d.mkdir()
    (d / "starcourse_2025-01-01.log").write_text("第一行日志\n第二行日志", encoding="utf-8")
    (d / "starcourse_2025-01-02.log").write_text("另一天日志", encoding="utf-8")
    (d / "starcourse_2025-01-01.log.1").write_text("轮转备份", encoding="utf-8")
    (d / "not_a_log.txt").write_text("不应被打包", encoding="utf-8")
    return d


class TestExportLogs:
    def test_zip_created_with_timestamp_name(self, log_dir, tmp_path):
        dest = tmp_path / "out"
        zip_path = export_logs(str(dest), log_dir=str(log_dir))
        assert re.search(r"starcourse_logs_\d{8}_\d{6}\.zip$", zip_path.replace("\\", "/"))
        assert zipfile.is_zipfile(zip_path)

    def test_zip_contents(self, log_dir, tmp_path):
        dest = tmp_path / "out"
        zip_path = export_logs(str(dest), log_dir=str(log_dir))
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(zf.namelist())
            assert names == [
                "starcourse_2025-01-01.log",
                "starcourse_2025-01-01.log.1",
                "starcourse_2025-01-02.log",
            ]
            # 非日志文件不打包；内容完整（行尾跨平台归一后比较）
            assert "not_a_log.txt" not in names
            content = zf.read("starcourse_2025-01-01.log").decode("utf-8")
            assert content.replace("\r\n", "\n") == "第一行日志\n第二行日志"

    def test_dest_dir_auto_created(self, log_dir, tmp_path):
        dest = tmp_path / "deep" / "nested" / "out"
        zip_path = export_logs(str(dest), log_dir=str(log_dir))
        assert dest.exists()
        assert zipfile.is_zipfile(zip_path)

    def test_empty_log_dir_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            export_logs(str(tmp_path / "out"), log_dir=str(empty))
