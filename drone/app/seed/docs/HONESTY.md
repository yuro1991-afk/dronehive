# Honesty

| Claim | Truth |
|-------|--------|
| Controllable drones | **Yes** — role nodes + protocol |
| Full model per drone | **No** |
| Dual hemispheres | **Yes** — L/R chains + callosum |
| Learn from builds | **Yes** — ledger, skills, XP, smart_index |
| Smarter the more they build | **Yes** — measurable stats rise on disk |
| Human brain sim | **No** |
| Parent AI control only | **Yes** — allowed controller kinds |
| **Buzzer hive** | **Yes** — clean-slate ephemeral workers |
| **Parallel swarm** | **Yes** — ThreadPoolExecutor, peak_parallel tracked |
| Full LLM per buzzer | **No** — shared Ollama top model only |
| Tools on all roles | **Yes** — `DroneToolkit` sandbox (write/read/shell/python/package) |
| Top Ollama model | **Yes** — prefer `gemma4:12b` if installed (12GB fit) |
| Library connected | **Yes** — `F:\GrokSelfLibrary` + `OPEN_TASKS.json` |
| Fresh agent after each task | **Yes** — buzzer dies; free slot respawns next |
| **Clean Slate Agents v2** | **Yes** — passport + wipe audit + generation ledger + brain/tools bind |
| **Fast lane** | **Yes** — 5 nodes + lite tools/seal; **not** full 24-node production seal |
| Dense Vector DB (FAISS) | **No** — token Jaccard JSONL store |
| Post-live F: HOT rewrite | **Only if** `--post-live-write` and tool exits 0 |

## Lifecycle (law) — SWARM

```
library/queue → fan-out up to W fresh buzzers IN PARALLEL
             → each runs dual-hemisphere fabric
             → write hive memory + outbox note (locks)
             → discard buzzer (clean slate)
             → free slot → immediately spawn next fresh buzzer
```


## Seal rule

```
false_green: 0
evidence: out\HIVE_SWARM_SEAL.json + data\hive\ + data\skills\stats.json
status: GREEN only with those artifacts
```
