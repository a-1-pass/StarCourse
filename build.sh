#!/usr/bin/env bash
# ============================================================
#  StarCourse macOS / Linux 一键打包脚本
#  流程：安装依赖 -> 安装 PyInstaller -> 按 spec 构建单文件程序
#  产物：dist/StarCourse
# ============================================================
set -e
cd "$(dirname "$0")"

PY=python3

echo "[1/3] 安装运行依赖..."
"$PY" -m pip install -r requirements.txt

echo "[2/3] 安装 PyInstaller（打包工具，非运行依赖）..."
"$PY" -m pip install "pyinstaller>=6.0,<7.0"

echo "[3/3] 开始构建（首次约 5-10 分钟）..."
"$PY" -m PyInstaller StarCourse.spec --clean --noconfirm

echo ""
echo "============================================================"
echo " 构建完成！产物路径: $(pwd)/dist/StarCourse"
echo "============================================================"
