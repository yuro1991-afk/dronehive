# dronehive-tui

Lean **Rust TUI** command console for DroneHive Pro (Grok Build–style strip).

## Why

CustomTkinter desktop crashed / was heavy. This console:

- input bar only (primary)
- tiny progress gauge
- one status line
- **no** swarm canvas / log cockpit
- work runs in a background thread via `python -m drone app pro`

## Build

```bat
set PATH=G:\AI-Home\tools\cargo\bin;%PATH%
cd apps\dronehive-tui
cargo build --release
```

## Run

```bat
START_TUI.bat
```

or:

```bat
apps\dronehive-tui\target\release\dronehive-tui.exe --root G:\AI-Home\projects\ai-worker-drone-0.5b
```

Keys: **Enter** run · **Esc** / **Ctrl+C** quit · type task while idle.

## Lean profile

`opt-level = "z"`, LTO, strip — small release binary.
