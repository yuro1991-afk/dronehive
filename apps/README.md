# Apps (Rust)

| App | Path | Role |
|-----|------|------|
| **dronehive-tui** | `apps/dronehive-tui` | Lean Pro TUI → `drone app pro` |
| **drone-ollama-tui** | `apps/drone-ollama-tui` | Lean MAIN DELEGATE TUI → `drone app brain` (same UX) |
| **drone-ollama-app** | `apps/drone-ollama-app` | Installed Workbench (egui) + Ollama wire |
| **drone-ollama-mount** | `apps/drone-ollama-mount` | Mount engine + TUI |

## Lean TUIs (preferred)

```bat
START_TUI.bat
START_TUI_OLLAMA.bat
```

Install workbench:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File apps\drone-ollama-app\installer\Install-DroneOllamaApp.ps1
```

Build requires Rust at `G:\AI-Home\tools\cargo` on BOSS host.
