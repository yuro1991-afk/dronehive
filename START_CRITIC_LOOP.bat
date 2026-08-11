@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
set DRONE_CRITIC=1
REM leashed interval default 90s — constant sentient core critic
where py >nul 2>&1 && (
  py -3 -m drone critic-loop
  exit /b %ERRORLEVEL%
)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m drone critic-loop
exit /b %ERRORLEVEL%
