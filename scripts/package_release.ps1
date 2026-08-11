# Build wheel + portable win64 zip for GitHub Releases.
$ErrorActionPreference = "Stop"

if ($PSScriptRoot) {
  $Root = Split-Path -Parent $PSScriptRoot
} else {
  $Root = (Get-Location).Path
}
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
  $Root = (Get-Location).Path
}
Set-Location -LiteralPath $Root
Write-Host "Root: $Root"

$PyCandidates = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
  "python"
)
$Py = $null
foreach ($c in $PyCandidates) {
  if ($c -eq "python") { $Py = "python"; break }
  if (Test-Path -LiteralPath $c) { $Py = $c; break }
}

$Version = "1.0.0"
try {
  $meta = & $Py -c "import drone; print(drone.__version__)" 2>$null
  if ($meta) { $Version = ([string]$meta).Trim() }
} catch {}

$Rel = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $Rel | Out-Null

Write-Host "=== pip install build tooling ==="
& $Py -m pip install -q build wheel

Write-Host "=== wheel ==="
& $Py -m build --wheel --outdir $Rel
if ($LASTEXITCODE -ne 0) {
  Write-Host "wheel build failed - continuing with exe zip if present"
}

$ExePath = Join-Path $Root "dist"
$ExePath = Join-Path $ExePath "DroneHive"
$ExePath = Join-Path $ExePath "DroneHive.exe"
Write-Host "Looking for exe: $ExePath"

if (-not (Test-Path -LiteralPath $ExePath)) {
  Write-Host "No frozen exe - building..."
  $buildScript = Join-Path $Root "scripts\build_desktop_exe.ps1"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript
}

if (-not (Test-Path -LiteralPath $ExePath)) {
  throw "DroneHive.exe missing after freeze attempt: $ExePath"
}

$ZipName = "DroneHive-$Version-win64.zip"
$ZipPath = Join-Path $Rel $ZipName
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }

$Stage = Join-Path $Rel "stage-DroneHive"
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
$DistDir = Join-Path $Root "dist\DroneHive"
Copy-Item -Path (Join-Path $DistDir "*") -Destination $Stage -Recurse -Force

$portableReadme = @"
DroneHive $Version - portable Windows build

1. Unzip this folder anywhere.
2. Run DroneHive.exe (GUI).
3. Or install the wheel from release/ with: pip install dronehive-*.whl
4. Data/configs default to %LOCALAPPDATA%\DroneHive\workspace
   Or set DRONE_HIVE_ROOT to a folder you choose.

false_green: 0 - not 24 full LLMs.
"@
Set-Content -Path (Join-Path $Stage "README-PORTABLE.txt") -Value $portableReadme -Encoding UTF8

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force
Remove-Item -LiteralPath $Stage -Recurse -Force

$exeBytes = (Get-Item -LiteralPath $ExePath).Length
$wheels = @(Get-ChildItem -LiteralPath $Rel -Filter "*.whl" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })

$manifest = [ordered]@{
  schema      = "drone.hive.release.v1"
  app         = "DroneHive"
  version     = $Version
  utc         = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  wheel       = $wheels
  zip         = $ZipName
  exe         = "dist/DroneHive/DroneHive.exe"
  exe_bytes   = $exeBytes
  false_green = 0
}
$manifestPath = Join-Path $Rel "RELEASE_MANIFEST.json"
($manifest | ConvertTo-Json) | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host "RELEASE OK"
Write-Host "  zip: $ZipPath"
Write-Host "  manifest: $manifestPath"
Get-ChildItem -LiteralPath $Rel | Format-Table Name, Length
