@echo off
setlocal
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo === FAST LANE smoke (5-node lite) ===
"%PY%" -m drone fast-smoke
if errorlevel 1 exit /b 1
echo === FAST vs FULL timing compare ===
"%PY%" -m drone lane-compare
exit /b %ERRORLEVEL%
