"""应用只读资源路径解析（兼容开发模式与 PyInstaller 打包模式）。

打包（frozen）后，``datas`` 声明的资源会被解压到 ``sys._MEIPASS``，
源码目录假设（如 ``__file__`` 的相对路径）不再成立。GUI 层统一通过
本模块定位 theme.qss 等只读资源。
"""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """是否处于 PyInstaller 打包（frozen）运行状态。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> str:
    """只读资源根目录。

    - 打包模式：PyInstaller 解压目录 ``sys._MEIPASS``（datas 资源均在此）；
    - 开发模式：项目根目录（本文件的上两级）。
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """拼接资源根目录下的相对路径，如 ``resource_path("theme.qss")``。"""
    return os.path.join(resource_root(), *parts)
