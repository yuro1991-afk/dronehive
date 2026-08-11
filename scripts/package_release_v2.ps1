# Package DroneHive v2.0.0: wheel + TUI zip for GitHub Releases (no UAC bits).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) { $Root = (Get-Location).Path }
Set-Location -LiteralPath $Root

$Py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$Version = "2.0.0"
$Rel = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $Rel | Out-Null

Write-Host "=== wheel ==="
& $Py -m pip install -q build wheel
& $Py -m build --wheel --outdir $Rel

$Exe = Join-Path $Root "apps\dronehive-tui\target\release\dronehive-tui.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
  $env:PATH = "G:\AI-Home\tools\cargo\bin;G:\AI-Home\tools\rustup\toolchains\stable-x86_64-pc-windows-msvc\bin;" + $env:PATH
  $env:CARGO_HOME = "G:\AI-Home\tools\cargo"
  $env:RUSTUP_HOME = "G:\AI-Home\tools\rustup"
  Push-Location (Join-Path $Root "apps\dronehive-tui")
  cargo build --release
  Pop-Location
}
if (-not (Test-Path -LiteralPath $Exe)) { throw "TUI exe missing: $Exe" }

$Stage = Join-Path $Rel "stage-tui"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item $Exe (Join-Path $Stage "dronehive-tui.exe")
Copy-Item (Join-Path $Root "START_TUI.bat") $Stage
Copy-Item (Join-Path $Root "dronehive-tui.cmd") $Stage -ErrorAction SilentlyContinue
@"
DroneHive Pro TUI v$Version

1. Extract this folder.
2. In PowerShell or cmd (same window):
     .\dronehive-tui.exe --root <path-to-full-repo>
   Or from full repo checkout:
     .\START_TUI.bat

No admin / no UAC required.
Enter = run task  |  Esc = quit
"@ | Set-Content (Join-Path $Stage "README.txt") -Encoding UTF8

$ZipName = "dronehive-tui-$Version-win64.zip"
$ZipPath = Join-Path $Rel $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force
Remove-Item $Stage -Recurse -Force

$manifest = [ordered]@{
  schema      = "drone.hive.release.v2"
  app         = "DroneHive"
  version     = $Version
  utc         = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  wheel       = @(Get-ChildItem $Rel -Filter "dronehive-$Version*.whl" | ForEach-Object Name)
  tui_zip     = $ZipName
  tui_exe     = "apps/dronehive-tui/target/release/dronehive-tui.exe"
  exe_bytes   = (Get-Item $Exe).Length
  uac         = $false
  false_green = 0
}
($manifest | ConvertTo-Json) | Set-Content (Join-Path $Rel "RELEASE_MANIFEST.json") -Encoding UTF8
Write-Host "RELEASE OK"
Get-ChildItem $Rel | Format-Table Name, Length
