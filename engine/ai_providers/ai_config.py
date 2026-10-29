"""AI 多模型配置加载。

配置来源优先级：
1. ``config.json`` —— GUI「AI 模型设置」写入，已被 .gitignore，支持热更新；
2. ``config.yml`` 的 ``AIConfig`` 段 —— 随仓库分发的模板（无密钥）。

两种来源最终归一为内部结构::

    {
      "active": "deepseek",
      "priority": ["deepseek", "openai", ...],
      "timeout": 30,
      "max_retries": 1,
      "providers": {"deepseek": {...}, "openai": {...}, ...},
    }
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import yaml

logger = logging.getLogger("AIConfig")

_HERE = os.path.dirname(os.path.abspath(__file__))  # engine/ai_providers/
_ROOT = os.path.dirname(_HERE)                      # engine/


def _candidate_json_paths():
    paths = []
    # 打包模式下 config.json 落用户数据目录（bundle 内不可写，且密钥不应随包分发）
    try:
        from runtime_paths import is_frozen, config_json_path
        if is_frozen():
            paths.append(config_json_path())
    except Exception:
        pass
    paths.extend([
        os.path.join(_ROOT, "config.json"),
        os.path.join(os.path.dirname(_ROOT), "config.json"),
    ])
    return paths


def normalize_json_config(raw: dict) -> dict:
    """将 config.json 的扁平结构转换为内部结构。"""
    meta_keys = ("default_provider", "priority", "timeout", "max_retries", "vote_times")
    providers = {}
    meta = {}
    for key, value in (raw or {}).items():
        if isinstance(value, dict):
            providers[key] = value
        elif key in meta_keys:
            meta[key] = value
    priority = meta.get("priority")
    if not isinstance(priority, list) or not priority:
        priority = [meta.get("default_provider", "deepseek")]
    return {
        "active": meta.get("default_provider", priority[0]),
        "priority": [str(p) for p in priority],
        "timeout": meta.get("timeout", 30),
        "max_retries": meta.get("max_retries", 0),
        "vote_times": meta.get("vote_times", 0),
        "providers": providers,
    }


def _load_config_json() -> Optional[dict]:
    for path in _candidate_json_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            normalized = normalize_json_config(raw)
            # 仅当确实存在提供商配置时才采用，避免空壳文件遮蔽 yml 模板
            if normalized["providers"]:
                logger.debug(f"AI 配置加载自 {path}")
                return normalized
        except Exception as e:
            logger.debug(f"读取 {path} 失败: {e}")
    return None


def _candidate_yml_paths():
    """config.yml 候选路径。

    frozen 模式读 bundle 内只读资源（``sys._MEIPASS``），
    开发模式读 ``engine/config.yml``（``_HERE`` 下并无此文件，
    历史上误读 ``engine/ai_providers/config.yml`` 导致回退从不生效）。
    """
    paths = []
    try:
        from runtime_paths import is_frozen, bundle_dir
        if is_frozen():
            paths.append(os.path.join(bundle_dir(), "config.yml"))
    except Exception:
        pass
    paths.append(os.path.join(_ROOT, "config.yml"))
    return paths


def _load_config_yml() -> Optional[dict]:
    for path in _candidate_yml_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ai_cfg = data.get("AIConfig")
            # 仅当 AIConfig 段确实存在提供商配置时才采用，保持与 json 侧一致的语义
            if isinstance(ai_cfg, dict) and ai_cfg.get("providers"):
                logger.debug(f"AI 配置回退加载自 {path}")
                return ai_cfg
        except Exception as e:
            logger.debug(f"读取 {path} AIConfig 失败: {e}")
    return None


def load_ai_config() -> dict:
    """加载归一化的 AI 配置（config.json 优先，回退 config.yml）。"""
    return _load_config_json() or _load_config_yml() or {}
