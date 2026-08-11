@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
echo === AI Worker Drone — FULLY OPERATIONAL go-live ===
where py >nul 2>&1 && (
  py -3 -m drone go-live
  exit /b %ERRORLEVEL%
)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m drone go-live
exit /b %ERRORLEVEL%
