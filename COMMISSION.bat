@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo === DroneHive COMMISSION ===
"%PY%" -m drone app commission
exit /b %ERRORLEVEL%
