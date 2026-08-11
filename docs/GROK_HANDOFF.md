# Grok ↔ Drone live handoff · e2e cowork

**Product:** give Grok the ability to hand tasks to drones with **live lanes** connected for end-to-end cowork.

**Honesty:** `false_green: 0` — GREEN only with on-disk evidence.

## Topology

```
Boss (Grok)
   │  CoworkPacket {from,to,goal,mode,evidence,next,veto}
   ▼
Live lanes check  →  Ollama :11434 + drone modules (+ optional HTTP :8765)
   │
   ▼
Drones (fast | full | hive | brain | pro | inbox)
   │
   ▼
cowork inbox result packet  →  Grok collect / integrate
```

## Project root

`G:\AI-Home\projects\ai-worker-drone-0.5b`

## CLI (primary — Grok uses this)

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

# 1) Prove live lanes
& $py -m drone handoff lanes

# 2) Hand off a task (sync wait + seal)
& $py -m drone handoff to --goal "write a short worker seal note" --mode fast
& $py -m drone handoff to --goal "multi unit build" --mode hive --workers 2
& $py -m drone handoff to --goal "plan then swarm" --mode brain
& $py -m drone handoff to --goal "free form files" --mode pro --pro-rounds 6

# Async file-drop (process later)
& $py -m drone handoff to --goal "later job" --mode inbox

# 3) Pull results back to Grok
& $py -m drone handoff collect

# 4) Status
& $py -m drone handoff status
& $py -m drone handoff status --id <handoff_id>

# 5) Full e2e smoke
& $py -m drone handoff e2e
```

## Modes

| Mode | Path | When |
|------|------|------|
| `fast` | FastLane 5-node | Default grind, tools-first |
| `full` | 24-node fabric L\|\|R + callosum | Heavy seal |
| `hive` | BuzzerHive multi-unit | Parallel peer goals |
| `brain` | Ollama plan → swarm | MAIN DELEGATE path |
| `pro` | Pro free-form tool agent | Real file work |
| `inbox` | Drop JSON only | Async queue |

## Cowork dirs

| Path | Role |
|------|------|
| `data/app/cowork/outbox/` | Handoff requests from Grok |
| `data/app/cowork/inbox/` | Result packets for Grok |
| `data/app/cowork/active/` | RUNNING jobs |
| `data/app/cowork/done/` | Sealed results |
| `out/GROK_LANES_LIVE.json` | Last lane check |
| `out/GROK_HANDOFF_LAST.json` | Last handoff seal |
| `out/GROK_COWORK_SEAL.json` | Mirror seal |
| `out/GROK_COWORK_E2E_SEAL.json` | E2E smoke seal |

## Packet schema

```json
{
  "schema": "grok.drone.cowork.handoff.v1",
  "from_agent": "grok",
  "to_agent": "drones",
  "goal": "…",
  "handoff_id": "…",
  "mode": "fast",
  "status": "OPEN|RUNNING|GREEN|PARTIAL|RED",
  "evidence": ["paths…"],
  "next_action": "…",
  "veto": false,
  "false_green": 0
}
```

## HTTP (optional)

```powershell
& $py -m drone app serve
# GET  http://127.0.0.1:8765/api/v1/lanes
# POST http://127.0.0.1:8765/api/v1/handoff  {"goal":"…","mode":"fast"}
# GET  http://127.0.0.1:8765/api/v1/cowork/collect
```

CLI handoff works **without** HTTP serve. HTTP is optional glue.

## Live lanes gate

`ready_for_handoff` requires:

1. Drone modules on disk (fabric/hive/fast/tools/api)
2. Ollama reachable at `127.0.0.1:11434`

If RED:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.ollama\start-agent-lanes.ps1"
```

## Grok agent protocol

1. Load skill `drone-handoff` or rule `10-drone-handoff`
2. `python -m drone handoff lanes` — abort if not ready
3. `python -m drone handoff to --goal "…" --mode <mode>`
4. Read seal paths in JSON evidence
5. `python -m drone handoff collect` when multi-step cowork
6. Report GREEN only with evidence paths

## Super Cell vs Drones

| Surface | Use |
|---------|-----|
| Super Cell 4 (PROBE→SEAL) | Skilled supervisor phases + muscle_dispatch |
| DroneHive handoff | Controllable worker fabric / hive / fast grind |

Grok may use **both**. Do not nest Hermes/OpenClaw as fake agents for handoff.
