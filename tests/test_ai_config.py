# _*_ coding:utf-8 _*_
"""engine.ai_providers.ai_config 配置加载链测试。

覆盖：
1. config.json 缺失时回退 engine/config.yml 的 AIConfig 段；
2. config.json 优先级高于 yml（现有语义不变）；
3. 两者均缺失时优雅降级为 {}；
4. config.json 为空壳（无 providers）时仍回退 yml。
"""
import json
import os

import pytest

from ai_providers import ai_config  # noqa: E402  (conftest 已将 engine 加入 sys.path)


_YML_TEMPLATE = """\
GloConfig:
  timeout: 3

AIConfig:
  active: ymlprovider
  priority:
    - ymlprovider
  timeout: 99
  max_retries: 2
  providers:
    ymlprovider:
      api_key: ''
      base_url: 'https://example.com'
      model: 'yml-model'
"""


def _write_yml(tmp_path):
    """在临时目录写入一份含 AIConfig 段的 config.yml。"""
    path = tmp_path / "config.yml"
    path.write_text(_YML_TEMPLATE, encoding="utf-8")
    return str(path)


@pytest.fixture
def patch_candidates(monkeypatch, tmp_path):
    """将 json/yml 候选路径重定向到临时目录，返回 (json路径, yml路径)。"""
    json_path = str(tmp_path / "config.json")
    yml_path = _write_yml(tmp_path)
    monkeypatch.setattr(ai_config, "_candidate_json_paths", lambda: [json_path])
    monkeypatch.setattr(ai_config, "_candidate_yml_paths", lambda: [yml_path])
    return json_path, yml_path


def test_yml_fallback_when_json_missing(patch_candidates):
    """config.json 不存在时，回退读取 yml 的 AIConfig 段。"""
    cfg = ai_config.load_ai_config()
    assert cfg["active"] == "ymlprovider"
    assert cfg["timeout"] == 99
    assert cfg["providers"]["ymlprovider"]["model"] == "yml-model"


def test_json_takes_priority_over_yml(patch_candidates):
    """config.json 存在且含 providers 时优先于 yml。"""
    json_path, _ = patch_candidates
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "default_provider": "jsonprovider",
            "timeout": 5,
            "jsonprovider": {"api_key": "k", "model": "json-model"},
        }, f)
    cfg = ai_config.load_ai_config()
    assert cfg["active"] == "jsonprovider"
    assert cfg["timeout"] == 5
    assert "ymlprovider" not in cfg["providers"]


def test_graceful_degradation_when_both_missing(monkeypatch, tmp_path):
    """json 与 yml 均不存在时返回 {}，不抛异常。"""
    monkeypatch.setattr(ai_config, "_candidate_json_paths",
                        lambda: [str(tmp_path / "none.json")])
    monkeypatch.setattr(ai_config, "_candidate_yml_paths",
                        lambda: [str(tmp_path / "none.yml")])
    assert ai_config.load_ai_config() == {}


def test_empty_json_falls_back_to_yml(patch_candidates):
    """config.json 存在但无 providers（空壳）时不遮蔽 yml 模板。"""
    json_path, _ = patch_candidates
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"default_provider": "jsonprovider"}, f)
    cfg = ai_config.load_ai_config()
    assert cfg["active"] == "ymlprovider"


def test_yml_without_aiconfig_section_returns_empty(monkeypatch, tmp_path):
    """yml 存在但无 AIConfig 段（或无 providers）时优雅降级。"""
    yml_path = tmp_path / "config.yml"
    yml_path.write_text("GloConfig:\n  timeout: 3\n", encoding="utf-8")
    monkeypatch.setattr(ai_config, "_candidate_json_paths",
                        lambda: [str(tmp_path / "none.json")])
    monkeypatch.setattr(ai_config, "_candidate_yml_paths", lambda: [str(yml_path)])
    assert ai_config.load_ai_config() == {}
