# DroneHive 2.1.0

Installable modular AI worker-drone swarm for Windows (Python 3.10+).

| Surface | Command | Role |
|---------|---------|------|
| **TUI (primary)** | `START_TUI.bat` or `dronehive-tui` | Lean Rust console — input + progress |
| **Python CLI** | `dronehive` / `python -m drone …` | Health, pro agent, hive, fabric, seer, mesh, handoff |
| **Super LLMs** | `START_SUPER_LLMS.bat` / `python -m drone super-llms` | All safe full models on 12GB |
| **Future Seer** | `START_SEER.bat` / `python -m drone seer` | Multi-model typeahead |
| **Tk Pro UI** | `START_PRO.bat` | Optional; not primary |

**No admin / no UAC required.**

**Cloud default (Build-with-AI):** SpaceXAI via `XAI_API_KEY` → `https://api.x.ai/v1` · model `grok-4.5`. Local muscle: Ollama on RTX 3060.

## Quick start

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b   # or your clone path
.\INSTALL.bat                                  # pip install -e ".[desktop]"
.\START_TUI.bat                                # same console TUI
```

Desktop shortcut: **DroneHive** → opens TUI (normal user, no elevation).

### TUI keys

**Enter** run Pro agent · **Esc** quit  

### CLI

```powershell
python -m drone app health
python -m drone app pro --goal "write proof.txt" --rounds 6
python -m drone app pro --goal "swarm package job" --hive
python -m drone hive --goal "unit A" --workers 2
```

### Naming (no collision)

| Name | What it is |
|------|------------|
| `dronehive` (pip) | Python CLI (`drone.app.cli`) |
| `dronehive-tui` | Rust command console |
| `dronehive-desktop-pro` | Optional Tk UI |

## Editions

| Edition | Capability |
|---------|------------|
| Classic | Hive + fabric + role-mapped tools + Ollama brain → swarm |
| **Pro v2** | Free-form Ollama tool agent + real workspace files |

Docs: [docs/PRO.md](docs/PRO.md) · [docs/WORK_ORDER.md](docs/WORK_ORDER.md) · [apps/dronehive-tui/README.md](apps/dronehive-tui/README.md)

## Install (pip)

```powershell
python -m pip install -e ".[desktop]"
dronehive health
```

## Honesty

```
false_green: 0
not 24 full LLMs
tools: sandboxed + allowlisted shell
GREEN only with on-disk evidence
```

## License

MIT · https://github.com/yuro1991-afk/dronehive
