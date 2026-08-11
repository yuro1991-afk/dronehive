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
    app.geometry("980x680")
    app.minsize(860, 560)
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

    def worker(title: str, fn: Callable[[], Any]) -> None:
        if not busy_lock.acquire(blocking=False):
            set_status("Already running — wait for current job")
            return

        set_status(f"Running: {title}…")
        set_busy(True)

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
            except Exception:
                err = traceback.format_exc()
                _write_crash(err)
                ui_log(f"\n=== {title} ERROR ===\n{err}\n")
                set_status(f"{title}: ERROR — see crash.log")
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

    ctk.CTkLabel(left, text="Goal", anchor="w").pack(fill="x", padx=12, pady=(12, 4))
    goal_box = ctk.CTkTextbox(left, width=320, height=110)
    goal_box.pack(padx=12, pady=(0, 8))
    goal_box.insert("1.0", "desktop app: write a tiny workspace artifact")

    ctk.CTkLabel(left, text="Lane", anchor="w").pack(fill="x", padx=12, pady=(4, 2))
    lane_var = ctk.StringVar(value="fast")
    ctk.CTkSegmentedButton(left, values=["fast", "full"], variable=lane_var).pack(
        fill="x", padx=12, pady=(0, 8)
    )

    ctk.CTkLabel(left, text="LM assist", anchor="w").pack(fill="x", padx=12, pady=(4, 2))
    lm_var = ctk.StringVar(value="none")
    ctk.CTkOptionMenu(left, values=["none", "ollama"], variable=lm_var).pack(
        fill="x", padx=12, pady=(0, 12)
    )

    status_var = ctk.StringVar(value=f"Ready · root {ROOT}")

    def do_health() -> None:
        worker("health", lambda: svc.health())

    def do_links() -> None:
        worker("links", lambda: links.list_links())

    def do_probe() -> None:
        worker("links-probe", lambda: links.probe_all())

    def do_run() -> None:
        goal = goal_box.get("1.0", "end").strip()
        if not goal:
            set_status("Enter a goal")
            return
        lane = lane_var.get()
        lm = lm_var.get()

        def fn() -> Any:
            return svc.run_task(
                goal,
                lane=lane,
                lm_assist=lm,
                controller="local" if lm == "none" else "ollama",
            )

        worker(f"task[{lane}]", fn)

    def do_hive() -> None:
        goal = goal_box.get("1.0", "end").strip() or "hive desktop unit"
        lane = lane_var.get()

        def fn() -> Any:
            return svc.run_hive(
                goals=[goal, f"{goal} (peer)"],
                cycles=2,
                workers=2 if lane == "fast" else 1,
                lane=lane,
                lm_assist="none",
                controller="local",
            )

        worker(f"hive[{lane}]", fn)

    def do_inbox() -> None:
        worker("inbox", lambda: svc.process_inbox(max_n=10))

    def do_commission() -> None:
        def fn() -> Any:
            from drone.app.commission import commission

            # Avoid nested HTTP serve from GUI (can hang / thrash ports)
            return commission(ROOT, serve_probe=False)

        worker("commission", fn)

    health_btn = ctk.CTkButton(left, text="Health", command=do_health)
    health_btn.pack(fill="x", padx=12, pady=4)
    links_btn = ctk.CTkButton(left, text="Universal links", command=do_links)
    links_btn.pack(fill="x", padx=12, pady=4)
    probe_btn = ctk.CTkButton(left, text="Probe links", command=do_probe)
    probe_btn.pack(fill="x", padx=12, pady=4)
    run_btn = ctk.CTkButton(left, text="Run task", command=do_run, fg_color="#2563eb")
    run_btn.pack(fill="x", padx=12, pady=(12, 4))
    hive_btn = ctk.CTkButton(left, text="Hive swarm (2)", command=do_hive)
    hive_btn.pack(fill="x", padx=12, pady=4)
    inbox_btn = ctk.CTkButton(left, text="Process inbox", command=do_inbox)
    inbox_btn.pack(fill="x", padx=12, pady=4)
    commission_btn = ctk.CTkButton(
        left, text="Commission seal", command=do_commission, fg_color="#0f766e"
    )
    commission_btn.pack(fill="x", padx=12, pady=(12, 4))

    action_buttons = [
        health_btn,
        links_btn,
        probe_btn,
        run_btn,
        hive_btn,
        inbox_btn,
        commission_btn,
    ]

    ctk.CTkLabel(left, textvariable=status_var, wraplength=300, anchor="w").pack(
        fill="x", padx=12, pady=12
    )

    ctk.CTkLabel(right, text="Output / evidence", anchor="w").pack(
        fill="x", padx=12, pady=(12, 4)
    )
    # System font fallback — Consolas missing on some hosts can hard-crash CTkFont
    try:
        log_font = ctk.CTkFont(family="Consolas", size=12)
    except Exception:
        log_font = ctk.CTkFont(size=12)
    log_box = ctk.CTkTextbox(right, font=log_font)
    log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    log_box.insert(
        "1.0",
        f"{APP_NAME} Desktop\n"
        f"Install root: {ROOT}\n"
        f"Config: {cfg.get('name')} v{cfg.get('version')}\n"
        f"Default port (optional API): {cfg.get('port')}\n"
        f"This is a native window — not a browser.\n"
        f"Use Run task / Hive / Commission from the left panel.\n"
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
            app.destroy()
        except Exception:
            pass

    app.protocol("WM_DELETE_WINDOW", _on_close)

    # first health refresh (thread-safe worker)
    app.after(250, do_health)
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
