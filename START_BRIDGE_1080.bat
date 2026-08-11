@echo off
REM Stable Bridge GTX 1080 Ti — LIVE daemon, NO KILL SWITCH (3060 MAIN protected)
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
if "%~1"=="" (
  echo Starting LIVE bridge-1080 (no kill switch)...
  "%PY%" -m drone bridge-1080 start
  "%PY%" -m drone bridge-1080 process
  exit /b 0
)
if /I "%~1"=="kill" (
  echo NO KILL SWITCH — refused by design.
  "%PY%" -m drone bridge-1080 kill
  exit /b 2
)
"%PY%" -m drone bridge-1080 %*
endlocal
