# Host leash (keep her calm)

On 12GB BOSS, the drone stack is **leashed by default**.

## Defaults

| Control | Value |
|---------|--------|
| Code worker 8b auto | **OFF** unless `DRONE_CODE_ENABLE=1` |
| App activity sample | **12s** (not 2s) |
| Ollama refresh | **60s** |
| UI repaint idle | **1s** |
| Code `num_predict` max | **512** |
| Ollama `keep_alive` | **2m** |
| No `tasklist` spam | yes |

Config: `configs/leash.json`

## Unleash code worker (when you want real 8b codegen)

```powershell
$env:DRONE_CODE_ENABLE = "1"
$env:DRONE_CODE_MODEL = "llama3.1:8b"
python -m drone run --goal "write add(a,b)" --controller ollama --lm-assist ollama
```

## Full unleash (not recommended on 12GB)

```powershell
$env:DRONE_LEASH = "0"
$env:DRONE_CODE_ENABLE = "1"
```

## Calm Ollama VRAM

```powershell
# unload models when idle (optional)
curl http://127.0.0.1:11434/api/generate -d "{\"model\":\"llama3.1:8b\",\"keep_alive\":0}"
```
