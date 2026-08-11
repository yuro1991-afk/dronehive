@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
if "%~1"=="" (
  echo Usage: RUN_TASK.bat "goal text" [controller]
  echo Example: RUN_TASK.bat "build intake module" local
  exit /b 2
)
set GOAL=%~1
set CTRL=%~2
if "%CTRL%"=="" set CTRL=local
where py >nul 2>&1 && (
  py -3 -m drone run --goal "%GOAL%" --controller %CTRL%
  exit /b %ERRORLEVEL%
)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m drone run --goal "%GOAL%" --controller %CTRL%
exit /b %ERRORLEVEL%
