# Apps (Rust)

| App | Path | Role |
|-----|------|------|
| **drone-ollama-app** | `apps/drone-ollama-app` | Installed Workbench (egui) + Ollama wire |
| **drone-ollama-mount** | `apps/drone-ollama-mount` | Mount engine + TUI |

Install workbench:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File apps\drone-ollama-app\installer\Install-DroneOllamaApp.ps1
```

Build requires Rust at `G:\AI-Home\tools\cargo` on BOSS host.
