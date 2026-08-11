# Drone Ollama Workbench

Top-tier **installed** Windows app: **Ollama wired** + **24 worker drones** + mount/swarm/go-live.

## Install (BOSS)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\AI-Home\projects\drone-ollama-app\installer\Install-DroneOllamaApp.ps1
```

Creates:

- `G:\AI-Home\apps\DroneOllama\`
- Desktop **Drone Ollama** shortcut
- Start Menu **Drone Ollama → Drone Ollama Workbench**
- HKCU uninstall entry

## Features

| Tab | Action |
|-----|--------|
| Drones | Run 24-node fabric (tools + optional Ollama LM) |
| Swarm | Multi-goal swarm + **GO LIVE** |
| Ollama Chat | Direct chat to local models |
| System | Info/stats, honesty, open seals |

On launch: **Ensure-OllamaCritical** (or keep script) then native egui UI.

## Honesty

- Controllable drones — **not** 24 full LLMs
- **One** shared Ollama brain when LM enabled
- Not a human brain simulation
