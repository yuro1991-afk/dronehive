@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

echo === DroneHive install (editable + desktop extras) ===
echo Python: %PY%
"%PY%" -m pip install -U pip setuptools wheel
if errorlevel 1 exit /b 1
"%PY%" -m pip install -e ".[desktop]"
if errorlevel 1 (
  echo Desktop extra failed — installing core only...
  "%PY%" -m pip install -e .
  if errorlevel 1 exit /b 1
)

echo.
echo === Health check ===
"%PY%" -m drone app health
if errorlevel 1 (
  echo HEALTH FAILED
  exit /b 1
)

echo.
echo === Write INSTALL_SEAL ===
"%PY%" "%~dp0scripts\write_install_seal.py"
if errorlevel 1 exit /b 1

set "SCRIPTS=%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
echo.
echo Install OK.
echo.
echo Commands:
echo   "%PY%" -m drone app health
echo   "%PY%" -m drone app desktop
echo   "%PY%" -m drone app commission
if exist "%SCRIPTS%\dronehive.exe" (
  echo   "%SCRIPTS%\dronehive.exe" health
  echo.
  echo If 'dronehive' is not found in a new shell, add to User PATH:
  echo   %SCRIPTS%
)
echo.
echo Desktop shortcuts: INSTALL_DESKTOP.bat
echo Release zip:       powershell -File scripts\package_release.ps1
exit /b 0
