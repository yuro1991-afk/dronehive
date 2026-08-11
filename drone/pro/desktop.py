"""
DroneHive Pro — SUPER LEAN desktop.

Philosophy (progressive disclosure / mean machines):
  Show only the primary action surface. Hide everything else.
  UI must not burn cycles — no swarm canvas, no log pane, no chrome.

Visible:
  - one input bar
  - one tiny progress bar
  - one-line whisper status (minimal)

Work runs in a background thread. false_green: 0.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any


def _crash_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "DroneHivePro"
    base.mkdir(parents=True, exist_ok=True)
    return base / "crash.log"


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


def run_desktop_pro() -> None:
    import customtkinter as ctk

    from drone.pro import PRO_NAME, PRO_VERSION
    from drone.pro.service import DroneHiveProService

    root = ROOT
    svc = DroneHiveProService(root)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    # --- lean window: task bar, not a cockpit ---
    app = ctk.CTk()
    app.title(f"{PRO_NAME}")
    app.geometry("520x96")
    app.minsize(420, 88)
    app.resizable(True, False)
    try:
        app.attributes("-topmost", True)
        app.after(500, lambda: app.attributes("-topmost", False))
        app.lift()
        app.focus_force()
    except Exception:
        pass

    busy = threading.Lock()
    progress_job: str | None = None
    progress_val = 0.0

    # shell
    shell = ctk.CTkFrame(app, fg_color="#0a0a0a", corner_radius=0)
    shell.pack(fill="both", expand=True)

    # INPUT only
    cmd_var = ctk.StringVar(value="")
    entry = ctk.CTkEntry(
        shell,
        textvariable=cmd_var,
        height=36,
        border_width=1,
        border_color="#333333",
        fg_color="#141414",
        text_color="#f5f5f5",
        placeholder_text="task…  (Enter)",
        font=ctk.CTkFont(size=14),
    )
    entry.pack(fill="x", padx=10, pady=(10, 4))

    # tiny progress bar
    bar = ctk.CTkProgressBar(
        shell,
        height=4,
        corner_radius=2,
        progress_color="#f59e0b",
        fg_color="#1f1f1f",
    )
    bar.pack(fill="x", padx=10, pady=(0, 2))
    bar.set(0)

    # one-line whisper (can ignore; not a log)
    whisper = ctk.StringVar(value="")
    tip = ctk.CTkLabel(
        shell,
        textvariable=whisper,
        text_color="#525252",
        font=ctk.CTkFont(size=10),
        anchor="w",
        height=14,
    )
    tip.pack(fill="x", padx=12, pady=(0, 6))

    def _ui(fn: Any) -> None:
        try:
            app.after(0, fn)
        except Exception:
            pass

    def set_whisper(text: str) -> None:
        _ui(lambda: whisper.set((text or "")[:90]))

    def set_busy_ui(on: bool) -> None:
        def _a() -> None:
            try:
                entry.configure(state="disabled" if on else "normal")
                if not on:
                    entry.focus_set()
            except Exception:
                pass

        _ui(_a)

    def stop_pulse() -> None:
        nonlocal progress_job, progress_val
        progress_job = None
        progress_val = 0.0

        def _a() -> None:
            try:
                bar.set(0)
                bar.configure(progress_color="#f59e0b")
            except Exception:
                pass

        _ui(_a)

    def pulse(phase: str = "run") -> None:
        """Indeterminate-style crawl — almost no work (one after per tick)."""
        nonlocal progress_job, progress_val
        token = phase
        progress_job = token
        progress_val = 0.08

        def tick() -> None:
            nonlocal progress_val
            if progress_job != token:
                return
            # sawtooth 0.08 → 0.92
            progress_val += 0.035
            if progress_val >= 0.92:
                progress_val = 0.08
            try:
                bar.set(progress_val)
            except Exception:
                return
            try:
                app.after(90, tick)  # ~11 Hz — not 30fps canvas
            except Exception:
                pass

        try:
            app.after(0, tick)
        except Exception:
            pass

    def finish_bar(ok: bool) -> None:
        nonlocal progress_job
        progress_job = None

        def _a() -> None:
            try:
                bar.configure(progress_color="#22c55e" if ok else "#ef4444")
                bar.set(1.0 if ok else 0.15)
            except Exception:
                pass

        _ui(_a)
        # clear after beat so next task is clean
        _ui(lambda: app.after(900, stop_pulse))

    def run_task(event: Any = None) -> str:
        goal = (cmd_var.get() or "").strip()
        if not goal:
            set_whisper("type a task")
            return "break"
        if not busy.acquire(blocking=False):
            set_whisper("busy")
            return "break"

        set_busy_ui(True)
        set_whisper("working…")
        pulse("run")
        goal_cap = goal

        def body() -> None:
            try:
                # lean path: free-form tools only, no hive follow-up by default
                # (hive doubles cost; user wants task focus)
                result = svc.pro_run(
                    goal_cap,
                    max_rounds=6,
                    use_ollama=True,
                    also_hive=False,
                )
                st = (result.get("status") or "").upper()
                ok = st == "GREEN"
                n = int(result.get("tool_calls") or 0)
                path = result.get("seal_path") or result.get("report_path") or ""
                # write last result for power users (disk, not UI)
                try:
                    (root / "out" / "PRO_LEAN_LAST.json").write_text(
                        json.dumps(result, indent=2, default=str),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                set_whisper(
                    f"{st} · tools={n}"
                    + (f" · {Path(str(path)).name}" if path else "")
                )
                finish_bar(ok)
                # clear input on success so next task is ready
                if ok:
                    _ui(lambda: cmd_var.set(""))
            except Exception:
                err = traceback.format_exc()
                try:
                    _crash_log_path().write_text(err, encoding="utf-8")
                except OSError:
                    pass
                set_whisper("error · see crash.log")
                finish_bar(False)
            finally:
                busy.release()
                set_busy_ui(False)

        threading.Thread(target=body, daemon=True, name="pro-lean").start()
        return "break"

    entry.bind("<Return>", run_task)
    entry.bind("<KP_Enter>", run_task)
    # Esc clears whisper / aborts visual only (job may still finish)
    entry.bind("<Escape>", lambda e: (stop_pulse(), set_whisper(""), "break")[-1])

    def on_close() -> None:
        stop_pulse()
        try:
            app.destroy()
        except Exception:
            pass

    app.protocol("WM_DELETE_WINDOW", on_close)
    app.after(80, lambda: entry.focus_force())
    app.mainloop()


def main() -> int:
    try:
        run_desktop_pro()
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
            messagebox.showerror("DroneHive Pro", err[:1200])
            r.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
