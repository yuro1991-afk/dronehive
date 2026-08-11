# drone-ollama-tui

Lean **Rust TUI** for **Drone Ollama MAIN DELEGATE** — same UX as `dronehive-tui`.

## What it is

| Piece | Value |
|-------|--------|
| UX twin | `apps/dronehive-tui` (chat + gauge + input) |
| Pipeline | user command → Ollama plan → swarm / hive / task |
| Backend | `python -m drone app brain --command "…"` |
| Stream | `CHAT\|role\|text` live · final `SEAL\|{json}` |
| Seal | `out/BRAIN_DELEGATE_LAST.json` |

Not the heavy egui workbench. No swarm canvas.

## Keys

| Key | Action |
|-----|--------|
| **Enter** | run MAIN DELEGATE |
| **Esc** / **Ctrl+C** / **Ctrl+Q** | quit |
| **↑ / ↓ / PgUp / PgDn** | scroll chat |

## Build

```bat
set PATH=G:\AI-Home\tools\cargo\bin;%PATH%
cd G:\AI-Home\projects\ai-worker-drone-0.5b\apps\drone-ollama-tui
cargo build --release
```

## Run

From project root:

```bat
START_TUI_OLLAMA.bat
```

Or installed:

```bat
G:\AI-Home\apps\DroneOllama\START_TUI.cmd
```

## Vs dronehive-tui

| | dronehive-tui | drone-ollama-tui |
|--|---------------|------------------|
| CLI | `drone app pro` | `drone app brain` |
| Role | free-form tool agent | Ollama commander → drones |
| Title | DroneHive Pro | Drone Ollama MAIN DELEGATE |

## Lean profile

`opt-level = "z"`, LTO, strip — small release binary.
