# _*_ coding:utf-8 _*_
"""engine/config.yml 运行参数：注释保留式读取/写入。"""
import os

import pytest

from src.runtime_settings import (
    SETTING_SPECS,
    apply_settings,
    config_yml_path,
    read_settings,
    save_settings,
)

SAMPLE = """\
FunConfig:
  deal-mission:
    video-mode: 1  # 0为立即完成,1为等时长刷取
    single-thread: false
    enable-multi-thread: true  # 是否开启多线程并发刷取
    max-concurrent-threads: 5  # 最大并发线程数
    cover-rate: 0.6  # 章节检测覆盖率阈值
    submit-mode: auto  # auto=按覆盖率决定
    speed: 1.0
    learning-mode: stage
    exhaustive:
      enable: false  # 客观题穷举
      interval: 3.0
    video-retry-rounds: 5

TikuConfig:
  provider: ''
"""


def test_apply_patches_scalar_and_keeps_comment():
    out = apply_settings(SAMPLE, {"video-mode": 0, "cover-rate": 0.8})
    assert "video-mode: 0  # 0为立即完成,1为等时长刷取" in out
    assert "cover-rate: 0.8  # 章节检测覆盖率阈值" in out


def test_apply_writes_lowercase_bool():
    out = apply_settings(SAMPLE, {"enable-multi-thread": False})
    assert "enable-multi-thread: false  # 是否开启多线程并发刷取" in out
    out = apply_settings(SAMPLE, {"enable-multi-thread": True})
    assert "enable-multi-thread: true" in out


def test_apply_patches_nested_key_only():
    out = apply_settings(SAMPLE, {"exhaustive-enable": True})
    assert "      enable: true  # 客观题穷举" in out
    # 嵌套块之外的键不受影响
    assert "    learning-mode: stage" in out


def test_apply_formats_numbers_without_trailing_zero():
    out = apply_settings(SAMPLE, {"speed": 2.0, "max-concurrent-threads": 3})
    assert "speed: 2\n" in out
    assert "max-concurrent-threads: 3  # 最大并发线程数" in out


def test_apply_appends_missing_key_into_block():
    text = SAMPLE.replace("    video-retry-rounds: 5\n", "")
    out = apply_settings(text, {"video-retry-rounds": 2})
    assert "    video-retry-rounds: 2\n" in out


def test_apply_rejects_unknown_key():
    with pytest.raises(KeyError):
        apply_settings(SAMPLE, {"nope": 1})


def test_apply_leaves_other_sections_untouched():
    out = apply_settings(SAMPLE, {"submit-mode": "save"})
    assert "TikuConfig:\n  provider: ''" in out
    assert out.count("learning-mode") == 1


def test_read_settings_returns_spec_keys():
    values = _read_from_text(SAMPLE)
    for spec in SETTING_SPECS:
        assert spec["key"] in values
    assert values["video-mode"] == 1
    assert values["max-concurrent-threads"] == 5
    assert values["exhaustive-enable"] is False


def test_save_roundtrip(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(SAMPLE, encoding="utf-8")
    save_settings(str(path), {"video-mode": 0, "speed": 1.8})
    values = read_settings(str(path))
    assert values["video-mode"] == 0
    assert values["speed"] == 1.8
    assert not os.path.exists(str(path) + ".tmp")


def test_read_missing_file_returns_defaults(tmp_path):
    values = read_settings(str(tmp_path / "missing.yml"))
    assert values["video-mode"] == 1
    assert values["max-concurrent-threads"] == 5


def test_config_yml_path_points_to_engine_in_dev_mode():
    path = config_yml_path()
    assert path.endswith(os.path.join("engine", "config.yml"))
    assert os.path.exists(path)


def _read_from_text(text: str) -> dict:
    """通过临时文件复用 read_settings 的解析逻辑。"""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".yml", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(text)
        name = fh.name
    try:
        return read_settings(name)
    finally:
        os.unlink(name)
