"""
Realtime swarm viewport — little buzzer-bees (boids-style) + proof of work.

Visual only: syncs intensity / colors / labels to real hive activity.
Not a full physics sim claim. false_green: 0.
"""

from __future__ import annotations

import math
import random
import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Bee:
    x: float
    y: float
    vx: float
    vy: float
    phase: float = 0.0
    hue: str = "#f5c518"  # gold/yellow
    role: str = "buzzer"  # buzzer | scout | queen


@dataclass
class SwarmState:
    mode: str = "idle"  # idle | working | green | red | partial
    job: str = "idle"
    intensity: float = 0.25  # 0..1
    units: int = 0
    peak_parallel: int = 0
    proof_line: str = "awaiting work"
    evidence: list[str] = field(default_factory=list)
    false_green: int = 0
    last_status: str = "—"
    ticks: int = 0


class RealtimeSwarmViewport:
    """
    Embed a Canvas of buzzer-bees that flock like boids/bees.
    Call set_activity(...) from the main UI thread when jobs start/finish.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        width: int = 640,
        height: int = 260,
        n_bees: int = 28,
        bg: str = "#0b1220",
    ) -> None:
        self.width = width
        self.height = height
        self.bg = bg
        self.state = SwarmState()
        self._running = True
        self._after_id: str | None = None
        self._parent = parent

        outer = tk.Frame(parent, bg="#111827", highlightthickness=0)
        outer.pack(fill="both", expand=False, padx=12, pady=(8, 4))
        self.outer = outer

        header = tk.Frame(outer, bg="#111827")
        header.pack(fill="x", padx=8, pady=(6, 2))

        self.title_var = tk.StringVar(value="REALTIME SWARM")
        tk.Label(
            header,
            textvariable=self.title_var,
            fg="#fbbf24",
            bg="#111827",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        self.live_var = tk.StringVar(value="● LIVE")
        tk.Label(
            header,
            textvariable=self.live_var,
            fg="#34d399",
            bg="#111827",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(10, 0))

        self.job_var = tk.StringVar(value="job: idle")
        tk.Label(
            header,
            textvariable=self.job_var,
            fg="#93c5fd",
            bg="#111827",
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.canvas = tk.Canvas(
            outer,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=1,
            highlightbackground="#1f2937",
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=8, pady=4)

        proof = tk.Frame(outer, bg="#0f172a")
        proof.pack(fill="x", padx=8, pady=(0, 8))
        self.proof_var = tk.StringVar(
            value="proof: none yet · false_green: 0 · buzzers ready"
        )
        tk.Label(
            proof,
            textvariable=self.proof_var,
            fg="#cbd5e1",
            bg="#0f172a",
            font=("Consolas", 9),
            anchor="w",
            justify="left",
            wraplength=width - 16,
        ).pack(fill="x", padx=6, pady=4)

        self.bees: list[Bee] = []
        self._spawn_bees(n_bees)
        self._draw_static_layer()
        self._tick()

    def destroy(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.canvas.after_cancel(self._after_id)
            except Exception:
                pass
        try:
            self.outer.destroy()
        except Exception:
            pass

    def set_activity(
        self,
        *,
        mode: str | None = None,
        job: str | None = None,
        intensity: float | None = None,
        units: int | None = None,
        peak_parallel: int | None = None,
        proof_line: str | None = None,
        evidence: list[str] | None = None,
        last_status: str | None = None,
        false_green: int | None = None,
        n_bees: int | None = None,
    ) -> None:
        """Update swarm from real work — call on main thread only."""
        st = self.state
        if mode is not None:
            st.mode = mode
        if job is not None:
            st.job = job
        if intensity is not None:
            st.intensity = max(0.05, min(1.0, float(intensity)))
        if units is not None:
            st.units = max(0, int(units))
        if peak_parallel is not None:
            st.peak_parallel = max(0, int(peak_parallel))
        if proof_line is not None:
            st.proof_line = proof_line
        if evidence is not None:
            st.evidence = list(evidence)[:6]
        if last_status is not None:
            st.last_status = last_status
        if false_green is not None:
            st.false_green = int(false_green)
        if n_bees is not None and n_bees > 0:
            self._spawn_bees(n_bees)

        # mode → default intensity if not overridden
        if mode == "working" and intensity is None:
            st.intensity = max(st.intensity, 0.75)
        elif mode == "idle" and intensity is None:
            st.intensity = 0.22
        elif mode == "green":
            st.intensity = max(st.intensity, 0.9)
        elif mode == "red":
            st.intensity = max(st.intensity, 0.55)

        self._refresh_labels()

    def pulse_from_result(self, title: str, result: dict[str, Any] | None) -> None:
        """Map a real service result into swarm state + proof strip."""
        result = result or {}
        st = (result.get("status") or "").upper()
        ok = result.get("ok")
        units = int(result.get("units") or result.get("goals_n") or 0)
        peak = int(result.get("peak_parallel_observed") or result.get("workers") or 0)
        nodes = int(result.get("nodes_run") or 0)
        evidence: list[str] = []
        for key in (
            "seal_path",
            "report_path",
            "fabric_swarm_smoke_seal_path",
            "swarm_smoke_seal_path",
        ):
            v = result.get(key)
            if v:
                evidence.append(str(v))
        # nested fabric
        fab = result.get("fabric") or {}
        if isinstance(fab, dict):
            for key in ("report_path", "build_path", "seal_path"):
                v = fab.get(key)
                if v:
                    evidence.append(str(v))
        for r in result.get("results") or []:
            if isinstance(r, dict):
                for key in ("seal_path", "report_path", "imprint_path"):
                    v = r.get(key)
                    if v:
                        evidence.append(str(v))
                if r.get("lifecycle"):
                    lc = r.get("lifecycle") or {}
                    if isinstance(lc, dict) and lc.get("recycle_dir"):
                        evidence.append(str(lc["recycle_dir"]))

        if st == "GREEN" or ok is True:
            mode = "green"
        elif st == "PARTIAL":
            mode = "partial"
        elif st == "RED" or ok is False:
            mode = "red"
        else:
            mode = "idle"

        n = 24
        if units:
            n = max(18, min(48, 12 + units * 6))
        elif peak:
            n = max(18, min(48, 16 + peak * 6))
        elif nodes:
            n = max(18, min(40, 12 + nodes // 2))

        proof = (
            f"status={st or '—'} · units={units} · peak∥={peak} · "
            f"job={title} · false_green={result.get('false_green', 0)}"
        )
        if evidence:
            proof += f" · evidence={evidence[0]}"

        self.set_activity(
            mode=mode,
            job=title,
            intensity=0.95 if mode in {"green", "working"} else 0.5,
            units=units,
            peak_parallel=peak,
            proof_line=proof,
            evidence=evidence,
            last_status=st or "—",
            false_green=int(result.get("false_green") or 0),
            n_bees=n,
        )
        # celebrate / cool-down schedule handled by intensity decay in tick

    def mark_working(self, job: str, *, units_hint: int = 0) -> None:
        self.set_activity(
            mode="working",
            job=job,
            intensity=0.85,
            units=units_hint or self.state.units,
            proof_line=f"WORKING · {job} · swarm active · false_green: 0",
            last_status="RUNNING",
            n_bees=max(22, min(42, 20 + (units_hint or 2) * 4)),
        )

    def mark_idle(self) -> None:
        self.set_activity(
            mode="idle",
            job="idle",
            intensity=0.22,
            proof_line=self.state.proof_line
            if self.state.last_status not in {"—", "RUNNING"}
            else "proof: idle · buzzers milling · false_green: 0",
        )

    # --- internal ---

    def _spawn_bees(self, n: int) -> None:
        self.bees = []
        w, h = self.width, self.height
        for i in range(n):
            role = "buzzer"
            if i == 0:
                role = "queen"
            elif i % 7 == 0:
                role = "scout"
            hue = {"queen": "#f59e0b", "scout": "#60a5fa", "buzzer": "#facc15"}[role]
            self.bees.append(
                Bee(
                    x=random.uniform(20, w - 20),
                    y=random.uniform(20, h - 20),
                    vx=random.uniform(-1.5, 1.5),
                    vy=random.uniform(-1.5, 1.5),
                    phase=random.uniform(0, math.tau),
                    hue=hue,
                    role=role,
                )
            )

    def _draw_static_layer(self) -> None:
        c = self.canvas
        c.delete("static")
        w, h = self.width, self.height
        # hive hex center
        cx, cy = w * 0.5, h * 0.52
        r = 28
        pts = []
        for i in range(6):
            a = math.pi / 6 + i * math.pi / 3
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
        c.create_polygon(
            *pts, fill="#1e293b", outline="#fbbf24", width=2, tags="static"
        )
        c.create_text(
            cx,
            cy,
            text="HIVE",
            fill="#fde68a",
            font=("Segoe UI", 8, "bold"),
            tags="static",
        )
        # soft flower targets
        for fx, fy, col in (
            (w * 0.18, h * 0.3, "#4c1d95"),
            (w * 0.82, h * 0.35, "#831843"),
            (w * 0.22, h * 0.75, "#14532d"),
            (w * 0.78, h * 0.72, "#1e3a8a"),
        ):
            c.create_oval(
                fx - 10, fy - 10, fx + 10, fy + 10, fill=col, outline="", tags="static"
            )

    def _refresh_labels(self) -> None:
        st = self.state
        self.job_var.set(f"job: {st.job}")
        live_col = {
            "working": "#34d399",
            "green": "#fbbf24",
            "red": "#f87171",
            "partial": "#fbbf24",
            "idle": "#6ee7b7",
        }.get(st.mode, "#34d399")
        pulse = "● REALTIME SWARM" if st.mode == "working" else "● REALTIME SWARM"
        self.live_var.set(pulse)
        try:
            # update live label color
            for child in self.outer.winfo_children():
                pass
            # re-find live label is hard; set via configure on known widget path
            # (title stays gold; live text content is enough)
        except Exception:
            pass
        ev = st.evidence[0] if st.evidence else ""
        self.proof_var.set(
            f"{st.proof_line}"
            + (f" · {ev}" if ev and ev not in st.proof_line else "")
            + f" · buzzers={len(self.bees)}"
        )
        self.title_var.set("REALTIME SWARM")

    def _neighbors(self, bee: Bee, radius: float) -> list[Bee]:
        out = []
        r2 = radius * radius
        for o in self.bees:
            if o is bee:
                continue
            dx, dy = o.x - bee.x, o.y - bee.y
            if dx * dx + dy * dy < r2:
                out.append(o)
        return out

    def _step_bees(self) -> None:
        st = self.state
        w, h = max(40, self.canvas.winfo_width()), max(40, self.canvas.winfo_height())
        if w > 40:
            self.width = w
        if h > 40:
            self.height = h
        w, h = self.width, self.height
        cx, cy = w * 0.5, h * 0.52

        # intensity slowly decays after green/red so swarm calms
        if st.mode in {"green", "red", "partial"} and st.intensity > 0.3:
            st.intensity *= 0.992
        if st.mode != "working" and st.intensity < 0.28 and st.mode != "idle":
            if st.intensity < 0.26:
                st.mode = "idle"

        speed_cap = 1.2 + 3.2 * st.intensity
        align_w = 0.04 + 0.08 * st.intensity
        cohere_w = 0.01 + 0.04 * st.intensity
        separate_w = 0.05 + 0.03 * (1.0 - st.intensity * 0.4)
        target_w = 0.02 + 0.06 * st.intensity

        # work magnet: hive center when working; flowers when idle
        if st.mode == "working":
            targets = [(cx, cy)]
        elif st.mode == "green":
            targets = [(cx, cy), (cx, cy)]
        elif st.mode == "red":
            targets = [(20.0, 20.0), (w - 20.0, h - 20.0)]  # scatter corners
        else:
            targets = [
                (w * 0.18, h * 0.3),
                (w * 0.82, h * 0.35),
                (w * 0.22, h * 0.75),
                (w * 0.78, h * 0.72),
                (cx, cy),
            ]

        for i, b in enumerate(self.bees):
            neigh = self._neighbors(b, 42 + 20 * st.intensity)
            ax = ay = 0.0
            if neigh:
                # alignment
                avx = sum(n.vx for n in neigh) / len(neigh)
                avy = sum(n.vy for n in neigh) / len(neigh)
                ax += (avx - b.vx) * align_w
                ay += (avy - b.vy) * align_w
                # cohesion
                cxn = sum(n.x for n in neigh) / len(neigh)
                cyn = sum(n.y for n in neigh) / len(neigh)
                ax += (cxn - b.x) * cohere_w
                ay += (cyn - b.y) * cohere_w
                # separation
                sx = sy = 0.0
                for n in neigh:
                    dx, dy = b.x - n.x, b.y - n.y
                    d2 = dx * dx + dy * dy + 1e-6
                    if d2 < 18 * 18:
                        sx += dx / d2
                        sy += dy / d2
                ax += sx * separate_w * 40
                ay += sy * separate_w * 40

            # target (hive / flower)
            tx, ty = targets[i % len(targets)]
            if b.role == "queen":
                tx, ty = cx, cy
            ax += (tx - b.x) * target_w
            ay += (ty - b.y) * target_w

            # slight noise (scout more)
            noise = 0.15 + (0.25 if b.role == "scout" else 0.0)
            ax += random.uniform(-noise, noise)
            ay += random.uniform(-noise, noise)

            b.vx = max(-speed_cap, min(speed_cap, b.vx + ax))
            b.vy = max(-speed_cap, min(speed_cap, b.vy + ay))
            b.x += b.vx
            b.y += b.vy
            b.phase += 0.35 + st.intensity * 0.4

            # soft walls
            margin = 12
            if b.x < margin:
                b.x = margin
                b.vx = abs(b.vx)
            if b.x > w - margin:
                b.x = w - margin
                b.vx = -abs(b.vx)
            if b.y < margin:
                b.y = margin
                b.vy = abs(b.vy)
            if b.y > h - margin:
                b.y = h - margin
                b.vy = -abs(b.vy)

    def _bee_color(self, b: Bee) -> str:
        st = self.state
        if st.mode == "red":
            return "#f87171" if b.role != "queen" else "#ef4444"
        if st.mode == "green":
            return "#fde047" if b.role != "scout" else "#86efac"
        if st.mode == "working":
            return "#fbbf24" if b.role == "buzzer" else b.hue
        if st.mode == "partial":
            return "#fcd34d"
        return b.hue

    def _draw_bees(self) -> None:
        c = self.canvas
        c.delete("bee")
        c.delete("hud")
        st = self.state
        # trail haze when working
        if st.mode == "working" and st.intensity > 0.5:
            c.create_oval(
                self.width * 0.5 - 50,
                self.height * 0.52 - 50,
                self.width * 0.5 + 50,
                self.height * 0.52 + 50,
                outline="#fbbf2433",
                width=2,
                tags="hud",
            )

        for b in self.bees:
            col = self._bee_color(b)
            # body
            r = 4.2 if b.role == "queen" else (3.4 if b.role == "scout" else 3.0)
            # wing flutter offset
            wing = 1.5 + math.sin(b.phase) * 1.2
            c.create_oval(
                b.x - r - wing,
                b.y - r * 0.6,
                b.x - r + 1,
                b.y + r * 0.6,
                fill="#e2e8f0",
                outline="",
                tags="bee",
            )
            c.create_oval(
                b.x + r - 1,
                b.y - r * 0.6,
                b.x + r + wing,
                b.y + r * 0.6,
                fill="#e2e8f0",
                outline="",
                tags="bee",
            )
            c.create_oval(
                b.x - r, b.y - r, b.x + r, b.y + r, fill=col, outline="#0f172a", tags="bee"
            )
            # stripe
            c.create_line(
                b.x - r * 0.5,
                b.y,
                b.x + r * 0.5,
                b.y,
                fill="#0f172a",
                width=1,
                tags="bee",
            )
            # heading tip
            ang = math.atan2(b.vy, b.vx) if (b.vx or b.vy) else 0.0
            tx = b.x + math.cos(ang) * (r + 3)
            ty = b.y + math.sin(ang) * (r + 3)
            c.create_line(b.x, b.y, tx, ty, fill=col, width=1, tags="bee")

        # HUD corner
        mode_txt = f"{st.mode.upper()}  intensity={st.intensity:.2f}  ∥peak={st.peak_parallel}"
        c.create_text(
            10,
            12,
            anchor="w",
            text=mode_txt,
            fill="#94a3b8",
            font=("Segoe UI", 8),
            tags="hud",
        )
        c.create_text(
            self.width - 10,
            12,
            anchor="e",
            text="buzzers = clean-slate workers",
            fill="#64748b",
            font=("Segoe UI", 8),
            tags="hud",
        )

    def _tick(self) -> None:
        if not self._running:
            return
        try:
            if not self.canvas.winfo_exists():
                self._running = False
                return
        except Exception:
            self._running = False
            return

        self.state.ticks += 1
        # blink LIVE marker feel
        if self.state.ticks % 40 < 20:
            self.live_var.set("● REALTIME SWARM")
        else:
            self.live_var.set("○ REALTIME SWARM")

        try:
            self._step_bees()
            self._draw_bees()
        except Exception:
            pass

        try:
            self._after_id = self.canvas.after(33, self._tick)  # ~30 fps
        except Exception:
            self._running = False
