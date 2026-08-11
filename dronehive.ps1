# Run in the CURRENT PowerShell host only (like grok / other CLI TUIs).
# Usage:  .\dronehive.ps1
#    or:  . .\dronehive.ps1; dronehive
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
$exe = Join-Path $Root "apps\dronehive-tui\target\release\dronehive-tui.exe"
if (-not (Test-Path $exe)) {
  $exe = Join-Path $Root "apps\dronehive-tui\target\debug\dronehive-tui.exe"
}
if (-not (Test-Path $exe)) {
  Write-Error "HALT: build dronehive-tui first: cargo build --release in apps\dronehive-tui"
}
$env:DRONE_HIVE_ROOT = $Root
Set-Location $Root
& $exe --root $Root @args
exit $LASTEXITCODE
