#Requires -Version 5.1
<#
.SYNOPSIS
  Install Drone Ollama as a top-tier app on BOSS (Start Menu + Desktop + Ollama wire).
#>
$ErrorActionPreference = 'Stop'
$env:CARGO_HOME = 'G:\AI-Home\tools\cargo'
$env:RUSTUP_HOME = 'G:\AI-Home\tools\rustup'
$env:Path = "G:\AI-Home\tools\cargo\bin;$env:Path"

$Project = Split-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) -Parent
$InstallRoot = 'G:\AI-Home\apps\DroneOllama'
$BinDir = Join-Path $InstallRoot 'bin'
$LogDir = Join-Path $InstallRoot 'logs'
$OutDir = Join-Path $InstallRoot 'out'

Write-Host "=== Install Drone Ollama Workbench ==="
Write-Host "Project: $Project"
Write-Host "Install: $InstallRoot"

# Build release
Push-Location $Project
Write-Host "Building release (may take a minute)..."
cargo build --release
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "cargo build failed" }
Pop-Location

$exeSrc = Join-Path $Project 'target\release\drone-ollama-app.exe'
if (-not (Test-Path $exeSrc)) { throw "missing $exeSrc" }

New-Item -ItemType Directory -Force -Path $BinDir, $LogDir, $OutDir | Out-Null
Copy-Item -Force $exeSrc (Join-Path $BinDir 'drone-ollama-app.exe')

# Also copy/link mount helper if present
$mountSrc = 'G:\AI-Home\projects\drone-ollama-mount\target\release\drone-ollama-mount.exe'
if (Test-Path $mountSrc) {
    Copy-Item -Force $mountSrc (Join-Path $BinDir 'drone-ollama-mount.exe')
}

# Launcher that ensures Ollama then starts app
$launchPs1 = Join-Path $InstallRoot 'Launch-DroneOllama.ps1'
@'
#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Root "logs\launch.log"
function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format o), $m
  Add-Content -Path $Log -Value $line -ErrorAction SilentlyContinue
}
Log "launch start"

# Ensure Ollama critical
$ensure = Join-Path $env:USERPROFILE ".ollama\Ensure-OllamaCritical.ps1"
if (Test-Path $ensure) {
  Log "Ensure-OllamaCritical"
  try {
    powershell -NoProfile -ExecutionPolicy Bypass -File $ensure | Out-Null
  } catch { Log "ensure err: $_" }
} else {
  # quick ping
  try { Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null }
  catch {
    $keep = Join-Path $env:USERPROFILE ".ollama\start-ollama-serve-keep.ps1"
    if (Test-Path $keep) { powershell -NoProfile -ExecutionPolicy Bypass -File $keep | Out-Null }
  }
}

$exe = Join-Path $Root "bin\drone-ollama-app.exe"
if (-not (Test-Path $exe)) { Log "MISSING $exe"; exit 1 }
# Never kill existing session — second launch is refused by app mutex too
$running = Get-Process -Name "drone-ollama-app" -ErrorAction SilentlyContinue
if ($running) {
  Log "already running PID=$($running.Id) — not starting another"
  try { $running | ForEach-Object { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show("Drone Ollama is already running (PID $($_.Id)).","Drone Ollama") } } catch {}
  exit 0
}
Log "start $exe"
Start-Process -FilePath $exe -WorkingDirectory $Root
'@ | Set-Content -Path $launchPs1 -Encoding UTF8

# cmd launcher for double-click
$launchCmd = Join-Path $InstallRoot 'START.cmd'
@"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-DroneOllama.ps1"
"@ | Set-Content -Path $launchCmd -Encoding ASCII

# VBS silent launcher (no console flash)
$launchVbs = Join-Path $InstallRoot 'Launch-DroneOllama.vbs'
@"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$InstallRoot\Launch-DroneOllama.ps1""", 0, False
"@ | Set-Content -Path $launchVbs -Encoding ASCII

