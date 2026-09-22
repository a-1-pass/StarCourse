# -*- coding: utf-8 -*-
"""StarCourse task engine — internal package."""
import os
import sys

# Make engine modules importable as flat names (e.g. ``from config import GloConfig``)
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)
