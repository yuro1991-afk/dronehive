@echo off
setlocal
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "DRONE_HIVE_ROOT=%ROOT%"
set "PYTHONPATH=%ROOT%"
set "PYTHONUTF8=1"
set "OLLAMA_HOST=http://127.0.0.1:11434"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "%ROOT%\drone\super_llms.py" (
  echo HALT: drone\super_llms.py missing
  pause
  exit /b 1
)

title DroneHive Super LLMs
echo.
echo  DroneHive Super LLMs — all full models safe on RTX 3060 12GB
echo  Root: %ROOT%
echo.
"%PY%" -m drone super-llms status
echo.
"%PY%" -m drone super-llms tui
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo Exit %EC%
  pause
)
exit /b %EC%
