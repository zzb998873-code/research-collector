@echo off
chcp 65001 >nul
setlocal
set "PROJECT=%~dp0.."
set "STATE=%PROJECT%\.runtime"

netstat -ano | findstr /r /c:"127.0.0.1:8789" /c:"0.0.0.0:8789" >nul 2>&1
if not errorlevel 1 (
  echo 接收端已在运行，端口 8789。
  exit /b 0
)

for /f "delims=" %%i in ('python -c "import sys;print(sys._base_executable)"') do set "PY=%%i"
if not defined PY (
  echo 找不到 Python，请先运行「安装接收端.ps1」。
  exit /b 1
)
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"

start "" "%PYW%" -X utf8 "%PROJECT%\windows\collector.py" --state "%STATE%" serve
echo 已在后台启动接收端，日志见 %STATE%\collector.log
