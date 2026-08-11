@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo === Buzzer PARALLEL SWARM (peak proof + library) ===
"%PY%" -m drone hive-status
if errorlevel 1 exit /b 1
"%PY%" -m drone swarm-smoke --controller local --workers 4
exit /b %ERRORLEVEL%