# App manifest
$manifest = @{
    schema           = 'drone.ollama.app.install.v1'
    name             = 'Drone Ollama Workbench'
    version          = '1.0.0'
    installed_utc    = (Get-Date).ToUniversalTime().ToString('o')
    install_root     = $InstallRoot
    exe              = (Join-Path $BinDir 'drone-ollama-app.exe')
    launcher         = $launchPs1
    ollama_host      = 'http://127.0.0.1:11434'
    drone_root       = 'G:\AI-Home\projects\ai-worker-drone-0.5b'
    mount_exe        = 'G:\AI-Home\projects\drone-ollama-mount\target\release\drone-ollama-mount.exe'
    false_green      = 0
    honesty          = @{
        full_models_per_drone = $false
        shared_ollama_brain   = $true
        human_brain_sim       = $false
    }
} | ConvertTo-Json -Depth 5
$manifestPath = Join-Path $InstallRoot 'APP_MANIFEST.json'
$manifest | Set-Content -Path $manifestPath -Encoding UTF8

# Start Menu
$startDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Drone Ollama'
New-Item -ItemType Directory -Force -Path $startDir | Out-Null
$wsh = New-Object -ComObject WScript.Shell
$lnk1 = $wsh.CreateShortcut((Join-Path $startDir 'Drone Ollama Workbench.lnk'))
$lnk1.TargetPath = 'wscript.exe'
$lnk1.Arguments = "`"$launchVbs`""
$lnk1.WorkingDirectory = $InstallRoot
$lnk1.Description = 'Drone Ollama Workbench — Ollama + 24 worker drones'
$lnk1.Save()

$lnk2 = $wsh.CreateShortcut((Join-Path $startDir 'Uninstall Drone Ollama.lnk'))
$uninst = Join-Path $InstallRoot 'Uninstall.ps1'
$lnk2.TargetPath = 'powershell.exe'
$lnk2.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$uninst`""
$lnk2.WorkingDirectory = $InstallRoot
$lnk2.Save()

# Desktop
$desk = [Environment]::GetFolderPath('Desktop')
$lnk3 = $wsh.CreateShortcut((Join-Path $desk 'Drone Ollama.lnk'))
$lnk3.TargetPath = 'wscript.exe'
$lnk3.Arguments = "`"$launchVbs`""
$lnk3.WorkingDirectory = $InstallRoot
$lnk3.Description = 'Drone Ollama Workbench'
$lnk3.Save()

# Uninstall script
@'
#Requires -Version 5.1
$InstallRoot = "G:\AI-Home\apps\DroneOllama"
$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Drone Ollama"
$desk = [Environment]::GetFolderPath("Desktop")
$deskLnk = Join-Path $desk "Drone Ollama.lnk"
if (Test-Path $startDir) { Remove-Item -Recurse -Force $startDir -ErrorAction SilentlyContinue }
if (Test-Path $deskLnk) { Remove-Item -Force $deskLnk -ErrorAction SilentlyContinue }
if (Test-Path $InstallRoot) { Remove-Item -Recurse -Force $InstallRoot -ErrorAction SilentlyContinue }
Write-Host "Drone Ollama uninstalled."
'@ | Set-Content -Path $uninst -Encoding UTF8

# HKCU uninstall registry (Add/Remove Programs lite)
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DroneOllama'
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name 'DisplayName' -Value 'Drone Ollama Workbench'
Set-ItemProperty -Path $regPath -Name 'DisplayVersion' -Value '1.0.0'
Set-ItemProperty -Path $regPath -Name 'Publisher' -Value 'Boss / AI-Home'
Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value $InstallRoot
Set-ItemProperty -Path $regPath -Name 'UninstallString' -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninst`""
Set-ItemProperty -Path $regPath -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $regPath -Name 'NoRepair' -Value 1 -Type DWord

# Install seal
$seal = @{
    status        = 'GREEN'
    false_green   = 0
    installed_utc = (Get-Date).ToUniversalTime().ToString('o')
    install_root  = $InstallRoot
    exe_exists    = (Test-Path (Join-Path $BinDir 'drone-ollama-app.exe'))
    manifest      = $manifestPath
    start_menu    = $startDir
    desktop_lnk   = (Join-Path $desk 'Drone Ollama.lnk')
} | ConvertTo-Json -Depth 4
$sealPath = Join-Path $InstallRoot 'INSTALL_SEAL.json'
$seal | Set-Content -Path $sealPath -Encoding UTF8
Copy-Item -Force $sealPath (Join-Path $Project 'out\INSTALL_SEAL.json') -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "INSTALL GREEN"
Write-Host "  App:      $InstallRoot"
Write-Host "  Desktop:  Drone Ollama.lnk"
Write-Host "  Start:    Drone Ollama\Drone Ollama Workbench"
Write-Host "  Seal:     $sealPath"
Write-Host "Launch with Desktop shortcut or:"
Write-Host "  powershell -File $launchPs1"
