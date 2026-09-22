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

_HERE = os.path.dirname(os.path.abspath(__file__))  # engine/
_ROOT = os.path.dirname(_HERE)                      # project root


def _candidate_json_paths():
    return [
        os.path.join(_HERE, "config.json"),
        os.path.join(_ROOT, "config.json"),
    ]


def normalize_json_config(raw: dict) -> dict:
    """将 config.json 的扁平结构转换为内部结构。"""
    meta_keys = ("default_provider", "priority", "timeout", "max_retries")
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


def _load_config_yml() -> Optional[dict]:
    path = os.path.join(_HERE, "config.yml")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        ai_cfg = data.get("AIConfig")
        if isinstance(ai_cfg, dict) and ai_cfg.get("providers"):
            return ai_cfg
    except Exception as e:
        logger.debug(f"读取 config.yml AIConfig 失败: {e}")
    return None


def load_ai_config() -> dict:
    """加载归一化的 AI 配置（config.json 优先，回退 config.yml）。"""
    return _load_config_json() or _load_config_yml() or {}
