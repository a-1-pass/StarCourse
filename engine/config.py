# _*_ coding:utf-8 _*_
import os
import yaml
from pathlib import Path


class GloConfig:
    data: dict = None
    config_path: str = None

    @staticmethod
    def init_yaml_data():
        if not GloConfig.config_path:
            GloConfig.config_path = os.path.join(os.path.dirname(__file__), "config.yml")
        with open(GloConfig.config_path, "r", encoding="utf-8") as f:
            GloConfig.data = yaml.safe_load(f)

    @staticmethod
    def release_yaml_data():
        if not GloConfig.config_path:
            GloConfig.config_path = os.path.join(os.path.dirname(__file__), "config.yml")
        with open(GloConfig.config_path, "w", encoding="utf-8") as f:
            GloConfig.data = yaml.safe_dump(GloConfig.data, f, encoding='utf-8', allow_unicode=True, sort_keys=False)
