@echo off
REM Optional Tk Pro UI (not primary). Runs in-process python — no "start" detach.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set "DRONE_HIVE_ROOT=%~dp0"
"%PY%" -u -m drone app desktop-pro %*
exit /b %ERRORLEVEL%
