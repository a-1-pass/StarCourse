# _*_ coding:utf-8 _*_
"""运行时资源/数据路径解析测试（src.resource_path 与 engine.runtime_paths）。

覆盖开发模式与模拟 PyInstaller frozen 模式两种场景，
保证打包后只读资源走 sys._MEIPASS、可写数据落用户数据目录。
"""
import os
import sys

import pytest

from src import resource_path as rp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")

import runtime_paths as erp  # noqa: E402  (conftest 已将 engine 加入 sys.path)


# ── src.resource_path ─────────────────────────────────────

class TestResourcePath:
    def test_dev_root_is_project_root(self):
        assert os.path.normpath(rp.resource_root()) == os.path.normpath(ROOT)

    def test_resource_path_joins(self):
        assert rp.resource_path("theme.qss") == os.path.join(ROOT, "theme.qss")

    def test_frozen_uses_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert rp.resource_root() == str(tmp_path)
        assert rp.resource_path("theme.qss") == os.path.join(str(tmp_path), "theme.qss")

    def test_theme_qss_resolves_in_dev(self):
        # 开发模式下主题文件必须真实存在（打包时由 spec 的 datas 携带）
        assert os.path.exists(rp.resource_path("theme.qss"))


# ── engine.runtime_paths ──────────────────────────────────

class TestEngineRuntimePaths:
    def test_dev_writable_data_dir_is_engine_data(self):
        assert os.path.normpath(erp.writable_data_dir()) == os.path.normpath(
            os.path.join(ENGINE, "data"))

    def test_dev_data_file(self):
        assert erp.data_file("answer_records.db") == os.path.join(
            ENGINE, "data", "answer_records.db")

    def test_dev_config_json_path(self):
        assert erp.config_json_path() == os.path.join(ENGINE, "config.json")

    def test_dev_bundle_dir_is_engine(self):
        assert os.path.normpath(erp.bundle_dir()) == os.path.normpath(ENGINE)

    def test_frozen_data_file_seeds_from_bundle(self, monkeypatch, tmp_path):
        """frozen 时：目标不存在则从 bundle 种子拷贝初始数据。"""
        bundle = tmp_path / "bundle"
        (bundle / "data").mkdir(parents=True)
        seed = bundle / "data" / "answer_records.db"
        seed.write_bytes(b"seed-bytes")
        user_dir = tmp_path / "user"

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        monkeypatch.setattr(erp, "user_data_dir", lambda: str(user_dir))

        path = erp.data_file("answer_records.db")
        assert path == os.path.join(str(user_dir), "data", "answer_records.db")
        # 种子被拷贝且内容一致
        with open(path, "rb") as f:
            assert f.read() == b"seed-bytes"

        # 已存在的用户文件不会被种子覆盖
        seed.write_bytes(b"new-seed")
        path2 = erp.data_file("answer_records.db")
        with open(path2, "rb") as f:
            assert f.read() == b"seed-bytes"

    def test_frozen_config_json_in_user_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(erp, "user_data_dir", lambda: str(tmp_path))
        assert erp.config_json_path() == os.path.join(str(tmp_path), "config.json")

    def test_decoder_default_map_table_dev(self):
        """开发模式下字体映射表默认路径保持 engine/data/ 下不变。"""
        from font_decode import decoder
        assert decoder.DEFAULT_MAP_TABLE == os.path.join(
            ENGINE, "data", "font_map_table.json")
