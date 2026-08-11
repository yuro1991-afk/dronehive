#!/usr/bin/env python3
"""
Stress test: AI Bus two-way + knowledge imprint + hive fast swarm.

Honesty: real timings, real paths, false_green:0. No mock success.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(r"G:\AI-Home\projects\ai-worker-drone-0.5b")
sys.path.insert(0, str(ROOT))


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[i], 2)


def main() -> int:
    from drone.ai_bus import AIBus
    from drone.knowledge_imprint import build_knowledge_imprint, knowledge_sources_status
    from drone.hive import BuzzerHive
    from drone.tools import DroneToolkit
    from drone.work_order import imprint_drone, build_task_slot, load_work_order_config

    bus = AIBus(ROOT)
    report: dict = {
        "schema": "ai.worker.drone.stress_test.v1",
        "utc": _utc(),
        "false_green": 0,
        "host": "BOSS",
        "root": str(ROOT),
        "phases": {},
        "errors": [],
    }
    t_all = time.perf_counter()

    # ── Phase A: connection matrix ────────────────────────────
    t0 = time.perf_counter()
    try:
        conn = bus.connect_status()
        report["phases"]["A_connections"] = {
            "ok": True,
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "two_way_count": conn.get("two_way_count"),
            "channel_count": conn.get("channel_count"),
            "all_two_way": conn.get("all_two_way"),
            "path": conn.get("status_path"),
        }
    except Exception as e:
        report["phases"]["A_connections"] = {"ok": False, "error": str(e)}
        report["errors"].append(f"A:{e}")

    # ── Phase B: parallel channel reads (stress read path) ───
    channels = bus.channels()
    goals_pool = [
        "swarm multi-agent ollama coding",
        "blender mesh sculpt topology",
        "ue5 nanite materials pipeline",
        "rag embedding memory retrieval",
        "debug host gpu ollama inference",
        "instai peer lesson teaching",
        "codex model quant vram 12gb",
        "continuous open tasks board",
    ]
    t0 = time.perf_counter()
    read_ms: list[float] = []
    read_ok = 0
    read_fail = 0
    read_n = len(channels) * 4  # 4 rounds over all channels

    def _read_one(args: tuple[str, str]) -> tuple[bool, float, str]:
        ch, q = args
        s = time.perf_counter()
        try:
            hit = bus.read(ch, query=q, limit=3)
            ok = bool(hit.get("ok") or hit.get("probe_ok"))
            return ok, (time.perf_counter() - s) * 1000, ch
        except Exception:
            return False, (time.perf_counter() - s) * 1000, ch

    jobs = []
    for r in range(4):
        for i, ch in enumerate(channels):
            jobs.append((ch, goals_pool[(r + i) % len(goals_pool)]))

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="bus_r") as pool:
        futs = [pool.submit(_read_one, j) for j in jobs]
        for f in as_completed(futs):
            ok, ms, _ch = f.result()
            read_ms.append(ms)
            if ok:
                read_ok += 1
            else:
                read_fail += 1

    report["phases"]["B_parallel_reads"] = {
        "ok": read_fail == 0,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "ops": read_n,
        "ok_count": read_ok,
        "fail_count": read_fail,
        "workers": 8,
        "latency_ms": {
            "min": round(min(read_ms), 2) if read_ms else 0,
            "p50": _pct(read_ms, 50),
            "p95": _pct(read_ms, 95),
            "max": round(max(read_ms), 2) if read_ms else 0,
            "mean": round(statistics.mean(read_ms), 2) if read_ms else 0,
        },
    }

    # ── Phase C: parallel writes (stress write path) ──────────
    t0 = time.perf_counter()
    write_ms: list[float] = []
    write_ok = 0
    write_fail = 0
    write_n = len(channels) * 2

    def _write_one(args: tuple[str, int]) -> tuple[bool, float, str]:
        ch, i = args
        s = time.perf_counter()
        try:
            hit = bus.write(
                ch,
                {
                    "status": "PARTIAL",
                    "summary": f"stress write {ch} #{i}",
                    "text": f"stress test write channel={ch} i={i}",
                    "notes": f"stress {i}",
                    "evidence_paths": [str(ROOT / "out")],
                    "tags": ["stress", "ai_bus"],
                    "engine": "other",
                    "action": "stress_test",
                },
                unit_id=f"stress_w_{i}_{ch[:8]}",
                goal=f"stress write {ch}",
            )
            return bool(hit.get("ok")), (time.perf_counter() - s) * 1000, ch
        except Exception:
            return False, (time.perf_counter() - s) * 1000, ch

    wjobs = []
    for r in range(2):
        for i, ch in enumerate(channels):
            wjobs.append((ch, r * 100 + i))

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="bus_w") as pool:
        futs = [pool.submit(_write_one, j) for j in wjobs]
        for f in as_completed(futs):
            ok, ms, _ch = f.result()
            write_ms.append(ms)
            if ok:
                write_ok += 1
            else:
                write_fail += 1

    report["phases"]["C_parallel_writes"] = {
        "ok": write_fail == 0,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "ops": write_n,
        "ok_count": write_ok,
        "fail_count": write_fail,
        "workers": 6,
        "latency_ms": {
            "min": round(min(write_ms), 2) if write_ms else 0,
            "p50": _pct(write_ms, 50),
            "p95": _pct(write_ms, 95),
            "max": round(max(write_ms), 2) if write_ms else 0,
            "mean": round(statistics.mean(write_ms), 2) if write_ms else 0,
        },
    }

    # ── Phase D: full duplex waves (serial intensity) ─────────
    t0 = time.perf_counter()
    duplex_ms: list[float] = []
    duplex_ok = 0
    duplex_fail = 0
    duplex_n = 6
    for i in range(duplex_n):
        g = goals_pool[i % len(goals_pool)]
        s = time.perf_counter()
        try:
            d = bus.full_duplex(
                g,
                unit_id=f"stress_dx_{i}",
                result={
                    "status": "PARTIAL",
                    "goal": g,
                    "buzzer_id": f"stress_dx_{i}",
                    "summary": f"duplex stress {i}",
                    "evidence_paths": [str(ROOT / "out")],
                },
            )
            ms = (time.perf_counter() - s) * 1000
            duplex_ms.append(ms)
            if d.get("status") in {"GREEN", "PARTIAL"} and (d.get("outbound") or {}).get("ok"):
                duplex_ok += 1
            else:
                duplex_fail += 1
        except Exception as e:
            duplex_ms.append((time.perf_counter() - s) * 1000)
            duplex_fail += 1
            report["errors"].append(f"D{i}:{e}")

    report["phases"]["D_full_duplex_waves"] = {
        "ok": duplex_fail == 0,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "waves": duplex_n,
        "ok_count": duplex_ok,
        "fail_count": duplex_fail,
        "latency_ms": {
            "min": round(min(duplex_ms), 2) if duplex_ms else 0,
            "p50": _pct(duplex_ms, 50),
            "p95": _pct(duplex_ms, 95),
            "max": round(max(duplex_ms), 2) if duplex_ms else 0,
            "mean": round(statistics.mean(duplex_ms), 2) if duplex_ms else 0,
        },
    }

    # ── Phase E: knowledge imprint max (parallel goals) ───────
    t0 = time.perf_counter()
    ki_ms: list[float] = []
    ki_ok = 0
    ki_fail = 0
    ki_sources: list[dict] = []

    def _ki(g: str) -> tuple[bool, float, dict]:
        s = time.perf_counter()
        try:
            p = build_knowledge_imprint(ROOT, g, force_refresh=True)
            ms = (time.perf_counter() - s) * 1000
            return bool(p.get("ok")), ms, p.get("sources_hit") or {}
        except Exception:
            return False, (time.perf_counter() - s) * 1000, {}

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ki") as pool:
        futs = {pool.submit(_ki, g): g for g in goals_pool}
        for f in as_completed(futs):
            ok, ms, src = f.result()
            ki_ms.append(ms)
            ki_sources.append(src)
            if ok:
                ki_ok += 1
            else:
                ki_fail += 1

    # warm cache hit bench
    s = time.perf_counter()
    warm = build_knowledge_imprint(ROOT, goals_pool[0], force_refresh=False)
    warm_ms = round((time.perf_counter() - s) * 1000, 2)

    report["phases"]["E_knowledge_imprint"] = {
        "ok": ki_fail == 0,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "goals": len(goals_pool),
        "ok_count": ki_ok,
        "fail_count": ki_fail,
        "workers": 4,
        "warm_cache_ms": warm_ms,
        "warm_cache_hit": bool(warm.get("cache_hit")),
        "latency_ms": {
            "min": round(min(ki_ms), 2) if ki_ms else 0,
            "p50": _pct(ki_ms, 50),
            "p95": _pct(ki_ms, 95),
            "max": round(max(ki_ms), 2) if ki_ms else 0,
            "mean": round(statistics.mean(ki_ms), 2) if ki_ms else 0,
        },
        "sources_sample": ki_sources[:3],
        "sources_inventory": knowledge_sources_status().get("instai_lesson_count"),
    }

    # ── Phase F: imprint_drone birth stress ───────────────────
    t0 = time.perf_counter()
    imp_ms: list[float] = []
    imp_ok = 0
    cfg = load_work_order_config(ROOT)
    for i in range(12):
        g = goals_pool[i % len(goals_pool)]
        task = build_task_slot(g, cfg=cfg)
        s = time.perf_counter()
        try:
            im = imprint_drone(
                ROOT,
                buzzer_id=f"stress_imp_{i}_{int(time.time()) % 10000}",
                generation=i,
                task=task,
            )
            ms = (time.perf_counter() - s) * 1000
            imp_ms.append(ms)
            if im.get("imprint_path") and Path(im["imprint_path"]).is_file():
                imp_ok += 1
        except Exception as e:
            imp_ms.append((time.perf_counter() - s) * 1000)
            report["errors"].append(f"F{i}:{e}")

    report["phases"]["F_imprint_birth"] = {
        "ok": imp_ok == 12,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "units": 12,
        "ok_count": imp_ok,
        "latency_ms": {
            "min": round(min(imp_ms), 2) if imp_ms else 0,
            "p50": _pct(imp_ms, 50),
            "p95": _pct(imp_ms, 95),
            "max": round(max(imp_ms), 2) if imp_ms else 0,
            "mean": round(statistics.mean(imp_ms), 2) if imp_ms else 0,
        },
    }

    # ── Phase G: hive fast swarm (real buzzers, no heavy LLM) ─
    t0 = time.perf_counter()
    hive_result: dict = {}
    try:
        hive = BuzzerHive(ROOT, lane="fast", lm_assist="off")
        hive_goals = [
            f"stress hive package note {i}: write real file evidence for swarm path"
            for i in range(8)
        ]
        # Do NOT pass cycles=len(goals) carefully: cycles truncates planned goals.
        # Default cycles=6 caps multi-goal fan-out; pass cycles>=len(goals) for full list.
        hive_result = hive.run_swarm(
            goals=hive_goals,
            cycles=max(8, len(hive_goals)),
            workers=4,
            parallel=True,
            controller="local",
            controller_name="stress",
            pull_library=False,
            write_library_note=True,
            post_live_write=False,
            lane="fast",
            domain="build",
        )
        # units may be nested under results / buzzers / unit_reports
        units = (
            hive_result.get("results")
            or hive_result.get("units_detail")
            or hive_result.get("buzzers")
            or hive_result.get("unit_reports")
            or []
        )
        status_list = []
        if isinstance(units, list):
            for u in units:
                if isinstance(u, dict):
                    status_list.append(str(u.get("status") or "UNKNOWN"))
        green_n = sum(1 for s in status_list if s == "GREEN")
        partial_n = sum(1 for s in status_list if s == "PARTIAL")
        red_n = sum(1 for s in status_list if s == "RED")
        hive_status = str(hive_result.get("status") or "").upper()
        report["phases"]["G_hive_fast_swarm"] = {
            "ok": hive_status in {"GREEN", "PARTIAL"},
            "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
            "status": hive_result.get("status"),
            "mode": hive_result.get("mode"),
            "workers": hive_result.get("workers"),
            "peak_parallel": hive_result.get("peak_parallel_observed"),
            "units_planned": hive_result.get("units")
            or hive_result.get("goals_n")
            or len(hive_goals),
            "imprinted_units": hive_result.get("imprinted_units"),
            "unit_statuses": {
                "GREEN": green_n,
                "PARTIAL": partial_n,
                "RED": red_n,
                "listed": len(status_list),
            },
            "seal_path": hive_result.get("seal_path") or hive_result.get("report_path"),
            "keys": list(hive_result.keys())[:24],
            "ai_bus_on_units": sum(
                1
                for u in units
                if isinstance(u, dict) and (u.get("ai_bus") or {}).get("ok")
            )
            if isinstance(units, list)
            else None,
            "false_green": 0,
        }
    except Exception as e:
        report["phases"]["G_hive_fast_swarm"] = {
            "ok": False,
            "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
            "error": str(e),
            "trace": traceback.format_exc()[-800:],
        }
        report["errors"].append(f"G:{e}")

    # ── Phase H: toolkit concurrent bus ops ───────────────────
    t0 = time.perf_counter()
    tk_ok = 0
    tk_fail = 0
    tk_ms: list[float] = []

    def _tk(i: int) -> tuple[bool, float]:
        s = time.perf_counter()
        try:
            tk = DroneToolkit(ROOT, task_id=f"stress_tk_{i}")
            a = tk.ai_bus_status()
            b = tk.ai_bus_read("self_library", "stress")
            c = tk.ai_bus_write("hive_memory", notes=f"tk stress {i}", status="PARTIAL")
            ok = bool(a.get("ok") and b.get("ok") and c.get("ok"))
            return ok, (time.perf_counter() - s) * 1000
        except Exception:
            return False, (time.perf_counter() - s) * 1000

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tk") as pool:
        futs = [pool.submit(_tk, i) for i in range(8)]
        for f in as_completed(futs):
            ok, ms = f.result()
            tk_ms.append(ms)
            if ok:
                tk_ok += 1
            else:
                tk_fail += 1

    report["phases"]["H_toolkit_concurrent"] = {
        "ok": tk_fail == 0,
        "ms_wall": round((time.perf_counter() - t0) * 1000, 2),
        "ops_bundles": 8,
        "ok_count": tk_ok,
        "fail_count": tk_fail,
        "latency_ms": {
            "p50": _pct(tk_ms, 50),
            "p95": _pct(tk_ms, 95),
            "max": round(max(tk_ms), 2) if tk_ms else 0,
            "mean": round(statistics.mean(tk_ms), 2) if tk_ms else 0,
        },
    }

    # ── Aggregate ─────────────────────────────────────────────
    phases = report["phases"]
    phase_ok = {k: bool(v.get("ok")) for k, v in phases.items()}
    all_ok = all(phase_ok.values()) if phase_ok else False
    report["summary"] = {
        "phases_total": len(phase_ok),
        "phases_ok": sum(1 for v in phase_ok.values() if v),
        "phases_fail": sum(1 for v in phase_ok.values() if not v),
        "phase_ok": phase_ok,
        "wall_ms": round((time.perf_counter() - t_all) * 1000, 2),
        "error_count": len(report["errors"]),
    }
    # status honesty
    if all_ok:
        report["status"] = "GREEN"
    elif report["summary"]["phases_ok"] >= 5:
        report["status"] = "PARTIAL"
    else:
        report["status"] = "RED"

    out = ROOT / "out" / "STRESS_TEST_SEAL.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["seal_path"] = str(out)

    # also bus-write the seal itself
    try:
        bus.sync_write(
            {
                "status": report["status"],
                "goal": "stress test complete",
                "buzzer_id": "stress_harness",
                "summary": f"stress {report['status']} phases_ok={report['summary']['phases_ok']}",
                "evidence_paths": [str(out)],
                "seal_path": str(out),
            },
            unit_id="stress_harness",
            goal="stress test complete",
        )
    except Exception as e:
        report["errors"].append(f"final_bus:{e}")
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nSEAL {report['status']} → {out}")
    return 0 if report["status"] in {"GREEN", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
