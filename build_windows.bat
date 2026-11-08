@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv，请先创建虚拟环境并安装 requirements.txt
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [信息] 正在安装 PyInstaller...
  ".venv\Scripts\python.exe" -m pip install pyinstaller || exit /b 1
)

if not exist "bin\ffmpeg.exe" echo [提示] 缺少 bin\ffmpeg.exe，打包产物将无法转换
if not exist "bin\ffprobe.exe" echo [提示] 缺少 bin\ffprobe.exe，打包产物将无法读取媒体信息

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean FrameFlow.spec || exit /b 1

echo.
echo 构建完成: dist\FrameFlow\FrameFlow.exe
endlocal
