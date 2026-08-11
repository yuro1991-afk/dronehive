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

title DroneHive Multi-Model Hub
echo.
echo  ================================================
echo   DroneHive MULTI-MODEL HUB (main product)
echo   Super LLMs roster + Future Seer typeahead
echo  ================================================
echo.

"%PY%" -m drone super-llms status
echo.
"%PY%" -m drone seer hot
echo.
echo  Opening Super LLMs TUI in this window...
echo  (Future Seer: run START_SEER.bat in another console)
echo.
"%PY%" -m drone super-llms tui
exit /b %ERRORLEVEL%
