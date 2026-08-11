@echo off
setlocal
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "DRONE_HIVE_ROOT=%ROOT%"
set "PYTHONPATH=%ROOT%;G:\AI-Center"
set "PYTHONUTF8=1"
set "OLLAMA_HOST=http://127.0.0.1:11434"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "%ROOT%\drone\future_seer.py" (
  echo HALT: drone\future_seer.py missing
  pause
  exit /b 1
)

title DroneHive Future Seer
echo.
echo  Future Seer — multi-model speculative typeahead
echo  Hot lanes + Jane helpers + Ever swarm taps
echo.
"%PY%" -m drone seer hot
echo.
"%PY%" -m drone seer tui
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo Exit %EC%
  pause
)
exit /b %EC%
