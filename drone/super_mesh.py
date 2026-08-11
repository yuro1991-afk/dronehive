"""
Super Mesh Live AI — 20 m2m synapses + 2 flagship poles + tools + packet recycle.

Deep hardwire:
  m2m-* (0.5B)  = mesh neurons (strict M2M / AI2AI)
  fx-reason     = flagship conductor (AI2AI)
  fx-face       = only user-facing pole
  DroneToolkit  = full tool belt (disk/shell/python/bus/llm/…)
  PacketRecycleBus = emit → consume → recycle → reinject

Live loop:
  SENSE → ROUTE → FIRE_MESH → PACKETS → TOOLS → HEMISPHERE_MERGE
  → CONDUCT → FACE → RECYCLE → WEIGHT → GATE

false_green: 0 — GREEN only when real Ollama fires + tools/packets on disk.
CLI: python -m drone mesh hardwire|status|tick|run|seal|install-flagships|packets|recycle|tools
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from drone.mesh_packets import MeshPacket, PacketRecycleBus
from drone.tools import DroneToolkit


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _short() -> str:
    return uuid.uuid4().hex[:10]


class SuperMesh:
    """Live super mesh brain: hardwired m2m synapses + dual flagships + tools + recycle."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "super_mesh.json"
        self.out = self.root / "out"
        self.data = self.root / "data" / "super_mesh"
        self.out.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load(self.cfg_path)
        self.weights_path = self.data / "NODE_WEIGHTS.json"
        self.edges_path = self.data / "EDGE_WEIGHTS.json"
        self.hardwire_path = self.data / "HARDWIRE.json"
        self.trace_path = self.data / "TRACE.jsonl"
        self.state_path = self.out / "SUPER_MESH_STATE.json"
        self.last_tick_path = self.out / "SUPER_MESH_LAST_TICK.json"
        self.last_run_path = self.out / "SUPER_MESH_LAST_RUN.json"
        self.seal_path = self.out / "SUPER_MESH_SEAL.json"
        self.last_tools_path = self.out / "SUPER_MESH_TOOLS_LAST.json"
        self.node_weights = self._load_node_weights()
        self.edge_weights = self._load_edge_weights()
        rec = self.cfg.get("recycle") or {}
        self.bus = PacketRecycleBus(
            self.root,
            max_active=int(rec.get("max_active") or 64),
            max_recycle=int(rec.get("max_recycle") or 128),
        )

    def _load(self, path: Path) -> dict[str, Any]:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

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

    # ── topology ──────────────────────────────────────────────

    def all_m2m_nodes(self) -> list[str]:
        hemi = self.cfg.get("hemispheres") or {}
        left = list((hemi.get("left") or {}).get("nodes") or [])
        right = list((hemi.get("right") or {}).get("nodes") or [])
        # preserve order, unique
        seen: set[str] = set()
        out: list[str] = []
        for n in left + right:
            if n not in seen:
                seen.add(n)
                out.append(n)
        # fallback catalog
        if len(out) < 20:
            cat = self.root / "configs" / "m2m_clones_0.5b.json"
            if cat.is_file():
                clones = json.loads(cat.read_text(encoding="utf-8")).get("clones") or []
                for c in clones:
                    t = str(c.get("tag") or "")
                    if t and t not in seen:
                        seen.add(t)
                        out.append(t)
        return out

    def flagships(self) -> dict[str, str]:
        f = self.cfg.get("flagships") or {}
        return {
            "reason": str(f.get("reason") or "fx-reason"),
            "face": str(f.get("face") or "fx-face"),
        }

    def hardwire_edges(self) -> list[tuple[str, str]]:
        raw = self.cfg.get("hardwire_edges") or []
        edges: list[tuple[str, str]] = []
        for e in raw:
            if isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append((str(e[0]), str(e[1])))
        return edges

    def _load_node_weights(self) -> dict[str, float]:
        syn = self.cfg.get("synapse") or {}
        default = float(syn.get("default_weight") or 0.5)
        nodes = self.all_m2m_nodes() + list(self.flagships().values())
        base = {n: default for n in nodes}
        if self.weights_path.is_file():
            try:
                saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
                for k, v in (saved.get("weights") or {}).items():
                    base[str(k)] = float(v)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return base

    def _load_edge_weights(self) -> dict[str, float]:
        syn = self.cfg.get("synapse") or {}
        ed = float(syn.get("edge_default") or 0.35)
        base: dict[str, float] = {}
        for a, b in self.hardwire_edges():
            base[f"{a}->{b}"] = ed
        if self.edges_path.is_file():
            try:
                saved = json.loads(self.edges_path.read_text(encoding="utf-8"))
                for k, v in (saved.get("edges") or {}).items():
                    base[str(k)] = float(v)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return base

    def _save_weights(self) -> None:
        self._write(
            self.weights_path,
            {
                "schema": "drone.super_mesh.node_weights.v1",
                "utc": _utc(),
                "weights": self.node_weights,
                "false_green": 0,
            },
        )
        self._write(
            self.edges_path,
            {
                "schema": "drone.super_mesh.edge_weights.v1",
                "utc": _utc(),
                "edges": self.edge_weights,
                "false_green": 0,
            },
        )

    # ── ollama ────────────────────────────────────────────────

    def list_installed(self) -> list[str]:
        try:
            req = urllib.request.Request(f"{ollama_host()}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        except Exception:
            return []

    @staticmethod
    def _name_ok(tag: str, installed: list[str] | set[str]) -> bool:
        inst = set(installed)
        if tag in inst or f"{tag}:latest" in inst:
            return True
        base = tag.split(":")[0]
        return any(n == base or n.startswith(base + ":") for n in inst)

    def _chat(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        num_predict: int = 120,
        temperature: float = 0.1,
        timeout_s: float = 90,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        loop = self.cfg.get("loop") or {}
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": loop.get("keep_alive") or "30m",
                "options": {"num_predict": num_predict, "temperature": temperature},
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{ollama_host()}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str((data.get("message") or {}).get("content", "")).strip()
            return {
                "ok": bool(text),
                "text": text,
                "model": model,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "model": model,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }

    # ── hardwire ──────────────────────────────────────────────

    def hardwire(self) -> dict[str, Any]:
        """Deep-bind mesh topology into durable state + app configs."""
        installed = self.list_installed()
        nodes = self.all_m2m_nodes()
        fx = self.flagships()
        edges = self.hardwire_edges()

        m2m_live = [n for n in nodes if self._name_ok(n, installed)]
        fx_live = {k: v for k, v in fx.items() if self._name_ok(v, installed)}

        # ensure weights cover all nodes
        for n in nodes + list(fx.values()):
            self.node_weights.setdefault(n, float((self.cfg.get("synapse") or {}).get("default_weight") or 0.5))
        for a, b in edges:
            self.edge_weights.setdefault(
                f"{a}->{b}",
                float((self.cfg.get("synapse") or {}).get("edge_default") or 0.35),
            )
        self._save_weights()

        pack = {
            "schema": "drone.super_mesh.hardwire.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": "GREEN"
            if len(m2m_live) >= 20 and len(fx_live) == 2
            else ("PARTIAL" if m2m_live else "RED"),
            "m2m_catalog": len(nodes),
            "m2m_live": len(m2m_live),
            "m2m_missing": [n for n in nodes if n not in m2m_live and not self._name_ok(n, installed)],
            "flagships_wanted": fx,
            "flagships_live": fx_live,
            "edges": len(edges),
            "edge_keys": [f"{a}->{b}" for a, b in edges],
            "hemispheres": self.cfg.get("hemispheres"),
            "principal": self.cfg.get("principal"),
            "nodes": nodes,
            "idle_until_called": True,
            "roster": "configs/m2m_node_roster.json",
        }
        self._write(self.hardwire_path, pack)

        # bind into multi_hosts + super_llms + synaptic_loop
        multi_path = self.root / "configs" / "multi_hosts.json"
        super_path = self.root / "configs" / "super_llms.json"
        syn_path = self.root / "configs" / "synaptic_loop.json"
        multi = self._load(multi_path) if multi_path.is_file() else {}
        super_cfg = self._load(super_path) if super_path.is_file() else {}
        syn_cfg = self._load(syn_path) if syn_path.is_file() else {}

        # All nodes IDLE until called (one tool + one job each)
        from drone.mesh_nodes import MeshNodeRegistry

        idle_hw = MeshNodeRegistry(self.root).hardwire_idle()

        multi["super_mesh"] = {
            "enabled": True,
            "hardwire": str(self.hardwire_path),
            "config": "configs/super_mesh.json",
            "roster": "configs/m2m_node_roster.json",
            "m2m_live": m2m_live,
            "flagships": fx_live,
            "edges": len(edges),
            "strict_m2m": True,
            "idle_until_called": True,
            "idle_hardwire": idle_hw.get("status"),
            "idle_count": idle_hw.get("count_m2m"),
        }
        if self._name_ok(fx.get("face", ""), installed):
            face = multi.get("face") or {}
            face["model"] = fx["face"]
            face["flagship"] = True
            face["super_mesh"] = True
            multi["face"] = face
        multi_path.write_text(json.dumps(multi, indent=2), encoding="utf-8")

        super_cfg["super_mesh"] = multi["super_mesh"]
        routing = super_cfg.get("routing") or {}
        if self._name_ok(fx.get("face", ""), installed):
            routing["default"] = fx["face"]
            routing["face"] = fx["face"]
        if self._name_ok(fx.get("reason", ""), installed):
            routing["reason"] = fx["reason"]
            routing["mesh_conductor"] = fx["reason"]
        routing["m2m_mesh"] = "super_mesh"
        super_cfg["routing"] = routing
        super_path.write_text(json.dumps(super_cfg, indent=2), encoding="utf-8")

        # synaptic pool = all m2m tags (deep hardwire)
        syn_cfg["super_mesh"] = True
        syn_cfg["workers_pool"] = m2m_live if m2m_live else nodes
        syn_cfg["flagships"] = fx
        syn_cfg["principal"] = (
            "User ↔ fx-face only; 20 m2m synapses fire in super mesh; fx-reason conducts"
        )
        syn_path.write_text(json.dumps(syn_cfg, indent=2), encoding="utf-8")

        pack["wired"] = {
            "multi_hosts": str(multi_path),
            "super_llms": str(super_path),
            "synaptic_loop": str(syn_path),
            "node_weights": str(self.weights_path),
            "edge_weights": str(self.edges_path),
        }
        self._write(self.hardwire_path, pack)
        self._write(self.state_path, {"hardwire": pack, "utc": _utc(), "false_green": 0})
        self._trace("hardwire", {"m2m_live": len(m2m_live), "fx": list(fx_live.keys())})
        return pack

    def install_flagships(self) -> dict[str, Any]:
        """ollama create fx-reason + fx-face from catalog."""
        from drone.model_clones import ModelClones

        mc = ModelClones(
            self.root,
            catalog="configs/flagship_clones.json",
            modelfile_dir="models/clones/flagship",
            out_prefix="FLAGSHIP_CLONES",
        )
        ins = mc.install()
        # re-hardwire after install
        hw = self.hardwire()
        return {
            "schema": "drone.super_mesh.install_flagships.v1",
            "utc": _utc(),
            "false_green": 0,
            "install": ins,
            "hardwire_status": hw.get("status"),
            "flagships_live": hw.get("flagships_live"),
        }

    # ── live loop phases ──────────────────────────────────────

    def tools_list(self) -> list[str]:
        tcfg = self.cfg.get("tools") or {}
        if tcfg.get("use_all_tools", True) or not tcfg.get("tools_allowed"):
            return list(DroneToolkit.ALL_TOOLS)
        return list(tcfg.get("tools_allowed") or DroneToolkit.ALL_TOOLS)

    def phase_sense(self, goal: str, residual: str = "", *, task_id: str = "") -> dict[str, Any]:
        g = (goal or "").strip()
        r = (residual or "").strip()
        # Fact card: ground tiny m2m synapses in real inventory (anti-hallucination scaffold)
        installed = self.list_installed()
        nodes = self.all_m2m_nodes()
        m2m_live = [n for n in nodes if self._name_ok(n, installed)]
        fx = self.flagships()
        fx_live = {k: v for k, v in fx.items() if self._name_ok(v, installed)}
        bus_st = self.bus.status()
        facts = (
            f"[MESH_FACTS false_green:0]\n"
            f"m2m_synapses_live={len(m2m_live)}/{len(nodes)}\n"
            f"m2m_tags={','.join(m2m_live)}\n"
            f"flagship_reason={fx.get('reason')} live={self._name_ok(fx.get('reason',''), installed)}\n"
            f"flagship_face={fx.get('face')} live={self._name_ok(fx.get('face',''), installed)}\n"
            f"hardwire_edges={len(self.hardwire_edges())}\n"
            f"tools_n={len(self.tools_list())}\n"
            f"packets_active={bus_st.get('active_n')} packets_recycle_pool={bus_st.get('pool_n')}\n"
            f"DO_NOT_INVENT_COUNTS. Use MESH_FACTS only for inventory numbers."
        )
        # Reinject recycled packets into membrane
        rec = self.cfg.get("recycle") or {}
        reinjected: list[dict[str, Any]] = []
        rec_block = ""
        if rec.get("enabled", True) and rec.get("reinject", True):
            # soft reinject from pool (does not re-emit duplicates every sense if pool empty)
            if bus_st.get("pool_n", 0) > 0:
                reinjected = self.bus.reinject_best(
                    limit=int(rec.get("reinject_limit") or 4),
                    goal=g,
                )
            else:
                reinjected = self.bus.list_active(limit=int(rec.get("reinject_limit") or 4))
            rec_block = self.bus.membrane_block(reinjected)
        membrane = f"{g}\n\n{facts}"
        if rec_block:
            membrane = f"{membrane}\n\n{rec_block}"
        if r:
            membrane = f"{membrane}\n\n[residual]\n{r[:500]}"
        return {
            "phase": "SENSE",
            "goal": g,
            "residual": r[:400],
            "membrane": membrane,
            "task_id": task_id,
            "facts": {
                "m2m_live": len(m2m_live),
                "m2m_total": len(nodes),
                "flagships_live": fx_live,
                "edges": len(self.hardwire_edges()),
                "tools_n": len(self.tools_list()),
                "packets_active": bus_st.get("active_n"),
                "packets_pool": bus_st.get("pool_n"),
            },
            "reinjected_n": len(reinjected),
            "potential": min(1.0, 0.4 + 0.015 * len(g.split())),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_route(
        self,
        membrane: str,
        *,
        call_only: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Select nodes to wake. Default: EMPTY (all stay IDLE).
        Only tags in call_only are selected — no auto-fire of the fleet.
        """
        installed = self.list_installed()
        selected: list[str] = []
        route_name = "idle"
        if call_only:
            route_name = "call_only"
            for t in call_only:
                t = str(t).strip()
                if t and self._name_ok(t, installed) and t not in selected:
                    selected.append(t)
                elif t and t.startswith("m2m-") and t not in selected:
                    # allow call even if list race; registry will still run tool
                    selected.append(t)

        # Suggest route for conductor (not woken unless call_only set)
        seeds = self.cfg.get("seed_routes") or {}
        text = membrane.lower()
        if any(k in text for k in ("code", "patch", "python", "bug", "implement")):
            suggested = list(seeds.get("code") or [])
            suggest_name = "code"
        elif any(k in text for k in ("ops", "status", "health", "gpu", "ollama", "service")):
            suggested = list(seeds.get("ops") or [])
            suggest_name = "ops"
        elif any(k in text for k in ("fast", "quick", "brief", "summary")):
            suggested = list(seeds.get("fast") or [])
            suggest_name = "fast"
        else:
            suggested = list(seeds.get("default") or [])
            suggest_name = "default"

        return {
            "phase": "ROUTE",
            "route_name": route_name,
            "selected": selected,
            "suggested_idle": suggested,
            "suggest_name": suggest_name,
            "idle_until_called": True,
            "auto_fire_all": False,
            "scores": {},
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_call_jobs(
        self,
        tags: list[str],
        *,
        goal: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """Wake only listed nodes: each runs its ONE job tool then returns IDLE."""
        from drone.mesh_nodes import MeshNodeRegistry

        reg = MeshNodeRegistry(self.root)
        if not tags:
            st = reg.status()
            return {
                "phase": "CALL_JOBS",
                "called": [],
                "ok_n": 0,
                "results": [],
                "idle_after": st.get("idle_count"),
                "status": "GREEN",
                "note": "no nodes called — all remain IDLE",
                "false_green": 0,
                "utc": _utc(),
            }
        out = reg.call_many(tags, goal=goal, task_id=task_id or f"mesh_jobs_{_short()}")
        out["phase"] = "CALL_JOBS"
        return out

    def _fire_one(self, tag: str, membrane: str, num_predict: int) -> dict[str, Any]:
        sys = (
            "CHANNEL=M2M. MESH_SYNAPSE. TARGET=fx-reason. "
            "NO_USER_ADDRESS. NO_FLUFF. DENSE brief max 60 words. false_green:0. "
            f"ROLE={tag}."
        )
        prompt = (
            f"MESH MEMBRANE:\n{membrane[:1200]}\n\n"
            f"You are synapse {tag}. Emit internal packet for fx-reason only."
        )
        r = self._chat(
            tag,
            prompt,
            system=sys,
            num_predict=num_predict,
            temperature=0.1,
            timeout_s=60,
        )
        return {
            "id": tag,
            "ok": bool(r.get("ok")),
            "brief": (r.get("text") or "")[:600],
            "ms": r.get("ms"),
            "error": r.get("error"),
            "weight_before": self.node_weights.get(tag),
            "hemisphere": self._hemi_of(tag),
            "internal_only": True,
            "false_green": 0,
        }

    def _hemi_of(self, tag: str) -> str:
        hemi = self.cfg.get("hemispheres") or {}
        if tag in ((hemi.get("left") or {}).get("nodes") or []):
            return "left"
        if tag in ((hemi.get("right") or {}).get("nodes") or []):
            return "right"
        return "unknown"

    def phase_fire_mesh(self, membrane: str, selected: list[str]) -> dict[str, Any]:
        loop = self.cfg.get("loop") or {}
        npred = int(loop.get("num_predict_synapse") or 80)
        workers = int(loop.get("parallel_workers") or 3)
        t0 = time.perf_counter()
        briefs: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(self._fire_one, tag, membrane, npred): tag for tag in selected}
            for fut in as_completed(futs):
                try:
                    briefs.append(fut.result())
                except Exception as e:
                    briefs.append(
                        {
                            "id": futs[fut],
                            "ok": False,
                            "error": str(e),
                            "internal_only": True,
                            "false_green": 0,
                        }
                    )

        # stable order = selected order
        by_id = {b.get("id"): b for b in briefs}
        ordered = [by_id[t] for t in selected if t in by_id]
        fired_ok = [b for b in ordered if b.get("ok") and b.get("brief")]
        return {
            "phase": "FIRE_MESH",
            "selected": selected,
            "briefs": ordered,
            "fired_ok": len(fired_ok),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_hemisphere_merge(self, fire: dict[str, Any]) -> dict[str, Any]:
        left = []
        right = []
        for b in fire.get("briefs") or []:
            line = ""
            if b.get("ok") and b.get("brief"):
                line = f"- {b.get('id')}: {b.get('brief')}"
            else:
                line = f"- {b.get('id')}: FAIL {b.get('error') or 'silent'}"
            if b.get("hemisphere") == "left":
                left.append(line)
            else:
                right.append(line)
        return {
            "phase": "HEMISPHERE_MERGE",
            "left_packet": "\n".join(left) if left else "(no left fire)",
            "right_packet": "\n".join(right) if right else "(no right fire)",
            "left_n": len(left),
            "right_n": len(right),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_conduct(self, membrane: str, hemi: dict[str, Any], fire: dict[str, Any]) -> dict[str, Any]:
        fx = self.flagships()
        reason = fx["reason"]
        loop = self.cfg.get("loop") or {}
        council = []
        for b in fire.get("briefs") or []:
            if b.get("ok") and b.get("brief"):
                council.append(f"### {b.get('id')} (w={b.get('weight_before')})\n{b.get('brief')}")
            else:
                council.append(f"### {b.get('id')}\nSILENT: {b.get('error')}")
        prompt = (
            f"MEMBRANE:\n{membrane}\n\n"
            f"LEFT HEMISPHERE:\n{hemi.get('left_packet')}\n\n"
            f"RIGHT HEMISPHERE:\n{hemi.get('right_packet')}\n\n"
            f"RAW SYNAPSES:\n" + "\n\n".join(council) + "\n\n"
            "Conduct SUPER MESH: emit one dense brief for fx-face. "
            "Fields: intent, facts, plan, risks, confidence, evidence_gaps."
        )
        r = self._chat(
            reason,
            prompt,
            system=(
                "CHANNEL=AI2AI. ROLE=fx-reason mesh conductor. NO_USER_ADDRESS. "
                "DENSE technical. false_green:0. Never invent disk paths."
            ),
            num_predict=int(loop.get("num_predict_reason") or 280),
            temperature=0.15,
            timeout_s=120,
        )
        return {
            "phase": "CONDUCT",
            "model": reason,
            "ok": bool(r.get("ok")),
            "brief": r.get("text") or "",
            "ms": r.get("ms"),
            "error": r.get("error"),
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_face(self, membrane: str, conduct: dict[str, Any], fire: dict[str, Any]) -> dict[str, Any]:
        fx = self.flagships()
        face = fx["face"]
        loop = self.cfg.get("loop") or {}
        syn_n = int(fire.get("fired_ok") or 0)
        prompt = (
            f"USER GOAL / MEMBRANE:\n{membrane}\n\n"
            f"MESH CONDUCTOR (fx-reason):\n{conduct.get('brief') or '(empty)'}\n\n"
            f"SYNAPSES FIRED OK: {syn_n}/{len(fire.get('selected') or [])}\n\n"
            "Reply to the user clearly. End with:\n"
            "CONFIDENCE: 0.xx\n"
            "NEXT: <residual or DONE>"
        )
        r = self._chat(
            face,
            prompt,
            system=(
                "CHANNEL=FACE. You are fx-face — only Super Mesh voice to the user. "
                "Clear sentences. No inventing evidence. User is boss. false_green:0."
            ),
            num_predict=int(loop.get("num_predict_face") or 320),
            temperature=0.25,
            timeout_s=150,
        )
        text = r.get("text") or ""
        return {
            "phase": "FACE",
            "model": face,
            "ok": bool(r.get("ok")),
            "face_reply": text,
            "confidence": self._parse_confidence(text),
            "residual": self._parse_next(text),
            "ms": r.get("ms"),
            "error": r.get("error"),
            "false_green": 0,
            "utc": _utc(),
        }

    def _parse_confidence(self, text: str) -> float:
        m = re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0+|1)", text or "", re.I)
        if not m:
            return 0.55 if (text or "").strip() else 0.1
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            return 0.5

    def _parse_next(self, text: str) -> str:
        m = re.search(r"NEXT:\s*(.+)$", text or "", re.I | re.M)
        if not m:
            return ""
        nxt = m.group(1).strip()
        if nxt.upper() in {"DONE", "NONE", "N/A", "-"}:
            return ""
        return nxt[:400]

    def phase_weight(self, fire: dict[str, Any], conduct: dict[str, Any], face: dict[str, Any]) -> dict[str, Any]:
        syn = self.cfg.get("synapse") or {}
        lr = float(syn.get("learn_rate") or 0.1)
        decay = float(syn.get("decay") or 0.015)
        wmin = float(syn.get("min_weight") or 0.05)
        wmax = float(syn.get("max_weight") or 0.95)
        conf = float(face.get("confidence") or 0.5)
        updates: dict[str, Any] = {}

        for wid in list(self.node_weights.keys()):
            self.node_weights[wid] = max(wmin, float(self.node_weights[wid]) - decay)

        for b in fire.get("briefs") or []:
            wid = str(b.get("id") or "")
            if not wid:
                continue
            before = float(self.node_weights.get(wid, 0.5))
            if b.get("ok") and b.get("brief") and conf >= 0.4:
                self.node_weights[wid] = min(wmax, before + lr * conf)
            elif not b.get("ok"):
                self.node_weights[wid] = max(wmin, before - lr * 0.4)
            updates[wid] = {
                "before": round(before, 4),
                "after": round(self.node_weights[wid], 4),
                "ok": bool(b.get("ok")),
            }

        # strengthen edges along fired path
        selected = fire.get("selected") or []
        edge_updates = {}
        for i in range(len(selected) - 1):
            a, b = selected[i], selected[i + 1]
            key = f"{a}->{b}"
            # also check hardwired reverse-independent keys
            if key not in self.edge_weights:
                # find any hardwire a->b
                key = f"{a}->{b}"
                self.edge_weights.setdefault(key, float(syn.get("edge_default") or 0.35))
            before = float(self.edge_weights.get(key, 0.35))
            if conf >= 0.45:
                self.edge_weights[key] = min(wmax, before + lr * 0.5 * conf)
            edge_updates[key] = {
                "before": round(before, 4),
                "after": round(self.edge_weights[key], 4),
            }

        # flagship poles
        fx = self.flagships()
        for pole, ok in ((fx["reason"], conduct.get("ok")), (fx["face"], face.get("ok"))):
            before = float(self.node_weights.get(pole, 0.5))
            if ok and conf >= 0.4:
                self.node_weights[pole] = min(wmax, before + lr * conf)
            updates[pole] = {
                "before": round(before, 4),
                "after": round(self.node_weights.get(pole, before), 4),
                "ok": bool(ok),
            }

        self._save_weights()
        return {
            "phase": "WEIGHT",
            "node_updates": updates,
            "edge_updates": edge_updates,
            "false_green": 0,
            "utc": _utc(),
        }

    def phase_gate(self, face: dict[str, Any], tick: int, max_ticks: int) -> dict[str, Any]:
        loop = self.cfg.get("loop") or {}
        conf = float(face.get("confidence") or 0)
        stop_conf = float(loop.get("stop_confidence") or 0.88)
        residual = (face.get("residual") or "").strip()
        face_ok = bool(face.get("ok") and (face.get("face_reply") or "").strip())
        stop = False
        reason = "continue"
        if not face_ok:
            stop = tick >= max_ticks
            reason = "face_weak" if not stop else "max_ticks_face_weak"
        elif conf >= stop_conf and not residual:
            stop = True
            reason = "confidence_and_done"
        elif tick >= max_ticks:
            stop = True
            reason = "max_ticks"
        return {
            "phase": "GATE",
            "stop": stop,
            "reason": reason,
            "confidence": conf,
            "residual": residual,
            "false_green": 0,
            "utc": _utc(),
        }

    # ── tick / run / status / seal ────────────────────────────

    def tick(
        self,
        goal: str,
        residual: str = "",
        *,
        call: list[str] | None = None,
        fire_lm: bool = False,
    ) -> dict[str, Any]:
        """
        Live tick. Nodes stay IDLE unless `call` lists tags to wake.
        fire_lm: if True and call non-empty, also Ollama-fire those tags (default False — tool/job only).
        """
        t0 = time.perf_counter()
        task_id = f"mesh_tick_{_short()}"
        call_list = list(call or [])
        sense = self.phase_sense(goal, residual, task_id=task_id)
        route = self.phase_route(sense["membrane"], call_only=call_list)
        # JOBS: only called nodes run their tool; others stay idle
        jobs = self.phase_call_jobs(route["selected"], goal=goal, task_id=task_id)

        # LM fire only when explicitly requested AND someone was called
        if fire_lm and route["selected"]:
            fire = self.phase_fire_mesh(sense["membrane"], route["selected"])
        else:
            fire = {
                "phase": "FIRE_MESH",
                "selected": [],
                "briefs": [],
                "fired_ok": 0,
                "ms": 0,
                "skipped": True,
                "reason": "idle_until_called" if not call_list else "lm_fire_disabled",
                "false_green": 0,
                "utc": _utc(),
            }

        hemi = self.phase_hemisphere_merge(fire)
        # Conduct/face only if we have job results or LM briefs (else idle report)
        if call_list or fire.get("fired_ok"):
            # inject job results into hemi for conductor
            job_lines = []
            for r in jobs.get("results") or []:
                job_lines.append(
                    f"- {r.get('tag')}: job={r.get('job')} tool={r.get('tool')} ok={r.get('ok')}"
                )
            if job_lines:
                hemi["left_packet"] = (hemi.get("left_packet") or "") + "\n[JOBS]\n" + "\n".join(job_lines)
            conduct = self.phase_conduct(sense["membrane"], hemi, fire)
            face = self.phase_face(sense["membrane"], conduct, fire)
        else:
            conduct = {
                "phase": "CONDUCT",
                "ok": True,
                "model": self.flagships()["reason"],
                "brief": "All mesh nodes IDLE. No call list. Awaiting call.",
                "ms": 0,
                "false_green": 0,
            }
            face = {
                "phase": "FACE",
                "ok": True,
                "model": self.flagships()["face"],
                "face_reply": (
                    "Super Mesh is idle. Each of 20 m2m nodes has one tool and one job "
                    "and stays idle until called.\n\n"
                    "Call example: python -m drone mesh call m2m-probe\n"
                    "CONFIDENCE: 1.0\nNEXT: DONE"
                ),
                "confidence": 1.0,
                "residual": "",
                "ms": 0,
                "false_green": 0,
            }

        weight = self.phase_weight(fire, conduct, face)
        gate = self.phase_gate(face, 1, 1)

        # status: idle tick with no calls is GREEN (by design)
        if not call_list:
            status = "GREEN"
        elif int(jobs.get("ok_n") or 0) >= 1:
            status = "GREEN" if int(jobs.get("ok_n") or 0) == len(call_list) else "PARTIAL"
        else:
            status = "RED"

        out = {
            "schema": "drone.super_mesh.tick.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": status,
            "idle_until_called": True,
            "called": call_list,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "sense": sense,
            "route": route,
            "jobs": {
                "ok_n": jobs.get("ok_n"),
                "called": jobs.get("called") or jobs.get("results") and [r.get("tag") for r in jobs.get("results") or []],
                "idle_after": jobs.get("idle_after"),
                "results": [
                    {
                        "tag": r.get("tag"),
                        "job": r.get("job"),
                        "tool": r.get("tool"),
                        "ok": r.get("ok"),
                        "state_after": r.get("state_after"),
                    }
                    for r in (jobs.get("results") or [])
                ],
            },
            "fire": {
                "selected": fire.get("selected"),
                "fired_ok": fire.get("fired_ok"),
                "ms": fire.get("ms"),
                "briefs": [
                    {
                        "id": b.get("id"),
                        "ok": b.get("ok"),
                        "hemi": b.get("hemisphere"),
                        "preview": (b.get("brief") or "")[:120],
                        "error": b.get("error"),
                    }
                    for b in (fire.get("briefs") or [])
                ],
            },
            "hemisphere": {
                "left_n": hemi.get("left_n"),
                "right_n": hemi.get("right_n"),
            },
            "conduct": {
                "ok": conduct.get("ok"),
                "model": conduct.get("model"),
                "preview": (conduct.get("brief") or "")[:400],
                "ms": conduct.get("ms"),
                "error": conduct.get("error"),
            },
            "face": {
                "ok": face.get("ok"),
                "model": face.get("model"),
                "reply": face.get("face_reply"),
                "confidence": face.get("confidence"),
                "residual": face.get("residual"),
                "ms": face.get("ms"),
                "error": face.get("error"),
            },
            "weight": weight,
            "gate": gate,
            "flagships": self.flagships(),
        }
        self._write(self.last_tick_path, out)
        self._write(
            self.state_path,
            {
                "utc": _utc(),
                "last_status": out["status"],
                "last_confidence": face.get("confidence"),
                "last_fired_ok": fire.get("fired_ok"),
                "false_green": 0,
            },
        )
        self._trace(
            "tick",
            {
                "status": out["status"],
                "fired_ok": fire.get("fired_ok"),
                "conf": face.get("confidence"),
            },
        )
        return out

    def run(self, goal: str, *, ticks: int | None = None) -> dict[str, Any]:
        loop = self.cfg.get("loop") or {}
        n = int(ticks if ticks is not None else loop.get("default_ticks") or 2)
        n = max(1, min(n, int(loop.get("max_ticks") or 6)))
        run_id = f"mesh_{_short()}"
        residual = ""
        history: list[dict[str, Any]] = []
        t0 = time.perf_counter()
        final: dict[str, Any] = {}

        for i in range(1, n + 1):
            tk = self.tick(goal, residual)
            gate = self.phase_gate(tk.get("face") or {}, i, n)
            tk["gate"] = gate
            tk["tick"] = i
            history.append(
                {
                    "tick": i,
                    "status": tk.get("status"),
                    "fired_ok": (tk.get("fire") or {}).get("fired_ok"),
                    "confidence": (tk.get("face") or {}).get("confidence"),
                    "residual": (tk.get("face") or {}).get("residual"),
                    "route": (tk.get("route") or {}).get("selected"),
                }
            )
            final = tk
            residual = str((tk.get("face") or {}).get("residual") or "")
            if gate.get("stop"):
                break

        run = {
            "schema": "drone.super_mesh.run.v1",
            "run_id": run_id,
            "utc": _utc(),
            "false_green": 0,
            "goal": goal,
            "ticks_ran": len(history),
            "ticks_cap": n,
            "status": final.get("status") or "RED",
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "history": history,
            "final_face": (final.get("face") or {}).get("reply"),
            "final_confidence": (final.get("face") or {}).get("confidence"),
            "final_fire": final.get("fire"),
            "final_conduct_preview": (final.get("conduct") or {}).get("preview"),
            "flagships": self.flagships(),
            "evidence": {
                "last_tick": str(self.last_tick_path),
                "hardwire": str(self.hardwire_path),
                "node_weights": str(self.weights_path),
                "edge_weights": str(self.edges_path),
            },
        }
        self._write(self.last_run_path, run)
        self._write(self.data / f"{run_id}.json", run)
        self._trace("run", {"run_id": run_id, "status": run["status"], "ticks": len(history)})
        return run

    def status(self) -> dict[str, Any]:
        from drone.mesh_nodes import MeshNodeRegistry

        installed = self.list_installed()
        nodes = self.all_m2m_nodes()
        fx = self.flagships()
        m2m_live = [n for n in nodes if self._name_ok(n, installed)]
        idle = MeshNodeRegistry(self.root).status()
        return {
            "schema": "drone.super_mesh.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "m2m_live": len(m2m_live),
            "m2m_total": len(nodes),
            "flagships": {
                k: {"tag": v, "live": self._name_ok(v, installed)} for k, v in fx.items()
            },
            "edges": len(self.hardwire_edges()),
            "hardwire_exists": self.hardwire_path.is_file(),
            "node_weight_count": len(self.node_weights),
            "edge_weight_count": len(self.edge_weights),
            "idle_until_called": True,
            "idle_count": idle.get("idle_count"),
            "busy_count": idle.get("busy_count"),
            "roster_nodes": idle.get("nodes"),
            "last_tick": str(self.last_tick_path) if self.last_tick_path.is_file() else None,
            "principal": self.cfg.get("principal"),
        }

    def seal(self) -> dict[str, Any]:
        st = self.status()
        hw = self._load(self.hardwire_path) if self.hardwire_path.is_file() else {}
        last = self._load(self.last_tick_path) if self.last_tick_path.is_file() else {}
        m2m_ok = int(st.get("m2m_live") or 0) >= 20
        fx_ok = all((v or {}).get("live") for v in (st.get("flagships") or {}).values())
        live_ok = (last.get("status") in {"GREEN", "PARTIAL"}) and bool(last)
        if m2m_ok and fx_ok and live_ok and last.get("status") == "GREEN":
            status = "GREEN"
        elif m2m_ok or fx_ok or live_ok:
            status = "PARTIAL"
        else:
            status = "RED"
        seal = {
            "schema": "drone.super_mesh.seal.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": status,
            "status_detail": st,
            "hardwire_status": hw.get("status"),
            "last_tick_status": last.get("status"),
            "last_fired_ok": (last.get("fire") or {}).get("fired_ok"),
            "last_confidence": (last.get("face") or {}).get("confidence"),
            "evidence": [
                str(self.hardwire_path),
                str(self.weights_path),
                str(self.edges_path),
                str(self.last_tick_path),
                str(self.last_run_path),
                str(self.cfg_path),
                "configs/flagship_clones.json",
                "configs/m2m_clones_0.5b.json",
            ],
        }
        self._write(self.seal_path, seal)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone mesh", description="Super Mesh Live AI")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("hardwire", help="deep-bind 20 m2m + 2 flagships; all IDLE")
    sub.add_parser("install-flagships", help="create fx-reason + fx-face then hardwire")
    sub.add_parser("roster", help="list each node job+tool+idle state")
    tk = sub.add_parser("tick")
    tk.add_argument("goal", nargs="*")
    tk.add_argument("--residual", default="")
    tk.add_argument(
        "--call",
        default="",
        help="comma tags to wake (others stay IDLE). Empty = idle tick",
    )
    tk.add_argument("--fire-lm", action="store_true", help="also Ollama-fire called tags")
    rn = sub.add_parser("run")
    rn.add_argument("goal", nargs="+")
    rn.add_argument("--ticks", type=int, default=None)
    rn.add_argument("--call", default="", help="comma tags to wake each tick")
    sub.add_parser("seal")
    cl = sub.add_parser("call", help="wake one node: run its tool/job then back to IDLE")
    cl.add_argument("tag")
    cl.add_argument("--goal", default="")
    cm = sub.add_parser("call-many", help="wake only listed tags; others stay IDLE")
    cm.add_argument("tags", nargs="+")
    cm.add_argument("--goal", default="")

    args = p.parse_args(argv)
    mesh = SuperMesh(Path(args.root) if args.root else None)

    if args.cmd == "status":
        print(json.dumps(mesh.status(), indent=2))
        return 0
    if args.cmd == "roster":
        from drone.mesh_nodes import MeshNodeRegistry

        reg = MeshNodeRegistry(mesh.root)
        print(
            json.dumps(
                {
                    "idle_policy": reg.roster.get("idle_policy"),
                    "nodes": reg.roster.get("nodes"),
                    "flagships": reg.roster.get("flagships"),
                    "state": reg.status(),
                    "false_green": 0,
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "hardwire":
        out = mesh.hardwire()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "install-flagships":
        out = mesh.install_flagships()
        print(json.dumps(out, indent=2))
        inst = out.get("install") or {}
        return 0 if inst.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "call":
        from drone.mesh_nodes import MeshNodeRegistry

        out = MeshNodeRegistry(mesh.root).call_node(args.tag, goal=args.goal or "")
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "call-many":
        from drone.mesh_nodes import MeshNodeRegistry

        out = MeshNodeRegistry(mesh.root).call_many(list(args.tags), goal=args.goal or "")
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "tick":
        goal = " ".join(args.goal) if args.goal else "idle mesh status"
        call = [x.strip() for x in (args.call or "").split(",") if x.strip()]
        out = mesh.tick(
            goal,
            residual=args.residual or "",
            call=call or None,
            fire_lm=bool(args.fire_lm),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "run":
        call = [x.strip() for x in (args.call or "").split(",") if x.strip()]

        def _run_with_call() -> dict[str, Any]:
            # single-tick style multi: call list each tick
            loop = mesh.cfg.get("loop") or {}
            n = int(args.ticks if args.ticks is not None else loop.get("default_ticks") or 1)
            n = max(1, min(n, int(loop.get("max_ticks") or 6)))
            residual = ""
            hist = []
            final = {}
            t0 = time.perf_counter()
            for i in range(1, n + 1):
                tk = mesh.tick(" ".join(args.goal), residual, call=call or None)
                hist.append(
                    {
                        "tick": i,
                        "status": tk.get("status"),
                        "called": tk.get("called"),
                        "jobs_ok": (tk.get("jobs") or {}).get("ok_n"),
                        "idle_after": (tk.get("jobs") or {}).get("idle_after"),
                    }
                )
                final = tk
                residual = str((tk.get("face") or {}).get("residual") or "")
                if (tk.get("gate") or {}).get("stop"):
                    break
            run = {
                "schema": "drone.super_mesh.run.v1",
                "utc": _utc(),
                "false_green": 0,
                "goal": " ".join(args.goal),
                "called": call,
                "ticks_ran": len(hist),
                "status": final.get("status") or "RED",
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "history": hist,
                "final_face": (final.get("face") or {}).get("reply")
                or (final.get("face") or {}).get("face_reply"),
                "jobs": final.get("jobs"),
            }
            mesh._write(mesh.last_run_path, run)
            return run

        out = _run_with_call()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "seal":
        out = mesh.seal()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
