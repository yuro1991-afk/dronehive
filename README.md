# DroneHive

**Installable modular AI worker-drone swarm** for Windows (and Python 3.10+).

Two swarm systems share one work-order imprint law:

| System | What it is |
|--------|------------|
| **Buzzer Hive** | Parallel clean-slate buzzers — task → execute → registry → recycle → fresh unit |
| **Fabric Swarm** | Multi-goal fan-out of 24 controllable drones (L \|\| R hemispheres) |

**Honesty (read this):** these are **not** 24 full independent LLMs and **not** a human-brain simulation. Controllable workers + optional shared Ollama brain + tools. `false_green: 0` — status is GREEN only with on-disk evidence.

---

## Install (pick one)

### A) From source (recommended for development)

```powershell
git clone https://github.com/yuro1/dronehive.git
cd dronehive

# Python 3.10+ on PATH (or use full path to python.exe)
python -m pip install -U pip
python -m pip install -e ".[desktop]"

# Verify
python -m drone app health
# Console script (after Scripts is on PATH):
#   dronehive health
```

Windows one-click (from repo root):

```bat
INSTALL.bat
INSTALL_DESKTOP.bat
```

### B) Console scripts after install

Scripts land in Python’s `Scripts` folder (add to PATH if needed):

```text
%LOCALAPPDATA%\Programs\Python\Python312\Scripts\
```

```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
dronehive health
dronehive desktop
dronehive task --goal "write a tiny artifact" --lane fast
dronehive commission
```

### C) Native desktop + Start Menu

```bat
INSTALL_DESKTOP.bat
```

Creates:

- Start Menu → **DroneHive**
- Desktop shortcut **DroneHive**
- Install dir: `%LOCALAPPDATA%\Programs\DroneHive\`
- Optional frozen **`DroneHive.exe`** under `dist\DroneHive\` (built by the installer script)

Primary UI is a **native CustomTkinter window** — not a browser.

### D) Portable `.exe` (no pip required for end users)

```powershell
# Developer builds the freeze:
powershell -File scripts\build_desktop_exe.ps1
powershell -File scripts\package_release.ps1
```

Ship `release\DroneHive-*-win64.zip` from GitHub Releases.  
Users unzip and run `DroneHive.exe` (or `DroneHive.cmd`).  
Data lives under `%LOCALAPPDATA%\DroneHive\workspace` unless `DRONE_HIVE_ROOT` is set.

---

## Quick start

```powershell
# Health
python -m drone app health

# Native desktop GUI
python -m drone app desktop
# or: START_APP.bat

# Commission seal (full product check)
python -m drone app commission
# or: COMMISSION.bat

# Fabric task (local tools, no LLM)
python -m drone run --goal "write workspace artifacts" --controller local --lm-assist none

# Buzzer hive (parallel clean-slate units)
python -m drone hive --goal "unit A" --goal "unit B" --workers 2 --controller local

# Fabric multi-goal swarm
python -m drone fabric-swarm --goal "path A" --goal "path B" --workers 2

# Work order imprint status
python -m drone work-order-show

# Optional HTTP API (universal links) — secondary, not the main UI
python -m drone app serve
# http://127.0.0.1:8765
```

Double-click helpers in repo root: `RUN_SMOKE.bat` · `RUN_HIVE.bat` · `RUN_SWARM.bat` · `RUN_FAST.bat` · `START_APP.bat`

---

## What you get

| Piece | Role |
|-------|------|
| `drone` package | Fabric, hive, swarm, tools, work order, Ollama brain |
| `dronehive` CLI | Health, task, links, commission, desktop, serve |
| `DroneHive.exe` | Frozen desktop (PyInstaller) |
| Work order imprint | AI laws + task + Codex how-to → registry → memory recycle → NEXT |
| Universal links | REST · OpenAI-compat · file inbox · library · continuous · Ollama |

Docs: [`docs/APP.md`](docs/APP.md) · [`docs/WORK_ORDER.md`](docs/WORK_ORDER.md) · [`docs/HONESTY.md`](docs/HONESTY.md) · [`docs/INSTALL.md`](docs/INSTALL.md)

---

## Requirements

| Item | Notes |
|------|--------|
| OS | Windows 10/11 recommended (desktop installer is Windows-native) |
| Python | 3.10+ (3.12 tested) |
| Optional Ollama | `http://127.0.0.1:11434` for LM assist |
| Desktop GUI | `pip install customtkinter` / `.[desktop]` |

**No cloud API keys required** for local/controller=`local` tasks.

---

## Project layout

```text
dronehive/
  drone/                 # Python package
    app/                 # CLI, desktop GUI, API, commission, seed configs
    hive.py / swarm.py   # two swarm systems
    work_order.py        # imprint law
  configs/               # app + fabric + hive JSON
  docs/                  # product docs
  scripts/               # install, freeze, release
  dist/DroneHive/        # frozen app (after build)
  INSTALL.bat            # pip editable install
  INSTALL_DESKTOP.bat    # Start Menu + optional .exe
  LICENSE                # MIT
  pyproject.toml         # installable package metadata
```

Runtime data (gitignored): `data/`, `out/`.  
Installed portable workspace: `%LOCALAPPDATA%\DroneHive\workspace`.

---

## Environment

| Variable | Purpose |
|----------|---------|
| `DRONE_HIVE_ROOT` | Force workspace root (configs + data) |
| `DRONE_HIVE_HOST` / `PORT` | HTTP API bind |
| `DRONE_HIVE_LANE` | Default `fast` or `full` |
| `DRONE_HIVE_WEBHOOK` | Optional outbound webhook |
| `DRONE_PYTHON` | Python used by drone helpers |

See [`.env.example`](.env.example).

---

## GitHub listing checklist

- [x] `pyproject.toml` + entry points (`dronehive`, `dronehive-desktop`)
- [x] `LICENSE` (MIT)
- [x] `README.md` (this file)
- [x] `.gitignore`
- [x] `requirements.txt` / `requirements-desktop.txt`
- [x] `MANIFEST.in` + seed package data
- [x] Desktop installer + optional `DroneHive.exe`
- [x] Commission / health gates with evidence

Suggested repo name: **`dronehive`**.  
Create GitHub repo → push → optional Release attach `release\DroneHive-*-win64.zip`.

```powershell
# first publish (example)
git init
git add .
git commit -m "DroneHive 1.0.0 — installable modular swarm app"
gh repo create dronehive --public --source=. --remote=origin --push
```

---

## Seal rule

```text
false_green: 0
evidence: out/COMMISSION_SEAL.json + health ok + real task artifacts
status: GREEN only with those artifacts
```

Not claimed: free AGI, full finetune every job, or N full GPU models per swarm unit.
