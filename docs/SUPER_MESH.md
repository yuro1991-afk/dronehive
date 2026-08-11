# Super Mesh Live AI

**User ↔ `fx-face` only.** Twenty `m2m-*` 0.5B clones are mesh synapses. `fx-reason` conducts. **false_green: 0.**

## Topology

```
                         [fx-face]     ← only user voice (flagship)
                              ↑
                        [fx-reason]    ← mesh conductor (flagship)
                              ↑
                       [m2m-callosum]
                      ↙             ↘
              LEFT (10)              RIGHT (10)
         probe→…→left→seal      flash→…→bus→callosum
                      ↘  cross edges  ↙
```

- **20 m2m** = shared `qwen2.5:0.5b` body, role via SYSTEM (strict M2M)
- **fx-reason** = `qwen2.5:3b` frontier-style conductor
- **fx-face** = `llama3.1:8b` user pole
- **Edges** = hardwired adjacency + Hebbian weights on disk

## Live loop

`SENSE → ROUTE → FIRE_MESH → HEMISPHERE_MERGE → CONDUCT → FACE → WEIGHT → GATE`

## Commands (Windows / BOSS)

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$env:OLLAMA_EXE = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

& $py -m drone mesh install-flagships   # create fx-reason + fx-face
& $py -m drone mesh hardwire            # bind 20+2 into apps
& $py -m drone mesh status
& $py -m drone mesh tick "status of local mesh brain"
& $py -m drone mesh run "plan a small tool test" --ticks 2
& $py -m drone mesh seal
```

## Evidence paths

| File | Role |
|------|------|
| `data/super_mesh/HARDWIRE.json` | deep bind receipt |
| `data/super_mesh/NODE_WEIGHTS.json` | Hebbian node weights |
| `data/super_mesh/EDGE_WEIGHTS.json` | edge weights |
| `out/SUPER_MESH_LAST_TICK.json` | last live tick |
| `out/SUPER_MESH_LAST_RUN.json` | multi-tick run |
| `out/SUPER_MESH_SEAL.json` | seal |

## Honesty

Not a biological brain. Not 22 independent full LLM VRAM copies. Shared 0.5B body for m2m; two stronger flagship poles; real Ollama chat fires; weights on disk.
