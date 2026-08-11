# Core Principal — Multi-Model Scale Law

**Principal (non-negotiable):**

```
User
  ↕  ONLY
FACE (one speaker)
  ↑ internal briefs only
Workers / scouts / critics / embeds / cloud (clones)
```

## Scale by principal

| Scale | Role | VRAM class | Talks to user? |
|-------|------|------------|----------------|
| **P0 FACE** | Sole speaker | A (7–8B) | **YES — only** |
| **P1 Scout** | Intent / lightning | S (3B) | NO |
| **P2 Worker** | Code / ops / ontology | A (7B) | NO |
| **P3 Critic** | Seal / review | A (7B) | NO |
| **P4 Tight** | Heavy reason (if smoke OK) | B (12B) | NO |
| **P5 Embed** | Vectors only | E | NO |
| **P6 Cloud** | Opt-in remote | C | NO (brief FACE only) |

## Ten source models → ten app clones

Each clone is an Ollama model `dh-<role>` FROM the base, with SYSTEM locked to principal.

| # | Source | Clone tag | Principal scale | Role |
|---|--------|-----------|-----------------|------|
| 1 | llama3.1:8b | `dh-face` | P0 | FACE speaker |
| 2 | llama3.2:3b | `dh-scout` | P1 | Intent scout |
| 3 | qwen2.5:3b | `dh-flash` | P1 | Fast alt scout |
| 4 | ai-smarts:latest | `dh-smarts` | P1/P2 | Ontology worker |
| 5 | qwen2.5-coder:7b | `dh-coder` | P2 | Code worker |
| 6 | qwen2.5:7b | `dh-ops` | P2 | Ops worker |
| 7 | mistral:7b | `dh-critic` | P3 | Critic / seal |
| 8 | gemma4:12b | `dh-tight` | P4 | Tight reason (if live) |
| 9 | nomic-embed-text | `dh-embed` | P5 | Embed only |
| 10 | minimax-m3:cloud | `dh-cloud` | P6 | Cloud opt-in |

## Runtime law

1. User messages hit **dh-face** only (or face layer using dh-face).
2. Workers emit INTERNAL briefs; never final user prose.
3. Load budget 12GB: 1× P0/P2/P3 heavy + optional P1 warm.
4. `false_green: 0` — clone must answer smoke or marked RED.

## Install

```powershell
python -m drone clones install
python -m drone clones list
python -m drone clones smoke
```
