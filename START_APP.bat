@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
set DRONE_HIVE_ROOT=%~dp0
set DRONE_HIVE_ROOT=%DRONE_HIVE_ROOT:~0,-1%
REM Native CustomTkinter window (NOT browser, NOT silent frozen exe)
start "DroneHive Desktop" "%PY%" -m drone app desktop
exit /b 0
