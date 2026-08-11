@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
REM Native CustomTkinter window — NOT a browser
start "DroneHive" "%PY%" -m drone app desktop
exit /b 0
