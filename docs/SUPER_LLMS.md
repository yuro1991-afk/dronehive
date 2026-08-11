# DroneHive Super LLMs

**One super app** for every **full LLM** that can safely run on this host (BOSS · RTX 3060 12GB), same product family as DroneHive drones.

## Safety law

| Rule | Why |
|------|-----|
| Max **1** active full 7B+ | 12GB VRAM |
| Max **2** loaded total | Host optimize (`MAX_LOADED=2`) |
| Optional tiny (3B) + one full | Warm map models |
| Smoke must return text | No false greens |
| Cloud tags opt-in only | No surprise network/VRAM |

## Full LLMs in the super app (roster)

| Tag | Tier | Role | Full LLM |
|-----|------|------|----------|
| `llama3.1:8b` | A | general / default | yes |
| `qwen2.5-coder:7b` | A | coding | yes |
| `qwen2.5:7b` | A | ops | yes |
| `mistral:7b` | A | seal/ops alt | yes |
| `gemma4:12b` | B | tight reason | yes (if smoke OK) |
| `llama3.2:3b` | S | fast | yes (small full) |
| `qwen2.5:3b` | S | fast alt | yes |
| `ai-smarts:latest` | S | ontology | yes |
| `nomic-embed-text` | E | embed | no (embed only) |
| `minimax-m3:cloud` | C | cloud opt-in | yes (remote) |

Unsafe examples (not auto-run): ~23B+ local (e.g. qwen3.6) on 12GB.

## Launch (Windows)

```bat
START_SUPER_LLMS.bat
```

Or:

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $py -m drone super-llms status
& $py -m drone super-llms list
& $py -m drone super-llms smoke
& $py -m drone super-llms route "fix the python bug in api.py"
& $py -m drone super-llms chat --role coding "write a pure function add(a,b)"
& $py -m drone super-llms tui
& $py -m drone super-llms seal
```

## TUI commands

```
list | status | smoke | seal | roles
route <goal>
chat [model] <prompt>
quit
```

## Evidence seals

| File | Meaning |
|------|---------|
| `out/SUPER_LLMS_ROSTER_LIVE.json` | Installed ∩ safe roster |
| `out/SUPER_LLMS_SMOKE.json` | Per-model smoke results |
| `out/SUPER_LLMS_SEAL.json` | Super app seal |
| `out/SUPER_LLMS_STATE.json` | Last active model |

## HTTP (optional)

With `python -m drone app serve`:

- `GET  /api/v1/super-llms`
- `POST /api/v1/super-llms/route` `{"goal":"…"}`
- `POST /api/v1/super-llms/chat` `{"prompt":"…","model":"…"}`

## Relation to drones

| Surface | Job |
|---------|-----|
| Super LLMs | Pick/run safe full models |
| DroneHive handoff | Workers + tools + fabric/hive |
| Ollama | Shared inference backend only |

Drones stay tools workers; Super LLMs is the multi-model brain surface.
