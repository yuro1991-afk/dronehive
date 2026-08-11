# AI Protocol — strip human weights from AI-only models

## Law

| Channel | Talks to | Human weights |
|---------|----------|---------------|
| **FACE** | User | Allowed (clear speech) |
| **AI2AI** | FACE / other AI | **Stripped** |

AI2AI models must not use assistant persona, greetings, apologies, or “happy to help” fluff.

## Prefix

```
CHANNEL=AI2AI. TARGET=FACE_OR_UPSTREAM_AI. NO_USER_ADDRESS.
NO_GREETING. NO_APOLOGY. NO_PERSONA. NO_FLUFF.
OUTPUT=DENSE_TECHNICAL. Prefer JSON or bullets. false_green:0.
```

## Applied to

- Multi-face workers (Ollama roles, Hermes/OpenClaw/OpenCode briefs)
- Future Seer intent/plan/draft (pre-FACE)
- Agent loop planner (tool JSON only)
- Observer, brain delegate, ollama_brain code worker
- `dh-*` clone Modelfiles (`talks_to_user=false`)

## Files

- `configs/ai_protocol.json`
- `drone/ai_protocol.py`
- `models/clones/Modelfile.dh-*`

## Rebuild Ollama clones with stripped systems

```powershell
python -m drone clones modelfiles
python -m drone clones install
python -m drone clones wire
```
