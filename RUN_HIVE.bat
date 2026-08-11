@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo === Buzzer Hive PARALLEL SWARM ===
"%PY%" -m drone hive-status
if errorlevel 1 exit /b 1
"%PY%" -m drone hive-smoke --controller local --workers 4
exit /b %ERRORLEVEL%
