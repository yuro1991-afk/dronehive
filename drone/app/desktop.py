"""
DroneHive Desktop — native Windows GUI (CustomTkinter).

NOT a browser. Installable .exe + Start Menu via installer scripts.
"""

from __future__ import annotations

import json
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


def _ensure_path() -> Path:
    # Prefer shared resolver (handles frozen exe + APP_ROOT.txt)
    try:
        from drone.app.config import app_root

        root = app_root()
    except Exception:
        root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


import os  # noqa: E402  — after path helpers for crash log

ROOT = _ensure_path()


def run_desktop() -> None:
    import customtkinter as ctk

    from drone.app.service import DroneHiveService
    from drone.app.links import LinkRegistry
    from drone.app.config import app_root, load_app_config
    from drone.app import APP_NAME, APP_VERSION

    root = app_root()
    # keep global in sync
    global ROOT
    ROOT = root
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

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

    # --- state ---
    log_q: queue.Queue[str] = queue.Queue()

    def ui_log(msg: str) -> None:
        log_q.put(msg)

    def drain_log() -> None:
        try:
            while True:
                msg = log_q.get_nowait()
                log_box.configure(state="normal")
                log_box.insert("end", msg + "\n")
                log_box.see("end")
                log_box.configure(state="disabled")
        except queue.Empty:
            pass
        app.after(120, drain_log)

    def worker(title: str, fn: Callable[[], Any]) -> None:
        status_var.set(f"Running: {title}…")
        run_btn.configure(state="disabled")
        hive_btn.configure(state="disabled")
        health_btn.configure(state="disabled")

        def body() -> None:
            try:
                result = fn()
                text = json.dumps(result, indent=2, default=str)
                ui_log(f"\n=== {title} ===\n{text}\n")
                st = result.get("status") if isinstance(result, dict) else None
                ok = result.get("ok") if isinstance(result, dict) else None
                if st == "GREEN" or ok is True:
                    status_var.set(f"{title}: GREEN")
                elif st == "PARTIAL":
                    status_var.set(f"{title}: PARTIAL")
                elif st == "RED" or ok is False:
                    status_var.set(f"{title}: RED / failed")
                else:
                    status_var.set(f"{title}: done")
            except Exception:
                ui_log(f"\n=== {title} ERROR ===\n{traceback.format_exc()}\n")
                status_var.set(f"{title}: ERROR")
            finally:
                app.after(0, lambda: run_btn.configure(state="normal"))
                app.after(0, lambda: hive_btn.configure(state="normal"))
                app.after(0, lambda: health_btn.configure(state="normal"))

        threading.Thread(target=body, daemon=True).start()

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

    body = ctk.CTkFrame(app)
    body.pack(fill="both", expand=True, padx=16, pady=8)

    left = ctk.CTkFrame(body)
    left.pack(side="left", fill="y", padx=(0, 10))

    right = ctk.CTkFrame(body)
    right.pack(side="left", fill="both", expand=True)

    # Goal
    ctk.CTkLabel(left, text="Goal", anchor="w").pack(fill="x", padx=12, pady=(12, 4))
    goal_box = ctk.CTkTextbox(left, width=320, height=110)
    goal_box.pack(padx=12, pady=(0, 8))
    goal_box.insert("1.0", "desktop app: write a tiny workspace artifact")

    ctk.CTkLabel(left, text="Lane", anchor="w").pack(fill="x", padx=12, pady=(4, 2))
    lane_var = ctk.StringVar(value="fast")
    ctk.CTkSegmentedButton(
        left, values=["fast", "full"], variable=lane_var
    ).pack(fill="x", padx=12, pady=(0, 8))

    ctk.CTkLabel(left, text="LM assist", anchor="w").pack(fill="x", padx=12, pady=(4, 2))
    lm_var = ctk.StringVar(value="none")
    ctk.CTkOptionMenu(
        left, values=["none", "ollama"], variable=lm_var
    ).pack(fill="x", padx=12, pady=(0, 12))

    def do_health() -> None:
        worker("health", lambda: svc.health())

    def do_links() -> None:
        worker("links", lambda: links.list_links())

    def do_probe() -> None:
        worker("links-probe", lambda: links.probe_all())

    def do_run() -> None:
        goal = goal_box.get("1.0", "end").strip()
        if not goal:
            status_var.set("Enter a goal")
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

            # no nested HTTP serve from desktop thread pool thrash — still OK
            return commission(ROOT, serve_probe=True)

        worker("commission", fn)

    health_btn = ctk.CTkButton(left, text="Health", command=do_health)
    health_btn.pack(fill="x", padx=12, pady=4)
    ctk.CTkButton(left, text="Universal links", command=do_links).pack(
        fill="x", padx=12, pady=4
    )
    ctk.CTkButton(left, text="Probe links", command=do_probe).pack(
        fill="x", padx=12, pady=4
    )
    run_btn = ctk.CTkButton(left, text="Run task", command=do_run, fg_color="#2563eb")
    run_btn.pack(fill="x", padx=12, pady=(12, 4))
    hive_btn = ctk.CTkButton(left, text="Hive swarm (2)", command=do_hive)
    hive_btn.pack(fill="x", padx=12, pady=4)
    ctk.CTkButton(left, text="Process inbox", command=do_inbox).pack(
        fill="x", padx=12, pady=4
    )
    ctk.CTkButton(
        left, text="Commission seal", command=do_commission, fg_color="#0f766e"
    ).pack(fill="x", padx=12, pady=(12, 4))

    status_var = ctk.StringVar(value=f"Ready · root {ROOT}")
    ctk.CTkLabel(left, textvariable=status_var, wraplength=300, anchor="w").pack(
        fill="x", padx=12, pady=12
    )

    ctk.CTkLabel(right, text="Output / evidence", anchor="w").pack(
        fill="x", padx=12, pady=(12, 4)
    )
    log_box = ctk.CTkTextbox(right, font=ctk.CTkFont(family="Consolas", size=12))
    log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    log_box.insert(
        "1.0",
        f"{APP_NAME} Desktop\n"
        f"Install root: {ROOT}\n"
        f"Config: {cfg.get('name')} v{cfg.get('version')}\n"
        f"Default port (optional API): {cfg.get('port')}\n"
        f"This is a native window — not a browser.\n"
        f"Use Run task / Hive / Commission from the left panel.\n",
    )
    log_box.configure(state="disabled")

    def _report_callback_exception(exc, val, tb) -> None:  # type: ignore[no-untyped-def]
        err = "".join(traceback.format_exception(exc, val, tb))
        try:
            _crash_log_path().write_text(err, encoding="utf-8")
        except OSError:
            pass
        try:
            ui_log(f"\n=== UI ERROR ===\n{err}\n")
            status_var.set("UI error - see crash.log")
        except Exception:
            pass

    app.report_callback_exception = _report_callback_exception  # type: ignore[method-assign]

    # first health refresh
    app.after(200, do_health)
    app.after(100, drain_log)
    app.mainloop()


def main() -> int:
    try:
        run_desktop()
        return 0
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            _crash_log_path().write_text(err, encoding="utf-8")
        except OSError:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox

            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(
                "DroneHive Desktop crashed",
                err[:1800] + f"\n\nLog: {_crash_log_path()}",
            )
            r.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
