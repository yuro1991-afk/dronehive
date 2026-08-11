## DroneHive 2.1.0

Ship unfinished post-2.0.0 surface: Super Kernel, Super LLMs, Future Seer, Super Mesh, Bridge-1080, agent/synapse loops, Grok handoff, multi-face, SpaceXAI cloud default.

### Build-with-AI (SpaceXAI)

- Cloud default: **SpaceXAI** via `XAI_API_KEY` → `https://api.x.ai/v1`
- Default model: `grok-4.5` (`DRONE_XAI_MODEL`)
- Local muscle remains Ollama on RTX 3060
- See `.env.example` and `docs/APP.md`

### Surfaces

| CLI | Launcher |
|-----|----------|
| `python -m drone super-llms` | `START_SUPER_LLMS.bat` |
| `python -m drone seer` | `START_SEER.bat` |
| `python -m drone mesh` | `START_SUPER_MESH.bat` |
| `python -m drone bridge-1080` | `START_BRIDGE_1080.bat` |
| `python -m drone agent` / `synapse` / `face` | — |
| `python -m drone handoff` | — |
| TUI | `START_TUI.bat` / `dronehive.ps1` |

### Naming

| Name | Role |
|------|------|
| `dronehive` | Python CLI |
| `dronehive-tui` | Rust console |
| `drone-ollama-tui` | Ollama MAIN DELEGATE TUI |

### Desktop

- **DroneHive** → `START_TUI.bat` (normal user, empty Args)
- No DroneHive admin/UAC path

`false_green: 0`
