@echo off
REM Python CLI entry (pip console script preferred when on PATH).
REM For TUI use: dronehive-tui.cmd  or  START_TUI.bat
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set "DRONE_HIVE_ROOT=%~dp0"
"%PY%" -m drone %*
exit /b %ERRORLEVEL%
