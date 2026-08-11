# Super Kernel Lane

Barebones multi-thread model host for DroneHive.

## Law

| Rule | Meaning |
|------|---------|
| Minimum | stdlib `ThreadingHTTPServer` only — no FastAPI/uvicorn stack |
| Any model | Any installed Ollama tag / any instance id |
| No model cap | `max_models` / `max_instances` = **null** |
| Only limit | **VRAM** — refuse only when free VRAM cannot hang (after headroom + optional LRU eviction) |
| Stable lane | Dedicated port, multi-thread request queue, shared Ollama backend |
| false_green | 0 |

## Start (stable process — Boss/Grok kill only)

```bat
START_SUPER_KERNEL.bat
```

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
Set-Location G:\AI-Home\projects\ai-worker-drone-0.5b
& $py -m drone super-kernel start   # detached + watchdog
```

| Law | Meaning |
|-----|---------|
| Hidden stable | Survives console close (`CREATE_NO_WINDOW` + process group) |
| Watchdog | Restarts lane if it dies **unless** authorized stop |
| **Idler** | Keep-alive heartbeats every 15s; optional rare warm touch; restarts serve if down |
| Kill | **Only Boss CLI or Grok** — `python -m drone super-kernel kill` or `POST /kill` + token |
| Token | `%USERPROFILE%\.ollama\super-kernel\kill.token` (user ACL; never printed in status) |
| Unauthorized | HTTP `/kill` without token → **403**; Task Manager kill → watchdog/idler restart |

```bat
KILL_SUPER_KERNEL.bat
```

```powershell
python -m drone super-kernel kill      # authorized
python -m drone super-kernel process   # health + pids + idler heartbeat (no token leak)
python -m drone super-kernel idler     # foreground idler only (normally started via `start`)
```

Idler heartbeat: `%USERPROFILE%\.ollama\super-kernel\idler_heartbeat.json`  
Mirror: `out\SUPER_KERNEL_IDLER.json`

Default URL: `http://127.0.0.1:11450`

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | alive |
| GET | `/status` | full kernel status |
| GET | `/vram` | live nvidia-smi |
| GET | `/models` | all installed (no cap) |
| GET | `/instances` | open instances |
| POST | `/instance/open` | `{"model":"llama3.1:8b"}` |
| POST | `/instance/close` | `{"id":"inst-...","unload":true}` |
| POST | `/chat` | `{"model"|"connection_id"|"instance_id", "prompt"}` |
| POST | `/generate` | same as chat |
| POST | `/v1/chat/completions` | OpenAI-compatible thin shim |
| POST | `/can_hang` | VRAM admission check |
| POST | `/connect` | live connection (keeps server up) |
| POST | `/disconnect` | drop connection (optional unload) |
| GET | `/connections` | list live connections |
| GET | `/live` | connections + instances + active model |
| POST | `/hot-swap` | change model on conn/instance — **no server kill** |
| POST | `/reload-config` | reload `super_kernel.json` live |
| POST | `/touch` | refresh connection last-used |

HTTP **507** = VRAM cannot hang (not a fake model limit).

### Hot swap + live connections

```powershell
# open live connection
python -m drone super-kernel connect --model llama3.2:3b --client boss

# chat on that connection
python -m drone super-kernel chat --connection conn-XXXX "hello"

# hot-swap model — SAME connection id, server PID unchanged
python -m drone super-kernel hot-swap --connection conn-XXXX --to qwen2.5:3b

# global active model swap
python -m drone super-kernel hot-swap --to llama3.1:8b --set-active

# reload config without kill
python -m drone super-kernel reload-config
```

Law: hot-swap / connect / disconnect **never** set `authorized_stop` and **never** kill the server process. Only VRAM may refuse a swap.

### CPU power + RAM power (stability rails)

Applied on every `serve` / `idler` start. Re-apply live without kill:

```powershell
python -m drone super-kernel cpu          # status
python -m drone super-kernel cpu-power    # reassert HIGH priority + thread boost
python -m drone super-kernel host-power   # CPU + RAM together
```

| Setting (`configs/super_kernel.json` → `cpu_power`) | Default |
|------------------------------------------------------|---------|
| `process_priority` | `high` (not realtime unless `SUPER_KERNEL_REALTIME=1`) |
| `affinity` | `null` = all 24 logical CPUs (full power) |
| `boost_thread` | true |
| `pulse_burn_ms` | 2 (idler micro-spin, capped 20ms) |
| `pulse_every_ticks` | 2 |

HTTP: `GET /cpu` · `GET /power` · `POST /cpu-power` · `POST /host-power`

## CLI

```powershell
python -m drone super-kernel status
python -m drone super-kernel vram
python -m drone super-kernel models
python -m drone super-kernel can-hang --model gemma4:12b
python -m drone super-kernel open --model llama3.2:3b
python -m drone super-kernel chat --model llama3.2:3b "hello"
python -m drone super-kernel smoke
python -m drone super-kernel seal
```

## Config

`configs/super_kernel.json`

- `limits.max_models` / `max_instances` → **null**
- `limits.headroom_mib` → reserved free VRAM (default 384)
- `limits.evict_lru_on_pressure` → unload LRU instances when tight

## Honesty

Kernel is a **front lane**. Weights load in Ollama. Multiple instances are registry handles; concurrent full 7B+ loads still compete for the same 12GB — kernel admits by measured free VRAM, not by counting models.
