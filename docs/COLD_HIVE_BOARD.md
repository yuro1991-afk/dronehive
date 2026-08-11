# Cold Hive Board — latency + shared think

## Problem we fixed

| Bottleneck | Old behavior | New |
|------------|--------------|-----|
| Knowledge | Each drone imprinted full/near-full lesson **bodies** | **Cold refs** only (id, title, path, head) |
| Tools | Full toolkit catalog on every unit | **Task-exact tool pack** |
| Sharing | Re-read disk / re-FTS per unit | **One cold pack per goal-class per wave** |
| Hive think | Isolated unit logs | **Live chunks** on shared board |

## Architecture

```
commander plan
    │
    ▼
HiveBoard.open_wave(goals)
    │  cold packs (refs) once per classify hash
    │  publish commander_plan + goal_tool_pack chunks
    ▼
per unit: assign_unit(goal)
    │  tools_for_goal → pack_id write|code|diag|knowledge|ops|default
    │  lesson_refs only (no bodies)
    ▼
fabric L||R with scoped DroneToolkit(board_wave_id=…)
    │  knowledge_chunk → load body ONCE → publish lesson_body chunk
    │  board_publish → live progress
    ▼
seal_wave → out/HIVE_BOARD_LAST.json
```

## Tool packs

| Pack | When (goal hints) | Tools (count) |
|------|-------------------|---------------|
| write | note, report, proof, document | ~11 |
| code | python, smoke, build, .py | ~13 |
| diag | health check, nvidia, systems test | ~10 |
| knowledge | lesson, school, codex | ~10 |
| ops | library, commission | ~11 |
| default | fallback | ~12 |

## Paths

| Item | Path |
|------|------|
| Board root | `data/hive/board/<wave_id>/` |
| Index | `INDEX.json` |
| Chunks | `chunks/*.json` + `chunks.jsonl` |
| Last seal | `out/HIVE_BOARD_LAST.json` |
| Module | `drone/hive_board.py` |

## Keys (tools)

- `board_publish` / `board_get` / `board_list`
- `knowledge_chunk` — pull one lesson body onto the board
- `knowledge_imprint` — cold refs (or board ensure_cold_pack)

## Packet recycle + fast lane (latency)

| Pack | Default lane | Nodes |
|------|--------------|------:|
| write / code / default | **fast** | 5 |
| diag / knowledge / ops | **full** | 24 |

After GREEN, deposit slim packet under `data/hive/packet_recycle/`:

- tool pack list
- cold lesson refs (paths only)
- preferred lane
- **no** full bodies

Next same pack/goal-class **claims** packet → skip knowledge rebuild + keep scoped tools.

Module: `drone/packet_recycle.py`

## Honesty

- Not N full LLMs
- Not full curriculum in every imprint
- Packet recycle reuses tools/refs only — work still re-executes
- false_green: 0 — paths and chunk files are evidence
