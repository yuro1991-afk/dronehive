# Drone ↔ Ollama Mount Engine (Rust)

Rust engine that **mounts** the 24 controllable worker drones onto an **Ollama TUI**.

## Honesty

| Claim | Truth |
|--------|--------|
| Drones are full models | **No** — controllable fabric nodes |
| Engine mounts them for Ollama control | **Yes** |
| TUI is Rust (ratatui) | **Yes** |
| Learning still in Python fabric | **Yes** — engine shells `python -m drone` |
| Human brain sim | **No** |

## Layout

```
G:\AI-Home\projects\drone-ollama-mount\     ← this engine (Rust)
G:\AI-Home\projects\ai-worker-drone-0.5b\   ← 24 drones + learn-from-build
http://127.0.0.1:11434                      ← Ollama parent controller
```

## Build (this machine)

```powershell
$env:CARGO_HOME = 'G:\AI-Home\tools\cargo'
$env:RUSTUP_HOME = 'G:\AI-Home\tools\rustup'
$env:Path = "G:\AI-Home\tools\cargo\bin;$env:Path"
cd G:\AI-Home\projects\drone-ollama-mount
cargo build --release
```

## Launch TUI

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\AI-Home\projects\drone-ollama-mount\Launch.ps1
```

Or:

```text
START.bat
```

### Keys

| Key | Action |
|-----|--------|
| `m` | Mount all 24 drones |
| `u` | Unmount |
| `r` | Refresh Ollama status |
| `s` | Fabric stats (smart_index) |
| `1` | Cycle Ollama controller model |
| Enter | Run goal (Ollama brief → drones learn) |
| `q` | Quit |

## CLI

```powershell
drone-ollama-mount status
drone-ollama-mount mount
drone-ollama-mount run --goal "build a path parser"
drone-ollama-mount smoke
drone-ollama-mount tui
```

## Related

- Existing desktop Ollama UI: `G:\AI-Home\projects\ollama-rust-ui` (egui)
- This project: **TUI + mount engine** focused on drones
