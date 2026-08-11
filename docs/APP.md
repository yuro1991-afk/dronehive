# DroneHive — Standalone Modular App

**Product:** fully commissioned top-tier standalone modular swarm app with universal links.

**Honesty:** controllable workers + shared Ollama brain + tools. Not 24 independent LLMs.

## Modules

| Module | Path |
|--------|------|
| Fabric (24-node) | `drone.chain.BrainFabric` |
| Hive swarm | `drone.hive.BuzzerHive` |
| Fast lane (5-node) | `drone.fast_lane.FastLane` |
| Clean slate v2 | `drone.clean_slate` |
| Tools | `drone.tools.DroneToolkit` |
| Brain | `drone.ollama_brain` |
| Service facade | `drone.app.service.DroneHiveService` |
| HTTP API | `drone.app.api` |
| Universal links | `drone.app.links.LinkRegistry` |
| Commission | `drone.app.commission` |

## Universal links

| Link | How to attach |
|------|----------------|
| **REST** | `http://127.0.0.1:8765/api/v1/task` |
| **OpenAI-compat** | `POST /v1/chat/completions` |
| **File inbox** | Drop `{"goal":"…","lane":"fast"}` into `data/app/inbox/` |
| **Grok handoff** | `python -m drone handoff lanes\|to\|collect\|e2e` · cowork packets in `data/app/cowork/` |
| **Super LLMs** | `python -m drone super-llms` / `START_SUPER_LLMS.bat` — all safe full models one app |
| **Future Seer** | `python -m drone seer` / `START_SEER.bat` — typeahead multi-model speculate + Jane taps |
| **Multi FACE** | `python -m drone face` — Hermes/OpenClaw/OpenCode/Ollama/Drones → one speaker only |
| **Library** | `F:\GrokSelfLibrary` via LibraryBridge |
| **Continuous** | `OPEN_TASKS.json` pull |
| **Ollama** | Shared top model `127.0.0.1:11434` (default local muscle) |
| **SpaceXAI (cloud)** | Default cloud provider — `XAI_API_KEY` → `https://api.x.ai/v1` · model `grok-4.5` (`DRONE_XAI_MODEL`) · controller kinds `spacexai`/`xai`/`grok` |
| **Webhook out** | `DRONE_HIVE_WEBHOOK` env |
| **In-process** | Import `DroneHiveService` / `GrokDroneHandoff` |

### Build-with-AI (SpaceXAI)

Cloud LLM features default to **SpaceXAI** (served by xAI). Do not hardcode keys; use `XAI_API_KEY` from the environment or a git-ignored `.env`. See `.env.example` and live model list: https://docs.x.ai/developers/models

```powershell
$env:XAI_API_KEY = "xai-..."   # from https://console.x.ai — never commit
$env:DRONE_XAI_MODEL = "grok-4.5"
python -m drone app pro --goal "one line status" --lm-assist spacexai   # if wired
# or controller assist via hive/controllers when kind=spacexai|xai|grok
```

Local Ollama remains the default for swarm/muscle on this host (12GB RTX 3060). SpaceXAI is opt-in cloud when the key is set.

See also: `docs/GROK_HANDOFF.md` · `docs/SUPER_LLMS.md`

## Launch (Windows BOSS) — native desktop, not browser

```bat
INSTALL_DESKTOP.bat
START_APP.bat
COMMISSION.bat
```

Primary UI is a **native CustomTkinter window** (`python -m drone app desktop`).  
HTTP `app serve` is optional glue for universal links only — not the main app.

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $py -m drone app desktop          # native window
& $py -m drone app commission
# optional API only:
& $py -m drone app serve
```

## Commission gate

`out/COMMISSION_SEAL.json` is GREEN only when modules, health, links, fast task, file inbox, and HTTP probe all pass with evidence.
