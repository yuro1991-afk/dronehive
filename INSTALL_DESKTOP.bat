@echo off
setlocal
cd /d "%~dp0"
echo === Build native DroneHive.exe (optional; may take a few minutes) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_desktop_exe.ps1"
if errorlevel 1 (
  echo EXE build failed or skipped — installer will use Python-native GUI launcher.
)
echo.
echo === Install to Start Menu + Desktop ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-DroneHiveDesktop.ps1"
exit /b %ERRORLEVEL%
