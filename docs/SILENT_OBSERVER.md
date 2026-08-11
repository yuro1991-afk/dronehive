# Silent Observer LLM

## Role

Second LLM **inside the loop** — **never** answers user MAIN/chat.

| Does | Does not |
|------|----------|
| Receives drone work packs after every fabric/swarm run | Reply to user typing |
| Reads OS snapshot (GPU, disk, Ollama) | Appear in chat as assistant |
| Writes `data/observer/LATEST.json` + `informed.jsonl` | Handle DELEGATE input |

## Wire

```
user MAIN → commander Ollama → drones
                              ↓
                    silent observer informed
                    (drones pack + OS)
                              ↓
                    data/observer/ only
```

## Enable / model

```powershell
# default ON
$env:DRONE_OBSERVER = "1"
$env:DRONE_OBSERVER_MODEL = "llama3.2:3b"   # light under leash; or llama3.1:8b
```

Disable: `$env:DRONE_OBSERVER = "0"`

## CLI

```powershell
python -m drone observer
```
