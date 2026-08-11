#Requires -Version 5.1
# Launch drone-ollama-mount TUI; start Ollama if needed.
$ErrorActionPreference = 'Continue'
$env:CARGO_HOME = 'G:\AI-Home\tools\cargo'
$env:RUSTUP_HOME = 'G:\AI-Home\tools\rustup'
$env:Path = "G:\AI-Home\tools\cargo\bin;$env:Path"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root 'target\release\drone-ollama-mount.exe'

# Ollama up?
$up = $false
try {
    $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
    $up = $true
} catch {}
if (-not $up) {
    Write-Host 'Starting Ollama...'
    $keep = Join-Path $env:USERPROFILE '.ollama\start-ollama-serve-keep.ps1'
    $cmd = Join-Path $env:USERPROFILE '.ollama\start-ollama-serve.cmd'
    if (Test-Path $keep) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $keep
    } elseif (Test-Path $cmd) {
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "`"$cmd`"") -WindowStyle Hidden
        Start-Sleep 5
    } else {
        Write-Host 'WARN: no ollama start script; TUI will run with ollama offline (local controller fallback)'
    }
}

if (-not (Test-Path $exe)) {
    Write-Host 'Building release...'
    Push-Location $root
    cargo build --release
    if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
    Pop-Location
}

if (-not (Test-Path $exe)) {
    Write-Host "FAIL: missing $exe"
    exit 1
}

Write-Host "Launching $exe tui"
& $exe tui
exit $LASTEXITCODE
