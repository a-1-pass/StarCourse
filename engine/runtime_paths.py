# -*- coding: utf-8 -*-
"""引擎运行时路径解析：兼容开发模式与 PyInstaller 打包（frozen）模式。

- 只读资源（config.yml、字体映射表等随包数据）：frozen 时位于 ``sys._MEIPASS``；
- 可写数据（答题记录库、AI 答案缓存、AI 密钥配置）：frozen 时落用户数据目录，
  避免写入只读/临时的 bundle 目录导致运行失败或数据丢失。

本模块仅依赖标准库， engine 内各模块以扁平方式 ``import runtime_paths`` 使用。
"""
from __future__ import annotations

import os
import shutil
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))


def is_frozen() -> bool:
    """是否处于 PyInstaller 打包（frozen）运行状态。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> str:
    """只读资源根目录：frozen 为 ``sys._MEIPASS``，否则为 engine 目录。

    注：frozen 时 engine 的扁平模块被 PyInstaller 视为顶层模块，
    其 ``__file__`` 均指向 ``_MEIPASS`` 根下，与本函数返回值一致。
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return _ENGINE_DIR


def user_data_dir() -> str:
    """用户级可写数据目录（跨平台），自动创建。

    优先复用主项目 ``src.network_utils.get_app_data_dir``；
    engine 独立运行（CLI）时降级为内联实现。
    """
    try:
        from src.network_utils import get_app_data_dir
        return get_app_data_dir("StarCourse")
    except Exception:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "StarCourse")
        os.makedirs(path, exist_ok=True)
        return path


def writable_data_dir() -> str:
    """可写数据目录：frozen 为用户数据目录下 ``data/``，否则为 ``engine/data/``。"""
    if is_frozen():
        path = os.path.join(user_data_dir(), "data")
    else:
        path = os.path.join(_ENGINE_DIR, "data")
    os.makedirs(path, exist_ok=True)
    return path


def data_file(name: str, seed_from_bundle: bool = True) -> str:
    """返回可写数据文件路径。

    frozen 且目标文件不存在时，尝试从 bundle 内 ``data/`` 拷贝初始数据
    （如随包分发的答题记录库），保证首次运行即可用。
    """
    target = os.path.join(writable_data_dir(), name)
    if seed_from_bundle and is_frozen() and not os.path.exists(target):
        seed = os.path.join(bundle_dir(), "data", name)
        if os.path.exists(seed):
            try:
                shutil.copy2(seed, target)
            except OSError:
                pass
    return target


def config_json_path() -> str:
    """AI 密钥配置文件（config.json）路径。

    frozen 时落用户数据目录（bundle 内不可写且含密钥不应随包分发）；
    开发期保持 ``engine/config.json`` 不变。
    """
    if is_frozen():
        return os.path.join(user_data_dir(), "config.json")
    return os.path.join(_ENGINE_DIR, "config.json")
