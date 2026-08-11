# Real Agent Loop

**Plan → execute REAL tools → observe → replan → FACE summary.**

Not chat theater. GREEN only when tools succeed and artifacts exist.

```
PLAN (LLM JSON) → ACT (DroneToolkit) → OBSERVE → GATE → … → FACE (user only)
```

## CLI

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

& $py -m drone agent seal
& $py -m drone agent run "Write hello.txt with HELLO and package_manifest" --rounds 5
& $py -m drone agent status
```

## Tools (real)

`write_text` · `read_text` · `list_dir` · `write_json` · `run_python` · `run_shell` · `package_manifest` · …

## Evidence

| Path | Meaning |
|------|---------|
| `data/workspace/agentloop_*/` | Live workspace |
| `out/artifacts/agentloop_*/` | Manifest / copies |
| `out/AGENT_LOOP_LAST.json` | Last run |
| `out/AGENT_LOOP_SEAL.json` | Seal |

## vs synapse

| | synapse | **agent** |
|--|---------|-----------|
| Workers | LLM briefs | **Real tools** |
| Proof | conf score | **files on disk** |
| FACE | yes | yes (summary only) |
