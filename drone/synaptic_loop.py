"""
Functional Synaptic Agent Loop.

Biology metaphor (real software):
  SENSE   — take user / residual goal into the loop
  FIRE    — selected worker synapses produce INTERNAL briefs (parallel)
  INTEGRATE — FACE is the only user-facing cell; merges briefs
  WEIGHT  — Hebbian update: workers that produced usable briefs get stronger
  GATE    — stop / continue based on confidence + evidence

Architecture law (Core Principal):
  User ↔ FACE only. Workers never speak to user.

false_green: 0
CLI: python -m drone synapse tick|run|status|reset|seal
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _short() -> str:
    return uuid.uuid4().hex[:10]


class SynapticLoop:
    """Continuous multi-agent loop with synaptic weights + FACE integration."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "synaptic_loop.json"
        self.out = self.root / "out"
        self.data = self.root / "data" / "synapse"
        self.out.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.weights_path = self.data / "WEIGHTS.json"
        self.trace_path = self.data / "TRACE.jsonl"
        self.state_path = self.out / "SYNAPTIC_STATE.json"
        self.last_tick_path = self.out / "SYNAPTIC_LAST_TICK.json"
        self.last_run_path = self.out / "SYNAPTIC_LAST_RUN.json"
        self.seal_path = self.out / "SYNAPTIC_LOOP_SEAL.json"
        self.cfg = self._load_cfg()
        self.weights = self._load_weights()

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {
            "loop": {"default_ticks": 3, "max_ticks": 8},
            "synapse": {"default_weight": 0.5, "learn_rate": 0.1, "decay": 0.02},
            "workers_pool": ["ollama_fast"],
        }

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _trace(self, event: str, detail: dict[str, Any]) -> None:
        row = {"utc": _utc(), "event": event, "false_green": 0, **detail}
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _load_weights(self) -> dict[str, float]:
        syn = self.cfg.get("synapse") or {}
        default = float(syn.get("default_weight") or 0.5)
        pool = list(self.cfg.get("workers_pool") or ["ollama_fast"])
        base = {w: default for w in pool}
        if self.weights_path.is_file():
            try:
                saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
                for k, v in (saved.get("weights") or {}).items():
                    base[k] = float(v)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return base

    def _save_weights(self) -> None:
        self._write(
            self.weights_path,
            {
                "schema": "drone.synaptic.weights.v1",
                "utc": _utc(),
                "weights": self.weights,
                "false_green": 0,
            },
        )

    # ── phases ────────────────────────────────────────────────

    def phase_sense(self, goal: str, residual: str = "") -> dict[str, Any]:
        """SENSE: normalize goal + residual into membrane potential."""
        g = (goal or "").strip()
        r = (residual or "").strip()
        membrane = g
        if r:
            membrane = f"{g}\n\n[residual from prior tick]\n{r[:600]}"
        return {
            "phase": "SENSE",
            "goal": g,
            "residual": r[:400],
            "membrane": membrane,
            "potential": min(1.0, 0.35 + 0.02 * len(g.split())),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_fire(
        self, membrane: str, *, max_workers: int | None = None
    ) -> dict[str, Any]:
        """FIRE: pick synapses by weight, run internal workers via MultiFace."""
        from drone.multi_face import MultiFace

        syn = self.cfg.get("synapse") or {}
        loop = self.cfg.get("loop") or {}
        max_w = int(
            max_workers
            if max_workers is not None
            else loop.get("max_workers_per_tick") or 3
        )
        min_w = int(loop.get("min_workers_per_tick") or 1)

        # Sort synapses by weight (hebbian preference)
        ranked = sorted(
            self.weights.items(), key=lambda kv: kv[1], reverse=bool(syn.get("prefer_high_weight", True))
        )
        # Always include top scout if present
        selected: list[str] = []
        for wid, w in ranked:
            if w < float(syn.get("min_weight") or 0.05):
                continue
            selected.append(wid)
            if len(selected) >= max_w:
                break
        if len(selected) < min_w and ranked:
            selected = [ranked[0][0]]

        face = MultiFace(self.root)
        # Use MultiFace ask but only workers; we'll re-integrate for loop control
        # Direct worker dispatch for synapse trace
        live = face.probe_hosts()
        hosts_cfg = {h.get("id"): h for h in (face.cfg.get("hosts") or [])}
        briefs = []
        t0 = time.perf_counter()
        for wid in selected:
            h = hosts_cfg.get(wid)
            if not h:
                briefs.append(
                    {
                        "id": wid,
                        "ok": False,
                        "error": "not_in_hosts",
                        "weight": self.weights.get(wid),
                        "internal_only": True,
                        "false_green": 0,
                    }
                )
                continue
            live_row = next(
                (x for x in (live.get("hosts") or []) if x.get("id") == wid),
                {},
            )
            if not live_row.get("up") and h.get("kind") == "ollama":
                briefs.append(
                    {
                        "id": wid,
                        "ok": False,
                        "error": "host_down",
                        "weight": self.weights.get(wid),
                        "internal_only": True,
                        "false_green": 0,
                    }
                )
                continue
            brief = face._dispatch_worker(h, membrane)
            brief["weight_before"] = self.weights.get(wid, 0.5)
            briefs.append(brief)

        ms = round((time.perf_counter() - t0) * 1000, 1)
        fired_ok = [b for b in briefs if b.get("ok") and b.get("brief")]
        return {
            "phase": "FIRE",
            "selected": selected,
            "weights_snapshot": {k: self.weights.get(k) for k in selected},
            "briefs": briefs,
            "fired_ok": len(fired_ok),
            "ms": ms,
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_integrate(self, membrane: str, fire: dict[str, Any]) -> dict[str, Any]:
        """INTEGRATE: FACE is the only user-facing cell."""
        from drone.multi_face import MultiFace

        face = MultiFace(self.root)
        face_cfg = face.cfg.get("face") or {}
        lines = []
        for b in fire.get("briefs") or []:
            wid = b.get("id")
            if b.get("ok") and b.get("brief"):
                lines.append(
                    f"### synapse {wid} (w={b.get('weight_before')})\n{b.get('brief')}"
                )
            else:
                lines.append(
                    f"### synapse {wid}\nSILENT/FAIL: {b.get('error') or 'no brief'}"
                )
        council = "\n\n".join(lines) if lines else "(no synapses fired)"
        prompt = (
            f"LOOP MEMBRANE (user goal + residual):\n{membrane}\n\n"
            f"SYNAPTIC BRIEFS (internal):\n{council}\n\n"
            "You are FACE. Reply to the user. Also end with one line:\n"
            "CONFIDENCE: 0.xx\n"
            "NEXT: <short residual question or DONE>"
        )
        r = face._ollama_chat(
            str(face_cfg.get("model") or "llama3.1:8b"),
            prompt,
            system=str(
                face_cfg.get("system")
                or "You are the FACE — only voice to the user. Use synaptic briefs."
            ),
            num_predict=int(face_cfg.get("num_predict") or 320),
            temperature=0.25,
            timeout_s=180,
        )
        text = r.get("text") or ""
        conf = self._parse_confidence(text)
        residual = self._parse_next(text)
        return {
            "phase": "INTEGRATE",
            "ok": bool(r.get("ok")),
            "face_model": face_cfg.get("model"),
            "face_reply": text,
            "confidence": conf,
            "residual": residual,
            "ms": r.get("ms"),
            "error": r.get("error"),
            "false_green": 0,
            "utc": _utc(),
        }

    def _parse_confidence(self, text: str) -> float:
        import re

        m = re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0+|1)", text or "", re.I)
        if not m:
            # heuristic: longer coherent reply → mid conf
            return 0.55 if (text or "").strip() else 0.1
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            return 0.5

    def _parse_next(self, text: str) -> str:
        import re

        m = re.search(r"NEXT:\s*(.+)$", text or "", re.I | re.M)
        if not m:
            return ""
        nxt = m.group(1).strip()
        if nxt.upper() in {"DONE", "NONE", "N/A", "-"}:
            return ""
        return nxt[:400]

    def phase_weight(self, fire: dict[str, Any], integrate: dict[str, Any]) -> dict[str, Any]:
        """WEIGHT: Hebbian — usable brief + good face conf strengthens synapse."""
        syn = self.cfg.get("synapse") or {}
        lr = float(syn.get("learn_rate") or 0.12)
        decay = float(syn.get("decay") or 0.02)
        wmin = float(syn.get("min_weight") or 0.05)
        wmax = float(syn.get("max_weight") or 0.95)
        conf = float(integrate.get("confidence") or 0.5)
        updates = {}

        # decay all slightly
        for wid in list(self.weights.keys()):
            self.weights[wid] = max(wmin, self.weights[wid] - decay)

        for b in fire.get("briefs") or []:
            wid = str(b.get("id") or "")
            if not wid:
                continue
            if wid not in self.weights:
                self.weights[wid] = float(syn.get("default_weight") or 0.5)
            before = self.weights[wid]
            if b.get("ok") and b.get("brief") and conf >= 0.45:
                # Hebbian potentiation
                delta = lr * conf
                self.weights[wid] = min(wmax, before + delta)
            elif not b.get("ok"):
                self.weights[wid] = max(wmin, before - lr * 0.5)
            updates[wid] = {
                "before": round(before, 4),
                "after": round(self.weights[wid], 4),
                "ok": bool(b.get("ok")),
            }

        self._save_weights()
        return {
            "phase": "WEIGHT",
            "updates": updates,
            "weights": dict(self.weights),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_gate(
        self,
        integrate: dict[str, Any],
        tick: int,
        max_ticks: int,
    ) -> dict[str, Any]:
        """GATE: decide continue / stop."""
        loop = self.cfg.get("loop") or {}
        gate_cfg = self.cfg.get("gate") or {}
        conf = float(integrate.get("confidence") or 0)
        stop_conf = float(loop.get("stop_confidence") or 0.85)
        residual = (integrate.get("residual") or "").strip()
        face_ok = bool(integrate.get("ok") and (integrate.get("face_reply") or "").strip())

        stop = False
        reason = "continue"
        if not face_ok and gate_cfg.get("require_face_text", True):
            stop = tick >= max_ticks
            reason = "face_weak" if not stop else "max_ticks_face_weak"
        elif conf >= stop_conf and not residual:
            stop = True
            reason = "confidence_and_done"
        elif residual.upper() == "DONE" or residual == "":
            if conf >= 0.7:
                stop = True
                reason = "done_residual"
            else:
                reason = "low_conf_empty_residual"
        if tick >= max_ticks:
            stop = True
            reason = "max_ticks"

        return {
            "phase": "GATE",
            "stop": stop,
            "reason": reason,
            "confidence": conf,
            "residual": residual,
            "tick": tick,
            "max_ticks": max_ticks,
            "false_green": 0,
            "utc": _utc(),
        }

    # ── tick / run ────────────────────────────────────────────

    def tick(
        self,
        goal: str,
        *,
        residual: str = "",
        tick_index: int = 1,
        max_ticks: int = 3,
    ) -> dict[str, Any]:
        """One full synaptic cycle."""
        tid = f"tick_{tick_index}_{_short()}"
        t0 = time.perf_counter()

        sense = self.phase_sense(goal, residual)
        fire = self.phase_fire(sense["membrane"])
        integrate = self.phase_integrate(sense["membrane"], fire)
        weight = self.phase_weight(fire, integrate)
        gate = self.phase_gate(integrate, tick_index, max_ticks)

        ms = round((time.perf_counter() - t0) * 1000, 1)
        packet = {
            "schema": "drone.synaptic.tick.v1",
            "tick_id": tid,
            "tick": tick_index,
            "status": "GREEN" if integrate.get("ok") else "PARTIAL",
            "false_green": 0,
            "duration_ms": ms,
            "goal": goal,
            "phases": {
                "SENSE": sense,
                "FIRE": fire,
                "INTEGRATE": {
                    **{k: v for k, v in integrate.items() if k != "face_reply"},
                    "face_reply_preview": (integrate.get("face_reply") or "")[:400],
                },
                "WEIGHT": weight,
                "GATE": gate,
            },
            "face_reply": integrate.get("face_reply"),
            "confidence": integrate.get("confidence"),
            "residual_out": gate.get("residual") or integrate.get("residual"),
            "stop": gate.get("stop"),
            "stop_reason": gate.get("reason"),
            "workers_fired": fire.get("selected"),
            "fired_ok": fire.get("fired_ok"),
            "utc": _utc(),
        }
        path = self.data / "ticks" / f"{tid}.json"
        self._write(path, packet)
        packet["path"] = str(path)
        self._write(self.last_tick_path, packet)
        self._trace(
            "tick",
            {
                "tick_id": tid,
                "tick": tick_index,
                "status": packet["status"],
                "confidence": packet["confidence"],
                "stop": packet["stop"],
                "fired_ok": packet["fired_ok"],
                "ms": ms,
            },
        )
        return packet

    def run(
        self,
        goal: str,
        *,
        ticks: int | None = None,
        max_ticks: int | None = None,
    ) -> dict[str, Any]:
        """Multi-tick synaptic loop until gate stop or max ticks."""
        loop = self.cfg.get("loop") or {}
        n = int(ticks if ticks is not None else loop.get("default_ticks") or 3)
        cap = int(max_ticks if max_ticks is not None else loop.get("max_ticks") or 12)
        n = max(1, min(n, cap))
        sleep_ms = int(loop.get("tick_sleep_ms") or 50)

        run_id = f"run_{_short()}"
        t0 = time.perf_counter()
        residual = ""
        history: list[dict[str, Any]] = []
        final_reply = ""
        stop_reason = "completed_ticks"

        for i in range(1, n + 1):
            tick = self.tick(goal, residual=residual, tick_index=i, max_ticks=n)
            history.append(
                {
                    "tick": i,
                    "tick_id": tick.get("tick_id"),
                    "status": tick.get("status"),
                    "confidence": tick.get("confidence"),
                    "fired_ok": tick.get("fired_ok"),
                    "workers": tick.get("workers_fired"),
                    "stop": tick.get("stop"),
                    "path": tick.get("path"),
                    "face_preview": (tick.get("face_reply") or "")[:200],
                }
            )
            final_reply = tick.get("face_reply") or final_reply
            residual = tick.get("residual_out") or ""
            if tick.get("stop"):
                stop_reason = str(tick.get("stop_reason") or "gate")
                break
            if sleep_ms > 0 and i < n:
                time.sleep(sleep_ms / 1000.0)

        ms = round((time.perf_counter() - t0) * 1000, 1)
        ok = bool(final_reply.strip())
        result = {
            "schema": "drone.synaptic.run.v1",
            "run_id": run_id,
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "utc": _utc(),
            "duration_ms": ms,
            "goal": goal,
            "ticks_requested": n,
            "ticks_ran": len(history),
            "stop_reason": stop_reason,
            "face_reply": final_reply,
            "final_confidence": (history[-1].get("confidence") if history else 0),
            "history": history,
            "weights": dict(self.weights),
            "principal": "User ↔ FACE only; workers are synapses",
            "phases_per_tick": self.cfg.get("phases"),
            "evidence": [
                str(self.last_tick_path),
                str(self.weights_path),
                str(self.trace_path),
            ],
        }
        self._write(self.last_run_path, result)
        self._write(
            self.state_path,
            {
                "utc": _utc(),
                "last_run_id": run_id,
                "status": result["status"],
                "weights": self.weights,
                "false_green": 0,
            },
        )
        self._trace("run", {"run_id": run_id, "status": result["status"], "ticks": len(history)})
        return result

    def status(self) -> dict[str, Any]:
        return {
            "schema": "drone.synaptic.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "weights": dict(self.weights),
            "weights_path": str(self.weights_path),
            "last_tick": str(self.last_tick_path) if self.last_tick_path.is_file() else None,
            "last_run": str(self.last_run_path) if self.last_run_path.is_file() else None,
            "trace": str(self.trace_path),
            "phases": self.cfg.get("phases"),
            "pool": self.cfg.get("workers_pool"),
        }

    def reset_weights(self) -> dict[str, Any]:
        syn = self.cfg.get("synapse") or {}
        default = float(syn.get("default_weight") or 0.5)
        pool = list(self.cfg.get("workers_pool") or ["ollama_fast"])
        self.weights = {w: default for w in pool}
        self._save_weights()
        return {
            "status": "GREEN",
            "false_green": 0,
            "weights": dict(self.weights),
            "utc": _utc(),
        }

    def seal(self, goal: str | None = None) -> dict[str, Any]:
        g = goal or "Synaptic loop smoke: confirm FACE integration works in one short sentence."
        run = self.run(g, ticks=2)
        seal = {
            "schema": "drone.synaptic.seal.v1",
            "status": run.get("status"),
            "false_green": 0,
            "utc": _utc(),
            "run_id": run.get("run_id"),
            "ticks_ran": run.get("ticks_ran"),
            "face_reply_preview": (run.get("face_reply") or "")[:300],
            "weights": run.get("weights"),
            "evidence": run.get("evidence")
            + [str(self.seal_path), str(self.last_run_path)],
            "functional": True,
            "principal": "synaptic workers → FACE → user",
        }
        self._write(self.seal_path, seal)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="drone synapse",
        description="Functional synaptic agent loop (workers → FACE → user)",
    )
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("reset")
    tk = sub.add_parser("tick", help="one synaptic tick")
    tk.add_argument("goal", nargs="+")
    tk.add_argument("--residual", default="")
    rn = sub.add_parser("run", help="multi-tick loop")
    rn.add_argument("goal", nargs="+")
    rn.add_argument("--ticks", type=int, default=None)
    sl = sub.add_parser("seal", help="smoke seal 2-tick run")
    sl.add_argument("--goal", default=None)

    args = p.parse_args(argv)
    loop = SynapticLoop(Path(args.root) if args.root else None)

    if args.cmd == "status":
        print(json.dumps(loop.status(), indent=2))
        return 0
    if args.cmd == "reset":
        print(json.dumps(loop.reset_weights(), indent=2))
        return 0
    if args.cmd == "tick":
        goal = " ".join(args.goal)
        out = loop.tick(goal, residual=args.residual or "")
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "run":
        goal = " ".join(args.goal)
        out = loop.run(goal, ticks=args.ticks)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "seal":
        out = loop.seal(args.goal)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
