# Install DroneHive native desktop (Start Menu + Desktop).
# Uses Python GUI launcher by default - more reliable than frozen exe on this host.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "drone\app\desktop.py"))) {
  $Root = (Get-Location).Path
}

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\DroneHive"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "bin") | Out-Null

$Py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Py)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $Py = $cmd.Source } else { throw "Python 3.12 not found" }
}

# Always pin project root for data/configs
Set-Content -Path (Join-Path $InstallDir "APP_ROOT.txt") -Value $Root -Encoding UTF8

# Prefer reliable Python-native GUI (frozen exe often silent-crashes without console)
$launcherPs1 = Join-Path $InstallDir "bin\Start-DroneHive.ps1"
@(
  '$ErrorActionPreference = "Stop"'
  '$log = Join-Path $env:LOCALAPPDATA "Programs\DroneHive\launch.log"'
  'function W($m){ Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date).ToString("o"), $m) }'
  'try {'
  '  W "start"'
  '  $rootFile = Join-Path $PSScriptRoot "..\APP_ROOT.txt"'
  '  $root = (Get-Content -Raw $rootFile).Trim()'
  '  W ("root=" + $root)'
  '  $py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"'
  '  if (-not (Test-Path $py)) { $py = "python" }'
  '  W ("py=" + $py)'
  '  $env:DRONE_HIVE_ROOT = $root'
  '  Set-Location $root'
  '  & $py -m drone app desktop *>> $log 2>&1'
  '  W ("exit=" + $LASTEXITCODE)'
  '} catch {'
  '  W ("ERR " + $_.Exception.Message)'
  '  throw'
  '}'
) | Set-Content -Path $launcherPs1 -Encoding UTF8

$launcherCmd = Join-Path $InstallDir "DroneHive.cmd"
@(
  '@echo off'
  'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\Start-DroneHive.ps1"'
) | Set-Content -Path $launcherCmd -Encoding ASCII

# Also copy frozen exe if present (optional secondary)
$packagedExe = Join-Path $Root "dist\DroneHive\DroneHive.exe"
$usePackaged = $false
if (Test-Path $packagedExe) {
  Copy-Item -Path (Join-Path $Root "dist\DroneHive\*") -Destination $InstallDir -Recurse -Force
  Set-Content -Path (Join-Path $InstallDir "APP_ROOT.txt") -Value $Root -Encoding UTF8
  $usePackaged = $true
}

# Shortcuts always use Python launcher (reliable)
$LaunchTarget = "powershell.exe"
$LaunchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$launcherPs1`""
$WorkDir = $Root

$smDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\DroneHive"
New-Item -ItemType Directory -Force -Path $smDir | Out-Null

function New-Shortcut {
  param([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkDir)
  $w = New-Object -ComObject WScript.Shell
  $s = $w.CreateShortcut($Path)
  $s.TargetPath = $Target
  if ($Arguments) { $s.Arguments = $Arguments }
  $s.WorkingDirectory = $WorkDir
  $s.WindowStyle = 7
  $s.Description = "DroneHive Desktop native app"
  $s.Save()
}

$smLnk = Join-Path $smDir "DroneHive.lnk"
$deskLnk = Join-Path $env:USERPROFILE "Desktop\DroneHive.lnk"
New-Shortcut -Path $smLnk -Target $LaunchTarget -Arguments $LaunchArgs -WorkDir $WorkDir
New-Shortcut -Path $deskLnk -Target $LaunchTarget -Arguments $LaunchArgs -WorkDir $WorkDir

$un = Join-Path $InstallDir "Uninstall-DroneHive.ps1"
@(
  "Remove-Item -Recurse -Force '$InstallDir' -ErrorAction SilentlyContinue"
  "Remove-Item -Recurse -Force '$smDir' -ErrorAction SilentlyContinue"
  "Remove-Item -Force '$deskLnk' -ErrorAction SilentlyContinue"
  "Write-Host 'DroneHive removed.'"
) | Set-Content -Path $un -Encoding UTF8

$manifestObj = [ordered]@{
  app           = "DroneHive"
  version       = "1.0.0"
  install_dir   = $InstallDir
  launch_mode   = "python_native_gui"
  packaged_exe_copied = $usePackaged
  project_root  = $Root
  python        = $Py
  start_menu    = $smLnk
  desktop       = $deskLnk
  kind          = "native_desktop"
  not_browser   = $true
  utc           = (Get-Date).ToUniversalTime().ToString("o")
}
$json = $manifestObj | ConvertTo-Json
Set-Content (Join-Path $InstallDir "INSTALL_MANIFEST.json") -Value $json -Encoding UTF8
New-Item -ItemType Directory -Force -Path (Join-Path $Root "out") | Out-Null
Set-Content (Join-Path $Root "out\DESKTOP_INSTALL_MANIFEST.json") -Value $json -Encoding UTF8

Write-Host "INSTALLED (python native GUI)"
Write-Host "  $deskLnk"
Write-Host "  $smLnk"
exit 0
