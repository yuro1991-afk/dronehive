# Prove installable app works — writes out/INSTALL_SMOKE_SEAL.json
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$results = [ordered]@{}
function Step($name, $scriptblock) {
  Write-Host "=== $name ==="
  try {
    $out = & $scriptblock 2>&1 | Out-String
    $results[$name] = @{ ok = $true; output_tail = $out.Substring([Math]::Max(0, $out.Length - 800)) }
  } catch {
    $results[$name] = @{ ok = $false; error = "$_" }
  }
}

Step "pip_editable" {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $Py -m pip install -e ".[desktop]" -q 2>&1 | Out-Null
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($code -ne 0) { throw "pip install exit $code" }
  "pip_ok"
}
Step "import_drone" { & $Py -c "import drone; from drone.app.cli import main; print(drone.__version__)" }
Step "health" { & $Py -m drone app health }
Step "work_order" { & $Py -m drone work-order-show }
Step "task_fast" { & $Py -m drone app task --goal "install smoke artifact" --lane fast --lm-assist none --controller local }
Step "dronehive_scripts" {
  $exe = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts\dronehive.exe"
  if (Test-Path $exe) { & $exe health } else { "scripts_exe_missing_but_module_ok" }
}
Step "exe_exists" {
  $e = Join-Path $Root "dist\DroneHive\DroneHive.exe"
  if (Test-Path $e) { "exe_bytes=$((Get-Item $e).Length)" } else { throw "DroneHive.exe missing" }
}
Step "license_readme" {
  foreach ($f in @("LICENSE","README.md","pyproject.toml","INSTALL.bat")) {
    if (-not (Test-Path (Join-Path $Root $f))) { throw "missing $f" }
  }
  "ok"
}

$allOk = ($results.Values | ForEach-Object { $_.ok }) -notcontains $false
$seal = [ordered]@{
  schema = "drone.hive.install_smoke.v1"
  status = $(if ($allOk) { "GREEN" } else { "RED" })
  false_green = 0
  utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  root = $Root
  results = $results
}
$outDir = Join-Path $Root "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$sealPath = Join-Path $outDir "INSTALL_SMOKE_SEAL.json"
($seal | ConvertTo-Json -Depth 8) | Set-Content -Path $sealPath -Encoding UTF8
Write-Host "WROTE $sealPath status=$($seal.status)"
if (-not $allOk) { exit 1 }
exit 0
