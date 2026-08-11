# Stable Bridge · GTX 1080 Ti

## Law

| Rule | Meaning |
|------|---------|
| **3060 MAIN** | Display + default Ollama `:11434` — never auto-hijacked |
| **1080 SPARE** | Opt-in only |
| **false_green: 0** | RED until 1080 is **LIVE in `nvidia-smi`** |
| Fail closed | No arm / no second lane while PnP Error |

## Current host truth (check often)

```powershell
python -m drone bridge-1080 status
nvidia-smi -L
Get-PnpDevice -FriendlyName '*1080*' | Format-List Status, Problem, ProblemDescription
```

Typical BOSS states:

| State | Meaning |
|-------|---------|
| `LIVE_DUAL` | Both cards in nvidia-smi — bridge can **arm** |
| `PNP_DISABLED_CODE22` | Device present but **disabled** (Code 22) |
| `PNP_DRIVER_FAIL_CODE31` | Driver failed — need dual-capable driver |
| `MAIN_ONLY_NO_PNP_1080` | No 1080 node |

## LIVE daemon · NO KILL SWITCH

```powershell
python -m drone bridge-1080 start      # detached LIVE + watchdog
python -m drone bridge-1080 process    # pids
python -m drone bridge-1080 kill       # REFUSED by design (exit 2)
```

| Law | Meaning |
|-----|---------|
| **No kill switch** | No authorized kill command; SIGINT/SIGTERM ignored in live loop |
| **Watchdog** | Respawns bridge if process dies |
| **Never kills 3060** | Main Ollama / MAIN GPU untouched |
| **Daemon LIVE** | Process is GREEN even if hardware still Code 22 |
| **Hardware GREEN** | Only when 1080 appears in `nvidia-smi` |

Boss can only stop via Task Manager / reboot — not via bridge CLI.

## Commands

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
python -m drone bridge-1080 start
python -m drone bridge-1080 status
python -m drone bridge-1080 enable    # Enable-PnpDevice (often needs Admin)
python -m drone bridge-1080 arm       # CUDA session map when hardware LIVE
python -m drone bridge-1080 disarm
python -m drone bridge-1080 seal
```

Or: `START_BRIDGE_1080.bat`

## Arm (when LIVE only)

Sets **this process / Apply script** CUDA map:

- `CUDA_DEVICE_ORDER=PCI_BUS_ID`
- `CUDA_VISIBLE_DEVICES=0,1` (or `0` with `--only-1080`)

Does **not** kill Ollama on 3060. Avoid `--persist-user` unless boss wants User env changed.

```powershell
. $env:USERPROFILE\.ollama\bridge-1080\Apply-Arm-Session.ps1
```

## Driver honesty

Pascal **GTX 1080 Ti** (`DEV_1B06`) must be listed by the installed NVIDIA package. Ampere-only stacks can leave the card Error/disabled. Repair notes: `%USERPROFILE%\.ollama\fix-dual-gpu-admin.ps1`.
