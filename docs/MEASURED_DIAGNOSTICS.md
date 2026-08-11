# Measured diagnostics (no template-only GREEN)

## Law

Execute / critic / verify / seal **must** use real host measurements.

Required commands (exit 0):

1. `nvidia-smi` (GPU name, VRAM used/total, util)
2. Disk free via PowerShell `Get-PSDrive`
3. `ping -n 2 127.0.0.1`

Optional: Ollama `/api/tags` count.

Artifacts (per task workspace):

- `MEASURED_DIAGNOSTIC_REPORT.md`
- `MEASURED_DIAGNOSTIC.json` with `"ok": true|false`

**GREEN seal only if** `diag_ok` and `verify_ok`.  
`false_green: 0`

## Smoke

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
python -m drone run --goal "run real host diagnostics" --controller local --lm-assist none
```
