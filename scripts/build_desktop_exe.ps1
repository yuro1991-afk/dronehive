# Build native DroneHive.exe with PyInstaller (no browser).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "drone\app\desktop.py"))) {
  $Root = (Get-Location).Path
}
$Py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Set-Location $Root
Write-Host "Root: $Root"
& $Py -m pip install -q pyinstaller customtkinter
$entry = Join-Path $Root "drone\app\desktop.py"
& $Py -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name DroneHive `
  --paths $Root `
  --add-data "configs;configs" `
  --add-data "docs;docs" `
  --hidden-import drone `
  --hidden-import drone.app `
  --hidden-import drone.app.desktop `
  --hidden-import drone.app.service `
  --hidden-import drone.app.links `
  --hidden-import drone.app.commission `
  --hidden-import drone.app.config `
  --hidden-import drone.chain `
  --hidden-import drone.hive `
  --hidden-import drone.fast_lane `
  --hidden-import drone.clean_slate `
  --hidden-import drone.tools `
  --hidden-import drone.ollama_brain `
  --hidden-import drone.library_bridge `
  --hidden-import drone.controllers `
  --hidden-import drone.buzzer `
  --hidden-import drone.work_order `
  --hidden-import customtkinter `
  --collect-all customtkinter `
  $entry

$exe1 = Join-Path $Root "dist\DroneHive\DroneHive.exe"
$exe2 = Join-Path $Root "dist\DroneHive.exe"
if (Test-Path $exe1) {
  Write-Host "BUILT: $exe1"
  exit 0
}
if (Test-Path $exe2) {
  Write-Host "BUILT: $exe2"
  exit 0
}
Write-Host "BUILD FAILED - exe not found"
exit 1
