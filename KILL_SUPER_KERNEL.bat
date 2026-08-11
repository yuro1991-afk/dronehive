@echo off
REM HARD KILL Super Kernel — Boss or Grok only (sets authorized_stop, no auto-restart)
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo [super-kernel] AUTHORIZED KILL (Boss/Grok)
"%PY%" -m drone super-kernel kill
endlocal
