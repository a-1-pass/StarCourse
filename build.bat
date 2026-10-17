@echo off
rem ============================================================
rem  StarCourse Windows 一键打包脚本
rem  流程：安装依赖 -^> 安装 PyInstaller -^> 按 spec 构建单文件 exe
rem  产物：dist\StarCourse.exe
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/3] 安装运行依赖...
py -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] 安装 PyInstaller（打包工具，非运行依赖）...
py -m pip install "pyinstaller>=6.0,<7.0"
if errorlevel 1 goto :fail

echo [3/3] 开始构建（首次约 5-10 分钟）...
py -m PyInstaller StarCourse.spec --clean --noconfirm
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  构建完成！产物路径: %CD%\dist\StarCourse.exe
echo ============================================================
goto :eof

:fail
echo.
echo [错误] 构建失败，请检查上方日志。
exit /b 1
