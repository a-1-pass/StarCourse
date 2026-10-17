# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— StarCourse 单文件（onefile）窗口程序。

用法::

    py -m PyInstaller StarCourse.spec --clean --noconfirm   # Windows
    python3 -m PyInstaller StarCourse.spec --clean --noconfirm  # macOS / Linux

要点：
- ``pathex`` 同时加入项目根与 ``engine/``，以解析 engine 的扁平 import
  （``from utils import ...``、``from config import ...`` 等历史设计）；
- ``hiddenimports`` 显式声明动态导入模块：engine/functions 下的功能插件包
  （cli_entry.py 运行时 ``importlib.import_module("functions.<名>")`` 扫描加载）、
  engine/ai_providers 注册表中的 provider 适配器，以及经函数级 import 触达的
  engine 扁平模块；
- ``datas`` 携带只读资源：theme.qss、engine/config.yml，以及可公开的种子
  数据（font_map_table.json）。本机运行时产物（answer_records.db 等）
  不打入发行包，frozen 首次运行时在用户数据目录新建。
  （frozen 时走 ``sys._MEIPASS``；数据库/缓存/密钥配置落用户数据目录）。
"""
import os

block_cipher = None

# SPECPATH 为 PyInstaller 内置变量：本 spec 文件所在目录（即项目根）
_PROJECT_ROOT = os.path.abspath(SPECPATH)
_ENGINE_DIR = os.path.join(_PROJECT_ROOT, "engine")

# ── 只读资源（frozen 后位于 sys._MEIPASS 对应子目录） ──
datas = [
    (os.path.join(_PROJECT_ROOT, "theme.qss"), "."),       # GUI 主题（main.py 读取）
    (os.path.join(_ENGINE_DIR, "config.yml"), "."),        # 引擎配置（engine 顶层模块按 __file__ 同级读取）
    (os.path.join(_PROJECT_ROOT, "config.example.json"), "."),  # AI 配置模板（参考用）
]
# 初始数据：只随包分发「可公开的种子文件」，绝不打包本机运行时产物。
# answer_records.db 是本机真实答题记录（含个人信息），严禁打入发行包；
# frozen 首次运行时由 engine/runtime_paths.data_file() 在用户数据目录新建。
# font_map_table.json（字体反混淆映射表）若存在则可安全分发。
_engine_data = os.path.join(_ENGINE_DIR, "data")
for _seed in ("font_map_table.json",):
    _seed_path = os.path.join(_engine_data, _seed)
    if os.path.isfile(_seed_path):
        datas.append((_seed_path, "data"))

# ── 动态导入模块显式声明 ──
hiddenimports = [
    # engine/functions 功能插件包（cli_entry.py 按目录扫描动态加载）
    "functions.deal_mission",
    "functions.deal_mission.deal_course",
    "functions.media_download",
    "functions.media_download.deal_media",
    "functions.set_log",
    "functions.set_time",
    "functions.set_time.deal_time",
    # engine/ai_providers 注册表中的 provider 适配器（factory.py: PROVIDER_REGISTRY）
    "ai_providers.factory",
    "ai_providers.base",
    "ai_providers.errors",
    "ai_providers.ai_config",
    "ai_providers.multi_client",
    "ai_providers.claude",
    "ai_providers.wenxin",
    "ai_providers.openai_compatible",
    # engine 扁平模块：经函数级 import 触达，显式声明防漏收
    "card_decode",
    "tiku",
    "vision_ocr",
    "non_choice",
    "exhaustive",
    "runtime_paths",
    "answer_record",
    "course_progress",
    "cache_dao",
    "font_decode",
    "font_decode.decoder",
]

a = Analysis(
    [os.path.join(_PROJECT_ROOT, "main.py")],
    pathex=[_PROJECT_ROOT, _ENGINE_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 开发/测试依赖，不打入产物
        # 注意：不要排除 tomli/packaging 等 —— setuptools 的 vendored 依赖
        # 钩子会给它们建别名，排除后会触发 modulegraph 冲突报错
        "pytest", "_pytest", "pluggy", "iniconfig", "py",
        "tkinter", "test", "tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="StarCourse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # 不启用 UPX 压缩（避免杀软误报）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                  # windowed：无控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
