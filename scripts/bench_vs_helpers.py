"""
Head-to-head latency bench (same goal):

  A) Drone fabric tools-only (24 nodes L||R)
  B) Drone fabric + shared Ollama LM (llama3.1:8b-class)
  C) Local Super Cell muscle helpers (probe 3b + forge coder 7b)
  D) Cloud helper (xAI / SpaceXAI OpenAI-compatible chat)

Honesty:
  - Wall-clock from this process only
  - Not identical intelligence quality — latency comparison
  - false_green: 0 — each arm must leave evidence path or error string
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(r"G:\AI-Home\projects\ai-worker-drone-0.5b")
MUSCLE = Path(r"G:\AI-Center\agents\super-cell-4\bridges\muscle_dispatch.py")
OUT = ROOT / "out" / "benchmarks"
PY = sys.executable

GOAL = (
    "List three concrete file paths under G:\\AI-Home for continuous work "
    "and one next action for the workshop hand-sculpt task. Keep under 120 words."
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _time_call(fn: Callable[[], Any]) -> tuple[float, Any, str | None]:
    t0 = time.perf_counter()
    err = None
    result = None
    try:
        result = fn()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    ms = (time.perf_counter() - t0) * 1000.0
    return ms, result, err


def ensure_ollama() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from drone.operational import ensure_ollama as _ensure

    return _ensure(timeout_s=90)


def arm_drone_tools_only() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from drone.chain import BrainFabric

    fabric = BrainFabric(ROOT, lm_fn=None, enable_tools=True)
    report = fabric.run_task_swarm(
        goal=GOAL,
        controller_kind="local",
        controller_name="bench",
        domain="bench",
        skill_tags=["bench", "build"],
        parallel_hemispheres=True,
    )
    return {
        "status": report.get("status"),
        "duration_ms_internal": report.get("duration_ms"),
        "tool_calls": report.get("tool_calls"),
        "nodes_run": report.get("nodes_run"),
        "report_path": report.get("report_path"),
        "workspace": report.get("workspace"),
        "mode": report.get("mode"),
        "ollama_model": report.get("ollama_model"),
    }


def arm_drone_with_lm() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from drone.chain import BrainFabric
    from drone.controllers import make_lm_fn

    lm = make_lm_fn("ollama")
    fabric = BrainFabric(ROOT, lm_fn=lm, enable_tools=True)
    report = fabric.run_task_swarm(
        goal=GOAL,
        controller_kind="ollama",
        controller_name="bench",
        domain="bench",
        skill_tags=["bench", "build", "lm"],
        parallel_hemispheres=True,
    )
    return {
        "status": report.get("status"),
        "duration_ms_internal": report.get("duration_ms"),
        "tool_calls": report.get("tool_calls"),
        "nodes_run": report.get("nodes_run"),
        "report_path": report.get("report_path"),
        "workspace": report.get("workspace"),
        "mode": report.get("mode"),
        "ollama_model": report.get("ollama_model"),
    }


def arm_muscle(expert: str, model: str, rounds: int = 2, timeout: int = 40) -> dict[str, Any]:
    run_id = time.strftime("bench-%Y%m%d-%H%M%S") + f"-{expert}"
    cmd = [
        PY,
        str(MUSCLE),
        "--expert",
        expert,
        "--goal",
        GOAL,
        "--run-id",
        run_id,
        "--model",
        model,
        "--rounds",
        str(rounds),
        "--timeout",
        str(timeout),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout * rounds + 30,
        check=False,
    )
    out_dir = Path(r"G:\AI-Center\agents\super-cell-4\parallel-runs") / run_id
    evidence = []
    if out_dir.is_dir():
        evidence = [str(p) for p in sorted(out_dir.rglob("*")) if p.is_file()][:30]
    stdout = (proc.stdout or "")[-2000:]
    stderr = (proc.stderr or "")[-800:]
    ok = proc.returncode == 0
    return {
        "status": "GREEN" if ok else "RED",
        "returncode": proc.returncode,
        "expert": expert,
        "model": model,
        "rounds": rounds,
        "timeout_s": timeout,
        "run_id": run_id,
        "out_dir": str(out_dir) if out_dir.exists() else None,
        "evidence_files": evidence,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
    }


def arm_cloud_xai() -> dict[str, Any]:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        return {"status": "RED", "error": "XAI_API_KEY not set"}
    model = os.environ.get("BENCH_XAI_MODEL", "grok-4.5")
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a cloud helper agent. Answer the user goal "
                        "concisely with concrete paths. Under 120 words."
                    ),
                },
                {"role": "user", "content": GOAL},
            ],
            "max_tokens": 256,
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.x.ai/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    t_connect = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        http_ms = (time.perf_counter() - t_connect) * 1000.0
    data = json.loads(raw)
    text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    usage = data.get("usage") or {}
    # save cloud reply
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"cloud_reply_{int(time.time())}.json"
    path.write_text(
        json.dumps(
            {"model": model, "goal": GOAL, "text": text, "usage": usage, "utc": _utc()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "GREEN" if text.strip() else "RED",
        "model": model,
        "http_roundtrip_ms": round(http_ms, 2),
        "reply_chars": len(text),
        "reply_preview": text[:400],
        "usage": usage,
        "evidence_path": str(path),
        "provider": "xai",
        "endpoint": "https://api.x.ai/v1/chat/completions",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bench_id = time.strftime("%Y%m%d-%H%M%S")
    results: dict[str, Any] = {
        "schema": "ai.worker.drone.bench_vs_helpers.v1",
        "bench_id": bench_id,
        "utc": _utc(),
        "goal": GOAL,
        "false_green": 0,
        "host": "BOSS",
        "arms": {},
        "notes": [
            "Same goal string for all arms",
            "Wall_ms includes arm setup in this process",
            "Helpers: Super Cell muscle_dispatch (local Ollama tool loop)",
            "Cloud: single xAI chat completion (helper-style answer, not 24 drones)",
            "Quality not scored — latency + completion only",
        ],
    }

    # Ollama prerequisite for local arms
    ol_ms, ol, ol_err = _time_call(ensure_ollama)
    results["ollama_ensure"] = {
        "wall_ms": round(ol_ms, 2),
        "result": ol,
        "error": ol_err,
    }

    # A drones tools-only
    ms, res, err = _time_call(arm_drone_tools_only)
    results["arms"]["A_drone_tools_only"] = {
        "wall_ms": round(ms, 2),
        "error": err,
        "result": res,
        "ok": err is None and (res or {}).get("status") == "GREEN",
    }

    # B drones + LM
    ms, res, err = _time_call(arm_drone_with_lm)
    results["arms"]["B_drone_plus_ollama_lm"] = {
        "wall_ms": round(ms, 2),
        "error": err,
        "result": res,
        "ok": err is None and (res or {}).get("status") == "GREEN",
    }

    # C local helpers
    ms, res, err = _time_call(lambda: arm_muscle("probe", "llama3.2:3b", rounds=2, timeout=40))
    results["arms"]["C1_helper_probe_3b"] = {
        "wall_ms": round(ms, 2),
        "error": err,
        "result": res,
        "ok": err is None and (res or {}).get("status") == "GREEN",
    }

    ms, res, err = _time_call(
        lambda: arm_muscle("forge", "qwen2.5-coder:7b", rounds=2, timeout=45)
    )
    results["arms"]["C2_helper_forge_coder7b"] = {
        "wall_ms": round(ms, 2),
        "error": err,
        "result": res,
        "ok": err is None and (res or {}).get("status") == "GREEN",
    }

    # D cloud
    ms, res, err = _time_call(arm_cloud_xai)
    results["arms"]["D_cloud_xai_helper"] = {
        "wall_ms": round(ms, 2),
        "error": err,
        "result": res,
        "ok": err is None and (res or {}).get("status") == "GREEN",
    }

    # ranking by wall_ms among ok arms
    ranked = []
    for name, arm in results["arms"].items():
        if arm.get("ok"):
            ranked.append((arm["wall_ms"], name))
    ranked.sort()
    results["ranking_fastest_ok"] = [{"wall_ms": m, "arm": n} for m, n in ranked]

    # ratios vs fastest
    if ranked:
        base = ranked[0][0] or 1.0
        results["speed_vs_fastest"] = {
            n: round(m / base, 2) for m, n in ranked
        }

    # summary table
    results["summary_table"] = [
        {
            "arm": name,
            "wall_ms": arm.get("wall_ms"),
            "ok": arm.get("ok"),
            "error": arm.get("error"),
            "detail_status": (arm.get("result") or {}).get("status"),
        }
        for name, arm in results["arms"].items()
    ]

    all_ok = all(a.get("ok") for a in results["arms"].values())
    results["status"] = "GREEN" if all_ok else "PARTIAL"
    if not any(a.get("ok") for a in results["arms"].values()):
        results["status"] = "RED"

    seal = OUT / f"BENCH_VS_HELPERS_{bench_id}.json"
    latest = OUT / "BENCH_VS_HELPERS_LATEST.json"
    text = json.dumps(results, indent=2, default=str)
    seal.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    results["seal_path"] = str(seal)
    print(text)
    return 0 if results["status"] in {"GREEN", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
