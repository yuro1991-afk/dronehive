# Real code + dual Ollama

## Law

1. **execute** must produce a **goal-named `.py` module** (not only `_drone_snip.py`).
2. Module must **compile** and **smoke-run** (`exit 0`). Prefer printing `SMOKE_OK`.
3. **seal** is **RED** if diagnostics fail **or** no real `.py` **or** verify fails.
4. `false_green: 0`

## Dual Ollama (one server)

| Role | Model (default) | Job |
|------|-----------------|-----|
| Commander / general LM | top resolved (env `DRONE_OLLAMA_MODEL`) | plans, critic notes |
| **Code worker** | **`llama3.1:8b`** (env `DRONE_CODE_MODEL`) | generate real module source |

Same `127.0.0.1:11434`, sequential lock (no dual VRAM thrash).

## Smoke

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
python -m drone brain
python -m drone run --goal "write a python function add(a,b) that returns a+b and smoke it" --controller ollama --lm-assist ollama
```
