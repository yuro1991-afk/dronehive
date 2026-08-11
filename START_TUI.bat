@echo off
setlocal
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "DRONE_HIVE_ROOT=%ROOT%"
set "PYTHONPATH=%ROOT%"
set "PYTHONUTF8=1"

set "EXE=%ROOT%\apps\dronehive-tui\target\release\dronehive-tui.exe"
if not exist "%EXE%" set "EXE=%ROOT%\apps\dronehive-tui\target\debug\dronehive-tui.exe"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "%EXE%" (
  echo HALT: missing dronehive-tui.exe
  echo Expected: %ROOT%\apps\dronehive-tui\target\release\dronehive-tui.exe
  pause
  exit /b 1
)

if not exist "%ROOT%\drone\__init__.py" (
  echo HALT: drone package missing under %ROOT%
  pause
  exit /b 1
)

title DroneHive
"%EXE%" --root "%ROOT%" --python "%PY%"
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo Exit code %EC%
  if exist "%ROOT%\out\TUI_LAST_ERROR.txt" (
    echo --- TUI_LAST_ERROR.txt ---
    type "%ROOT%\out\TUI_LAST_ERROR.txt"
  )
  pause
)
exit /b %EC%
