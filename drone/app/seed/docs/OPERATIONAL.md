# Fully operational drones

## One command

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
python -m drone go-live
```

Or double-click `START_OPERATIONAL.bat`.

## What go-live does

1. Ensure Ollama on `:11434` (host Ensure script)
2. Probe real tools (write + shell)
3. Resolve shared top Ollama model
4. Run **24-drone fabric** mission with tools (+ LM if Ollama up)
5. Run **fabric multi-goal swarm** (3 goals, L||R parallel)
6. Run **buzzer hive** mini (2 parallel clean-slate units)
7. **Rust mount** all 24 drones (`drone-ollama-mount.exe`)
8. Write `out\OPERATIONAL_SEAL.json`

## Honesty

| Claim | Truth |
|--------|--------|
| Fully operational workforce | **Yes** if seal GREEN/PARTIAL with evidence |
| Full LLM per drone | **No** |
| Shared Ollama brain | **Yes** when reachable |
| Tools on all roles | **Yes** |
| Learn from builds | **Yes** |

## Status vocabulary

- **GREEN** — all required steps ok (Ollama optional for tools path)
- **PARTIAL** — stack works but Ollama brain down
- **RED** — tools/fabric/swarm failed
