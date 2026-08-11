@echo off
REM DroneHive Pro TUI — current console, NO admin / NO UAC / NO extra window
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "DRONE_HIVE_ROOT=%ROOT%"
set "EXE=%ROOT%\apps\dronehive-tui\target\release\dronehive-tui.exe"
if not exist "%EXE%" set "EXE=%ROOT%\apps\dronehive-tui\target\debug\dronehive-tui.exe"
if not exist "%EXE%" (
  echo HALT: missing dronehive-tui.exe — build: apps\dronehive-tui ^& cargo build --release
  exit /b 1
)
"%EXE%" --root "%ROOT%" %*
exit /b %ERRORLEVEL%
