# Synaptic Agent Loop

Functional multi-agent loop with Hebbian weights and FACE-only user speech.

```
SENSE → FIRE (synapses) → INTEGRATE (FACE) → WEIGHT → GATE → …
```

## Principal

- **Workers** = synapses (internal briefs only)
- **FACE** = only cell that talks to the user
- Weights strengthen synapses that help FACE (Hebbian)

## CLI

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

& $py -m drone synapse status
& $py -m drone synapse tick "explain imports briefly"
& $py -m drone synapse run "fix a python import error" --ticks 3
& $py -m drone synapse reset
& $py -m drone synapse seal
```

## Evidence

| Path | Meaning |
|------|---------|
| `data/synapse/WEIGHTS.json` | Live synaptic weights |
| `data/synapse/TRACE.jsonl` | Tick/run journal |
| `data/synapse/ticks/*.json` | Full tick packets |
| `out/SYNAPTIC_LAST_TICK.json` | Last tick |
| `out/SYNAPTIC_LAST_RUN.json` | Last multi-tick run |
| `out/SYNAPTIC_LOOP_SEAL.json` | Seal |

## Gate stop

Stops early when FACE confidence is high and NEXT is DONE / empty, or at max ticks.
