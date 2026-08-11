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
| **Library** | `F:\GrokSelfLibrary` via LibraryBridge |
| **Continuous** | `OPEN_TASKS.json` pull |
| **Ollama** | Shared top model `127.0.0.1:11434` |
| **Webhook out** | `DRONE_HIVE_WEBHOOK` env |
| **In-process** | Import `DroneHiveService` |

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
