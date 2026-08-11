@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
where py >nul 2>&1 && (
  py -3 -m drone smoke
  exit /b %ERRORLEVEL%
)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m drone smoke
exit /b %ERRORLEVEL%
