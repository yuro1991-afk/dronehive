# DroneHive Pro (v2.0.0)

**Tier:** premium / “richer” capability  
**v1 preserved:** classic hive + role-mapped tools still ship.

## What Pro adds

| v1 (classic) | Pro v2 |
|--------------|--------|
| Role → tool dispatch (scripted) | **Free-form tool choice** — Ollama picks tools + args each round |
| Brain plans hive/task JSON | **Pro agent loop** writes real workspace files first |
| Shared toolkit | Same sandbox toolkit + multi-round autonomy |
| Classic desktop | **Gold Pro desktop** + realtime swarm |

## Pipeline

```text
You → Pro command
   → Ollama chooses tools (write_text, run_python, …)
   → Host executes tools (sandbox)
   → Optional hive swarm follow-up
   → Seal: out/PRO_*.json + PRO_LAST.json + PRO_RUN_LAST.json
```

## Run (no UAC)

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b

# Primary: lean Rust TUI
.\START_TUI.bat
# or: dronehive-tui   (if ~/bin on PATH)
# Desktop shortcut: "DroneHive" (normal user)

# Free-form agent CLI
python -m drone app pro --goal "build a premium proof package with a small test"
python -m drone app pro --goal "swarm packaging job" --hive

# Optional Tk UI (not primary)
.\START_PRO.bat
```

Naming: `dronehive` = Python CLI · `dronehive-tui` = Rust console · no admin.

## Honesty

```
false_green: 0
free_form_tools: yes
sandbox: yes
shell: allowlisted
not 24 full LLMs
GREEN only with evidence paths
```

If Ollama fails mid-loop, Pro still writes a **heuristic deliverable** so the run is not empty theater — status may be PARTIAL/GREEN with evidence.

## Files

| Path | Role |
|------|------|
| `drone/pro/tool_agent.py` | Free-form tool loop |
| `drone/pro/service.py` | Pro service |
| `drone/pro/desktop.py` | Pro GUI |
| `configs/app_pro.json` | Pro config |
| `START_PRO.bat` | Windows launcher |
