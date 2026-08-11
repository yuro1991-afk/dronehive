# Third LLM — Core Critic (constant sentient loop)

## Stack of three (roles)

| # | Name | Job | User chat? |
|---|------|-----|------------|
| 1 | Commander / general | MAIN → plan / notes | via app path |
| 2 | Observer | Informed of drone work + OS | **never** |
| 3 | **Core critic** | Critical thinking for **core engine** | **never** |

## Wire

```
drones finish run/swarm
        ↓
 observer (informed)
        ↓
 core critic (critical for engine)
        ↓
 data/core_critic/ENGINE_INSIGHTS.json  ← engine can read next cycle
```

## Constant loop

```powershell
python -m drone critic-loop
# or
START_CRITIC_LOOP.bat
```

Interval: `DRONE_CRITIC_INTERVAL_S` (default **90** under leash).

One-shot:

```powershell
python -m drone critic
```

## Outputs

- `data/core_critic/LATEST.json`
- `data/core_critic/ENGINE_INSIGHTS.json` — actions/flaws for core
- `data/core_critic/critique.jsonl`

## Disable

```powershell
$env:DRONE_CRITIC = "0"
```
