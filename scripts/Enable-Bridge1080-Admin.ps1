#Requires -RunAsAdministrator
<#
  Elevated: try enable GTX 1080 Ti for Stable Bridge.
  Does NOT change default Ollama / 3060 MAIN lane.
#>
$ErrorActionPreference = 'Continue'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $root 'drone\bridge_1080.py'))) {
  $root = 'G:\AI-Home\projects\ai-worker-drone-0.5b'
}
Set-Location $root
Write-Host "=== Enable 1080 Ti (admin) ===" -ForegroundColor Cyan
& $py -m drone bridge-1080 enable
& $py -m drone bridge-1080 status
Write-Host ""
Write-Host "If still Code 22/31: dual-GPU driver path — see ~/.ollama/fix-dual-gpu-admin.ps1" -ForegroundColor Yellow
