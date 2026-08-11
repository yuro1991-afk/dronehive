"""
DroneHive Desktop — native Windows GUI (CustomTkinter).

NOT a browser. Installable .exe + Start Menu via installer scripts.

Thread safety: all Tk/CTk widget updates run on the main loop via app.after(...).
Background work never touches widgets directly (prevents random hard crashes).
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable


def _crash_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "DroneHive"
    base.mkdir(parents=True, exist_ok=True)
    return base / "crash.log"


def _debug_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "DroneHive"
    base.mkdir(parents=True, exist_ok=True)
    return base / "desktop_debug.log"


def _append_debug(msg: str) -> None:
    try:
        p = _debug_log_path()
        with p.open("a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass


def _write_crash(err: str) -> Path:
    path = _crash_log_path()
    try:
        path.write_text(err, encoding="utf-8")
    except OSError:
        pass
    _append_debug(f"CRASH\n{err}")
    return path


def _ensure_path() -> Path:
    try:
        from drone.app.config import app_root

        root = app_root()
    except Exception:
        root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


ROOT = _ensure_path()


def run_desktop() -> None:
    import customtkinter as ctk

    from drone.app import APP_NAME, APP_VERSION
    from drone.app.config import app_root, load_app_config
    from drone.app.links import LinkRegistry
    from drone.app.service import DroneHiveService

    root = app_root()
    global ROOT
    ROOT = root
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    _append_debug(f"start root={root}")

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    svc = DroneHiveService(root)
    links = LinkRegistry(root)
    cfg = load_app_config(root)

    app = ctk.CTk()
    app.title(f"{APP_NAME} Desktop  v{APP_VERSION}")
    app.geometry("1100x780")
    app.minsize(960, 680)
    try:
        app.attributes("-topmost", True)
        app.after(800, lambda: app.attributes("-topmost", False))
        app.lift()
        app.focus_force()
    except Exception:
        pass

    log_q: queue.Queue[str] = queue.Queue()
    busy_lock = threading.Lock()

    # --- UI helpers (main-thread only) ---
    def ui_log(msg: str) -> None:
        log_q.put(msg)

    def set_status(text: str) -> None:
        # Always schedule onto main loop
        def _apply() -> None:
            try:
                status_var.set(text)
            except Exception:
                pass

        try:
            app.after(0, _apply)
        except Exception:
            pass

    def set_busy(is_busy: bool) -> None:
        state = "disabled" if is_busy else "normal"

        def _apply() -> None:
            for btn in action_buttons:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

        try:
            app.after(0, _apply)
        except Exception:
            pass

    def drain_log() -> None:
        try:
            while True:
                msg = log_q.get_nowait()
                try:
                    log_box.configure(state="normal")
                    log_box.insert("end", msg + "\n")
                    log_box.see("end")
                    log_box.configure(state="disabled")
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            app.after(150, drain_log)
        except Exception:
            pass

    def worker(title: str, fn: Callable[[], Any], *, swarm_units: int = 0) -> None:
        if not busy_lock.acquire(blocking=False):
            set_status("Already running — wait for current job")
            return

        set_status(f"Running: {title}…")
        set_busy(True)
        try:
            swarm.mark_working(title, units_hint=swarm_units)
        except Exception:
            pass

        def body() -> None:
            try:
                result = fn()
                text = json.dumps(result, indent=2, default=str)
                ui_log(f"\n=== {title} ===\n{text}\n")
                st = result.get("status") if isinstance(result, dict) else None
                ok = result.get("ok") if isinstance(result, dict) else None
                if st == "GREEN" or ok is True:
                    set_status(f"{title}: GREEN")
                elif st == "PARTIAL":
                    set_status(f"{title}: PARTIAL")
                elif st == "RED" or ok is False:
                    set_status(f"{title}: RED / failed")
                else:
                    set_status(f"{title}: done")

                def _sync_swarm() -> None:
                    try:
                        if isinstance(result, dict):
                            swarm.pulse_from_result(title, result)
                        else:
                            swarm.mark_idle()
                    except Exception:
                        pass

                app.after(0, _sync_swarm)
            except Exception:
                err = traceback.format_exc()
                _write_crash(err)
                ui_log(f"\n=== {title} ERROR ===\n{err}\n")
                set_status(f"{title}: ERROR — see crash.log")
                app.after(
                    0,
                    lambda: swarm.set_activity(
                        mode="red",
                        job=title,
                        proof_line=f"ERROR · {title} · see crash.log · false_green: 0",
                        last_status="RED",
                    ),
                )
            finally:
                set_busy(False)
                busy_lock.release()

        threading.Thread(target=body, daemon=True, name=f"dronehive-{title}").start()

    # --- layout ---
    header = ctk.CTkFrame(app, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(14, 6))
    ctk.CTkLabel(
        header,
        text=f"{APP_NAME}",
        font=ctk.CTkFont(size=22, weight="bold"),
    ).pack(side="left")
    ctk.CTkLabel(
        header,
        text=f"  Desktop · v{APP_VERSION} · modular swarm · not a browser",
        text_color="#8aa0c8",
    ).pack(side="left", padx=(8, 0))

    body_fr = ctk.CTkFrame(app)
    body_fr.pack(fill="both", expand=True, padx=16, pady=8)

    left = ctk.CTkFrame(body_fr)
    left.pack(side="left", fill="y", padx=(0, 10))

    right = ctk.CTkFrame(body_fr)
    right.pack(side="left", fill="both", expand=True)

    ctk.CTkLabel(
        left,
        text="Command / goal  (Enter to run)",
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 4))

    # Primary single-line command bar — reliable focus + Return binding
    cmd_var = ctk.StringVar(value="")
    cmd_entry = ctk.CTkEntry(
        left,
        width=320,
        height=36,
        textvariable=cmd_var,
        placeholder_text="Command → Ollama brain → swarm (Enter)",
    )
    cmd_entry.pack(fill="x", padx=12, pady=(0, 6))

    ctk.CTkLabel(left, text="Notes (optional multi-line)", anchor="w").pack(
        fill="x", padx=12, pady=(4, 2)
    )
    goal_box = ctk.CTkTextbox(left, width=320, height=72)
    goal_box.pack(padx=12, pady=(0, 8))
    goal_box.insert("1.0", "")

    ctk.CTkLabel(left, text="Lane (hint for brain / manual)", anchor="w").pack(
        fill="x", padx=12, pady=(4, 2)
    )
    lane_var = ctk.StringVar(value="fast")
    ctk.CTkSegmentedButton(left, values=["fast", "full"], variable=lane_var).pack(
        fill="x", padx=12, pady=(0, 8)
    )

    ctk.CTkLabel(
        left,
        text="Brain: Ollama  →  Swarm (default)",
        anchor="w",
        text_color="#fbbf24",
    ).pack(fill="x", padx=12, pady=(4, 2))
    # Manual override for legacy direct buttons only
    lm_var = ctk.StringVar(value="ollama")
    ctk.CTkLabel(left, text="Manual LM (buttons only)", anchor="w").pack(
        fill="x", padx=12, pady=(4, 2)
    )
    ctk.CTkOptionMenu(left, values=["ollama", "none"], variable=lm_var).pack(
        fill="x", padx=12, pady=(0, 12)
    )

    status_var = ctk.StringVar(
        value=f"Ready · Ollama brain → swarm · root {ROOT}"
    )
    last_cmd_var = ctk.StringVar(value="last cmd: (none)")

    def _read_main_input() -> str:
        """Read primary command entry + optional notes. Always returns stripped text."""
        primary = ""
        try:
            primary = (cmd_var.get() or "").strip()
        except Exception:
            try:
                primary = (cmd_entry.get() or "").strip()
            except Exception:
                primary = ""
        notes = ""
        try:
            # CTkTextbox: try both index styles
            notes = goal_box.get("1.0", "end-1c")
        except Exception:
            try:
                notes = goal_box.get("0.0", "end")
            except Exception:
                try:
                    notes = goal_box.get("1.0", "end")
                except Exception:
                    notes = ""
        notes = (notes or "").strip()
        if primary and notes:
            return f"{primary}\n{notes}".strip()
        return primary or notes

    def _echo_command(raw: str, action: str) -> None:
        last_cmd_var.set(f"last cmd: [{action}] {raw[:80]}")
        set_status(f"Got command → {action}: {raw[:60]}")
        ui_log(f"\n>>> USER INPUT [{action}]\n{raw}\n")
        try:
            swarm.set_activity(
                mode="working",
                job=action,
                proof_line=f"command accepted · {action} · {raw[:70]} · false_green: 0",
                last_status="RUNNING",
            )
        except Exception:
            pass

    def do_brain(goal_override: str | None = None) -> None:
        """Main path: user text → Ollama plans → swarm executes."""
        raw = (goal_override if goal_override is not None else _read_main_input()).strip()
        if not raw:
            set_status("Type a command for Ollama → swarm, then Enter")
            try:
                cmd_entry.focus_set()
            except Exception:
                pass
            return
        _echo_command(raw, "ollama→swarm")
        set_status(f"Ollama brain planning… then swarm · {raw[:50]}")
        try:
            swarm.set_activity(
                mode="working",
                job="ollama-brain",
                intensity=0.9,
                proof_line=f"OLLAMA PLANNING · {raw[:60]} · then delegate swarm · false_green: 0",
                last_status="RUNNING",
                n_bees=32,
            )
        except Exception:
            pass

        cmd_cap = raw

        def fn() -> Any:
            return svc.brain_command(cmd_cap)

        worker("ollama→swarm", fn, swarm_units=3)

    def do_health() -> None:
        _echo_command("health", "health")
        worker("health", lambda: svc.health(), swarm_units=1)

    def do_links() -> None:
        _echo_command("links", "links")
        worker("links", lambda: links.list_links(), swarm_units=1)

    def do_probe() -> None:
        _echo_command("probe", "links-probe")
        worker("links-probe", lambda: links.probe_all(), swarm_units=1)

    def do_run(goal_override: str | None = None) -> None:
        # Manual direct task (bypass brain) — still uses Ollama as controller by default
        goal = (goal_override if goal_override is not None else _read_main_input()).strip()
        if not goal:
            set_status("Enter a goal in the command box")
            try:
                cmd_entry.focus_set()
            except Exception:
                pass
            return
        low = goal.lower()
        if low.startswith("run "):
            goal = goal[4:].strip()
        lane = lane_var.get()
        lm = lm_var.get() or "ollama"
        _echo_command(goal, f"direct-task[{lane}]")
        goal_cap, lane_cap, lm_cap = goal, lane, lm

        def fn() -> Any:
            return svc.run_task(
                goal_cap,
                lane=lane_cap,
                lm_assist=lm_cap,
                controller="ollama" if lm_cap != "none" else "local",
            )

        worker(f"task[{lane_cap}]", fn, swarm_units=2 if lane_cap == "fast" else 4)

    def do_hive(goal_override: str | None = None) -> None:
        raw = (goal_override if goal_override is not None else _read_main_input()).strip()
        goal = raw or "hive desktop unit"
        low = goal.lower()
        if low.startswith("hive "):
            goal = goal[5:].strip() or "hive desktop unit"
        elif low == "hive":
            goal = "hive desktop unit"
        lane = lane_var.get()
        workers = 2 if lane == "fast" else 1
        lm = lm_var.get() or "ollama"
        _echo_command(goal, f"direct-hive[{lane}]×{workers}")
        goal_cap, lane_cap, workers_cap, lm_cap = goal, lane, workers, lm

        def fn() -> Any:
            return svc.run_hive(
                goals=[goal_cap, f"{goal_cap} (peer)"],
                cycles=2,
                workers=workers_cap,
                lane=lane_cap,
                lm_assist=lm_cap if lm_cap != "none" else "none",
                controller="ollama" if lm_cap != "none" else "local",
            )

        worker(f"hive[{lane_cap}]", fn, swarm_units=workers_cap)

    def do_inbox() -> None:
        _echo_command("inbox", "inbox")
        worker("inbox", lambda: svc.process_inbox(max_n=10), swarm_units=1)

    def do_commission() -> None:
        _echo_command("commission", "commission")

        def fn() -> Any:
            from drone.app.commission import commission

            return commission(ROOT, serve_probe=False)

        worker("commission", fn, swarm_units=3)

    def dispatch_command(event: Any = None) -> str | None:
        """
        Main input → always Ollama brain → swarm delegate (unless empty).
        Explicit local verbs still available via side buttons.
        Prefix 'local:' to skip brain and run direct task.
        """
        raw = _read_main_input()
        if not raw:
            set_status("Type a command for Ollama → swarm, then press Enter")
            return "break"
        low = raw.strip().splitlines()[0].strip().lower()
        # escape hatch: local:… skips brain
        if low.startswith("local:"):
            do_run(raw.split(":", 1)[1].strip())
            return "break"
        if low.startswith("direct hive"):
            do_hive(raw[len("direct hive") :].strip() or None)
            return "break"
        # default: Ollama plans, swarm executes
        do_brain(raw)
        return "break"

    # Enter in command bar runs the command (main UX fix)
    cmd_entry.bind("<Return>", dispatch_command)
    cmd_entry.bind("<KP_Enter>", dispatch_command)
    try:
        cmd_entry.focus_set()
    except Exception:
        pass

    go_btn = ctk.CTkButton(
        left,
        text="Go · Ollama → Swarm (Enter)",
        command=dispatch_command,
        fg_color="#2563eb",
    )
    go_btn.pack(fill="x", padx=12, pady=(4, 8))

    health_btn = ctk.CTkButton(left, text="Health", command=do_health)
    health_btn.pack(fill="x", padx=12, pady=4)
    links_btn = ctk.CTkButton(left, text="Universal links", command=do_links)
    links_btn.pack(fill="x", padx=12, pady=4)
    probe_btn = ctk.CTkButton(left, text="Probe links", command=do_probe)
    probe_btn.pack(fill="x", padx=12, pady=4)
    run_btn = ctk.CTkButton(
        left, text="Direct task (skip brain)", command=lambda: do_run(None)
    )
    run_btn.pack(fill="x", padx=12, pady=(12, 4))
    hive_btn = ctk.CTkButton(
        left, text="Direct hive (skip brain)", command=lambda: do_hive(None)
    )
    hive_btn.pack(fill="x", padx=12, pady=4)
    inbox_btn = ctk.CTkButton(left, text="Process inbox", command=do_inbox)
    inbox_btn.pack(fill="x", padx=12, pady=4)
    commission_btn = ctk.CTkButton(
        left, text="Commission seal", command=do_commission, fg_color="#0f766e"
    )
    commission_btn.pack(fill="x", padx=12, pady=(12, 4))

    action_buttons = [
        go_btn,
        health_btn,
        links_btn,
        probe_btn,
        run_btn,
        hive_btn,
        inbox_btn,
        commission_btn,
    ]

    ctk.CTkLabel(left, textvariable=status_var, wraplength=300, anchor="w").pack(
        fill="x", padx=12, pady=(8, 2)
    )
    ctk.CTkLabel(
        left, textvariable=last_cmd_var, wraplength=300, anchor="w", text_color="#94a3b8"
    ).pack(fill="x", padx=12, pady=(0, 8))

    # --- REALTIME SWARM viewport (boids bees synced to work) ---
    from drone.app.swarm_viewport import RealtimeSwarmViewport

    ctk.CTkLabel(
        right,
        text="Hive activity / proof of work",
        anchor="w",
        font=ctk.CTkFont(size=13, weight="bold"),
    ).pack(fill="x", padx=12, pady=(10, 0))

    # tk parent for Canvas: CTk frame's internal widget
    swarm_host = ctk.CTkFrame(right, fg_color="#111827")
    swarm_host.pack(fill="x", padx=4, pady=(4, 4))
    # Use underlying tk widget so Canvas parents cleanly
    swarm = RealtimeSwarmViewport(
        swarm_host,
        width=700,
        height=240,
        n_bees=28,
        bg="#0b1220",
    )
    swarm.set_activity(
        mode="idle",
        job="ready",
        proof_line="proof: viewport live · bees = buzzers · false_green: 0",
        last_status="READY",
    )

    ctk.CTkLabel(right, text="Output / evidence", anchor="w").pack(
        fill="x", padx=12, pady=(8, 4)
    )
    # System font fallback — Consolas missing on some hosts can hard-crash CTkFont
    try:
        log_font = ctk.CTkFont(family="Consolas", size=12)
    except Exception:
        log_font = ctk.CTkFont(size=12)
    log_box = ctk.CTkTextbox(right, font=log_font, height=220)
    log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    log_box.insert(
        "1.0",
        f"{APP_NAME} Desktop\n"
        f"Install root: {ROOT}\n"
        f"Config: {cfg.get('name')} v{cfg.get('version')}\n"
        f"Default port (optional API): {cfg.get('port')}\n"
        f"This is a native window — not a browser.\n"
        f"MAIN INPUT → Ollama brain plans JSON → swarm/hive executes.\n"
        f"Press Enter / Go. Evidence: out/BRAIN_LAST_PLAN.json + BRAIN_DELEGATE_LAST.json\n"
        f"Escape hatch: prefix local: for direct task without brain.\n"
        f"Realtime swarm bees track working/GREEN/RED.\n"
        f"Crash log: {_crash_log_path()}\n",
    )
    log_box.configure(state="disabled")

    def _report_callback_exception(exc, val, tb) -> None:  # type: ignore[no-untyped-def]
        err = "".join(traceback.format_exception(exc, val, tb))
        _write_crash(err)
        ui_log(f"\n=== UI ERROR ===\n{err}\n")
        set_status("UI error — see crash.log")

    app.report_callback_exception = _report_callback_exception  # type: ignore[method-assign]

    def _on_close() -> None:
        _append_debug("window close")
        try:
            swarm.destroy()
        except Exception:
            pass
        try:
            app.destroy()
        except Exception:
            pass

    app.protocol("WM_DELETE_WINDOW", _on_close)

    # Focus command bar first so typing works immediately (do not steal with auto-job)
    def _focus_cmd() -> None:
        try:
            cmd_entry.focus_force()
        except Exception:
            try:
                cmd_entry.focus_set()
            except Exception:
                pass

    app.after(150, _focus_cmd)
    # Light health in background after UI is ready — does not replace user commands
    app.after(900, do_health)
    app.after(100, drain_log)
    _append_debug("mainloop")
    try:
        app.mainloop()
    finally:
        _append_debug("exit ok")


def main() -> int:
    try:
        run_desktop()
        return 0
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        path = _write_crash(err)
        try:
            import tkinter as tk
            from tkinter import messagebox

            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(
                "DroneHive Desktop crashed",
                err[:1800] + f"\n\nLog: {path}",
            )
            r.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
