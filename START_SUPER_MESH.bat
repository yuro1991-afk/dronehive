@echo off
setlocal
cd /d G:\AI-Home\projects\ai-worker-drone-0.5b
set OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo === Super Mesh: install flagships + hardwire ===
"%PY%" -m drone mesh install-flagships
echo === Super Mesh: status ===
"%PY%" -m drone mesh status
echo.
echo Usage:
echo   "%PY%" -m drone mesh tick "your goal"
echo   "%PY%" -m drone mesh run "your goal" --ticks 2
echo   "%PY%" -m drone mesh seal
endlocal
