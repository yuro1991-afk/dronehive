# DroneHive Future Seer

**Top-tier multi-model speculative text future-seer.**

Reads as you type, prepares answers/tasks, keeps lanes hot, taps Jane Super Cell helpers + Everest.

## What it does

| Capability | How |
|------------|-----|
| Typeahead read | Partial buffer → debounced speculate |
| Multi-model | Intent (3b) → plan (smarts/3b) → draft (8b/coder) |
| Future draft | Speculative answer before Enter |
| Hot lanes | Warm tiny + coder; keep_alive; Jane `--lanes` |
| Ever tap | Everest health probe (optional) |
| Helper tap | muscle_dispatch probe/draft/forge/seal |
| Commit | preview · drone handoff · helper summon |

## Safety (12GB)

- Intent always on **tiny** model (hot)
- **One** full draft model at a time
- Spec drafts are **PREVIEW** until commit
- `false_green: 0`

## Launch

```bat
START_SEER.bat
```

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $py -m drone seer hot
& $py -m drone seer type "how do I fix python import"
& $py -m drone seer commit "how do I fix python import error" --mode preview
& $py -m drone seer commit "build a seal note" --mode handoff_fast
& $py -m drone seer helper --expert probe "research handoff wire"
& $py -m drone seer tui
& $py -m drone seer seal
```

## TUI

```
you> type your question
  ⚡ SEERintent=… conf=… draft=…
Enter = commit preview
/hot  /status  /spec  /type <text>  /commit-fast  /helper-probe  /quit
```

## HTTP

```
GET  /api/v1/seer
POST /api/v1/seer/hot
POST /api/v1/seer/type   {"text":"partial…"}
POST /api/v1/seer/commit {"text":"full…","mode":"handoff_fast"}
```

## Evidence

| Path | Meaning |
|------|---------|
| `out/FUTURE_SEER_HOT.json` | Hot lanes + jane/ever |
| `out/FUTURE_SEER_LAST_SPEC.json` | Last speculation |
| `out/FUTURE_SEER_SEAL.json` | Seal |
| `data/seer/drafts/` | Spec packets |
