@echo off
REM Super Kernel Lane — STABLE detached process (Boss/Grok kill only)
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
set SUPER_KERNEL_HOST=127.0.0.1
set SUPER_KERNEL_PORT=11450
if not defined OLLAMA_HOST set OLLAMA_HOST=http://127.0.0.1:11434
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo [super-kernel] starting STABLE multi-thread lane on %SUPER_KERNEL_HOST%:%SUPER_KERNEL_PORT%
echo [super-kernel] kill only: python -m drone super-kernel kill  (Boss/Grok)
"%PY%" -m drone super-kernel start --host %SUPER_KERNEL_HOST% --port %SUPER_KERNEL_PORT%
endlocal
