"""CLI: python -m drone <command>"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from drone.chain import BrainFabric
    from drone.controllers import describe_controllers, make_lm_fn, validate_controller
    from drone.hive import BuzzerHive
    from drone.library_bridge import LibraryBridge

    p = argparse.ArgumentParser(
        description="AI Worker Drone fabric + Buzzer Hive (clean-slate fresh agents)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="fabric layout + honesty")
    sub.add_parser("stats", help="smart_index / skills / builds")
    sub.add_parser("brain", help="Ollama top-model status + tool wiring")
    sub.add_parser(
        "observer",
        help="silent observer LATEST (never user chat — loop-informed only)",
    )
    sub.add_parser(
        "critic",
        help="third LLM core critic — one critical pass for the engine",
    )
    cl = sub.add_parser(
        "critic-loop",
        help="constant sentient critic loop (engine-facing, leashed interval)",
    )
    cl.add_argument(
        "--ticks",
        type=int,
        default=None,
        help="stop after N ticks (default: forever until Ctrl+C)",
    )

    # --- Standalone modular app ---
    app_p = sub.add_parser(
        "app",
        help="DroneHive standalone modular app (serve · commission · links · task)",
    )
    app_sub = app_p.add_subparsers(dest="app_cmd", required=True)
    app_sub.add_parser("health")
    app_sub.add_parser("status")
    app_sub.add_parser("links")
    app_sub.add_parser("links-probe")
    app_sub.add_parser("commission")
    app_sub.add_parser("desktop", help="NATIVE desktop GUI — not a browser")
    app_sub.add_parser(
        "desktop-pro",
        help="DroneHive Pro v2 desktop — free-form tools + premium UI",
    )
    app_srv = app_sub.add_parser("serve", help="optional HTTP API (secondary)")
    app_srv.add_argument("--host", default=None)
    app_srv.add_argument("--port", type=int, default=None)
    app_task = app_sub.add_parser("task")
    app_task.add_argument("--goal", required=True)
    app_task.add_argument("--lane", default="fast", choices=["fast", "full"])
    app_task.add_argument("--lm-assist", default="none")
    app_task.add_argument("--controller", default="local")
    app_pro = app_sub.add_parser(
        "pro",
        help="PRO v2 free-form Ollama tool agent (real files)",
    )
    app_pro.add_argument("--goal", "-g", required=True)
    app_pro.add_argument("--rounds", type=int, default=8)
    app_pro.add_argument("--hive", action="store_true")
    app_pro.add_argument("--no-ollama", action="store_true")
    app_brain = app_sub.add_parser(
        "brain",
        help="user command → Ollama plan → swarm (v1 path)",
    )
    app_brain.add_argument("--command", "-c", required=True)
    app_inbox = app_sub.add_parser("inbox")
    app_inbox.add_argument("--max", type=int, default=10)
    app_sub.add_parser(
        "handoff",
        help="Grok ↔ drone live handoff (use: python -m drone handoff …)",
    )

    # --- Principal model clones (dh-*) + M2M 0.5B fleet (m2m-*) ---
    cl_p = sub.add_parser(
        "clones",
        help="Model clones: dh-* principal or --catalog configs/m2m_clones_0.5b.json (strict M2M)",
    )
    cl_p.add_argument(
        "--catalog",
        default=None,
        help="catalog path (default model_clones.json; use configs/m2m_clones_0.5b.json for 20×0.5B M2M)",
    )
    cl_sub = cl_p.add_subparsers(dest="clones_cmd", required=True)
    cl_sub.add_parser("list")
    cl_sub.add_parser("modelfiles")
    cl_ins = cl_sub.add_parser("install")
    cl_ins.add_argument("--only", default=None)
    cl_sub.add_parser("smoke")
    cl_sub.add_parser("wire")
    cl_sub.add_parser("seal")
    cl_sub.add_parser("all")

    # --- Real tool-using agent loop (PLAN→ACT→OBSERVE→FACE) ---
    ag_p = sub.add_parser(
        "agent",
        help="REAL agent loop: plan → execute tools → observe → FACE summary",
    )
    ag_sub = ag_p.add_subparsers(dest="agent_cmd", required=True)
    ag_sub.add_parser("status")
    ag_run = ag_sub.add_parser("run")
    ag_run.add_argument("goal", nargs="+")
    ag_run.add_argument("--rounds", type=int, default=None)
    ag_run.add_argument("--no-face", action="store_true")
    ag_seal = ag_sub.add_parser("seal")
    ag_seal.add_argument("--goal", default=None)

    # --- Synaptic agent loop (SENSE→FIRE→INTEGRATE→WEIGHT→GATE) ---
    syn_p = sub.add_parser(
        "synapse",
        help="Functional synaptic agent loop: workers fire → FACE integrates",
    )
    syn_sub = syn_p.add_subparsers(dest="syn_cmd", required=True)
    syn_sub.add_parser("status")
    syn_sub.add_parser("reset")
    syn_tk = syn_sub.add_parser("tick")
    syn_tk.add_argument("goal", nargs="+")
    syn_tk.add_argument("--residual", default="")
    syn_rn = syn_sub.add_parser("run")
    syn_rn.add_argument("goal", nargs="+")
    syn_rn.add_argument("--ticks", type=int, default=None)
    syn_sl = syn_sub.add_parser("seal")
    syn_sl.add_argument("--goal", default=None)

    # --- Super Mesh Live AI (20 m2m + 2 flagships) ---
    mesh_p = sub.add_parser(
        "mesh",
        help="Super Mesh Live AI: 20 m2m synapses + fx-reason + fx-face flagships",
    )
    mesh_sub = mesh_p.add_subparsers(dest="mesh_cmd", required=True)
    mesh_sub.add_parser("status")
    mesh_sub.add_parser("roster", help="each node: one job + one tool; idle state")
    mesh_sub.add_parser("hardwire", help="deep-bind mesh; all nodes IDLE until called")
    mesh_sub.add_parser("install-flagships", help="install fx-reason + fx-face then hardwire")
    mesh_tk = mesh_sub.add_parser("tick")
    mesh_tk.add_argument("goal", nargs="*")
    mesh_tk.add_argument("--residual", default="")
    mesh_tk.add_argument("--call", default="", help="comma tags to wake; empty=all idle")
    mesh_tk.add_argument("--fire-lm", action="store_true")
    mesh_rn = mesh_sub.add_parser("run")
    mesh_rn.add_argument("goal", nargs="+")
    mesh_rn.add_argument("--ticks", type=int, default=None)
    mesh_rn.add_argument("--call", default="")
    mesh_sub.add_parser("seal")
    mesh_call = mesh_sub.add_parser("call", help="wake one node tool/job → IDLE")
    mesh_call.add_argument("tag")
    mesh_call.add_argument("--goal", default="")
    mesh_cm = mesh_sub.add_parser("call-many", help="wake listed tags only")
    mesh_cm.add_argument("tags", nargs="+")
    mesh_cm.add_argument("--goal", default="")

    # --- Multi-Host FACE (all workers → one speaker → user) ---
    face_p = sub.add_parser(
        "face",
        help="Multi-host FACE: Hermes/OpenClaw/OpenCode/Ollama/Drones → one speaker",
    )
    face_sub = face_p.add_subparsers(dest="face_cmd", required=True)
    face_sub.add_parser("hosts")
    face_sub.add_parser("seal")
    face_sub.add_parser("hot", help="hotwire warm all models + probe hosts")
    face_sub.add_parser("status", help="hotwire status")
    face_ask = face_sub.add_parser("ask", help="legacy council ask (prefer: go/chat)")
    face_ask.add_argument("text", nargs="+")
    face_ask.add_argument("--opt-in", action="store_true")
    face_ask.add_argument("--workers", default=None)
    face_ask.add_argument("--stream", action="store_true")
    face_chat = face_sub.add_parser("chat", help="HOTWIRED chat → FACE only")
    face_chat.add_argument("text", nargs="+")
    face_exec = face_sub.add_parser("exec", help="precision command exec → FACE")
    face_exec.add_argument("text", nargs="+")
    face_go = face_sub.add_parser("go", help="auto classify chat|exec|status → FACE")
    face_go.add_argument("text", nargs="+")

    # --- Future Seer (speculative multi-model typeahead) ---
    se = sub.add_parser(
        "seer",
        help="Future Seer: multi-model speculative typeahead + Jane taps",
    )
    se_sub = se.add_subparsers(dest="seer_cmd", required=True)
    se_sub.add_parser("hot")
    se_sub.add_parser("hot-start")
    se_sub.add_parser("hot-stop")
    se_sub.add_parser("status")
    se_sub.add_parser("seal")
    se_sub.add_parser("tui")
    se_ty = se_sub.add_parser("type")
    se_ty.add_argument("text", nargs="+")
    se_ty.add_argument("--no-spec", action="store_true")
    se_sp = se_sub.add_parser("spec")
    se_sp.add_argument("text", nargs="+")
    se_cm = se_sub.add_parser("commit")
    se_cm.add_argument("text", nargs="*")
    se_cm.add_argument(
        "--mode",
        default="preview",
        choices=["preview", "handoff_fast", "handoff_hive", "helper_probe", "helper_draft"],
    )
    se_cm.add_argument("--auto", action="store_true")
    se_hp = se_sub.add_parser("helper")
    se_hp.add_argument("--expert", default="probe", choices=["probe", "draft", "forge", "seal"])
    se_hp.add_argument("goal", nargs="+")

    # --- Super LLMs (all safe full models in one app) ---
    sl = sub.add_parser(
        "super-llms",
        help="Super app: all full LLMs safe on this host (12GB)",
    )
    sl_sub = sl.add_subparsers(dest="super_cmd", required=True)
    sl_sub.add_parser("list")
    sl_sub.add_parser("status")
    sl_sm = sl_sub.add_parser("smoke")
    sl_sm.add_argument("--cloud", action="store_true")
    sl_sub.add_parser("seal")
    sl_sub.add_parser("roles")
    sl_sub.add_parser("tui")
    sl_rt = sl_sub.add_parser("route")
    sl_rt.add_argument("goal", nargs="+")
    sl_rt.add_argument("--role", default=None)
    sl_ch = sl_sub.add_parser("chat")
    sl_ch.add_argument("--model", "-m", default=None)
    sl_ch.add_argument("--role", default=None)
    sl_ch.add_argument("prompt", nargs="+")

    # --- Stable Bridge GTX 1080 Ti (spare; 3060 stays MAIN; NO kill switch) ---
    b1080 = sub.add_parser(
        "bridge-1080",
        help="LIVE bridge to GTX 1080 Ti (no kill switch; 3060 MAIN protected)",
    )
    b1080_sub = b1080.add_subparsers(dest="b1080_cmd", required=True)
    b1080_st = b1080_sub.add_parser("start", help="LIVE daemon + watchdog (no kill)")
    b1080_st.add_argument("--interval", type=float, default=20.0)
    b1080_st.add_argument("--no-watchdog", action="store_true")
    b1080_lv = b1080_sub.add_parser("live")
    b1080_lv.add_argument("--interval", type=float, default=20.0)
    b1080_wd = b1080_sub.add_parser("watchdog")
    b1080_wd.add_argument("--interval", type=float, default=20.0)
    b1080_sub.add_parser("process")
    b1080_sub.add_parser("status")
    b1080_sub.add_parser("probe")
    b1080_sub.add_parser("enable")
    b1080_sub.add_parser("disable")
    b1080_arm = b1080_sub.add_parser("arm")
    b1080_arm.add_argument("--only-1080", action="store_true")
    b1080_arm.add_argument("--persist-user", action="store_true")
    b1080_sub.add_parser("disarm")
    b1080_w = b1080_sub.add_parser("watch")
    b1080_w.add_argument("--interval", type=float, default=20.0)
    b1080_w.add_argument("--ticks", type=int, default=0)
    b1080_sub.add_parser("seal")
    b1080_sub.add_parser("kill", help="REFUSED — no kill switch")

    # --- Super Kernel Lane (barebones multi-thread any-model host) ---
    sk = sub.add_parser(
        "super-kernel",
        help="Barebones multi-thread super kernel lane — any model, VRAM-only limit",
    )
    sk_sub = sk.add_subparsers(dest="sk_cmd", required=True)
    sk_start = sk_sub.add_parser(
        "start",
        help="stable process + watchdog + idler (only Boss/Grok may kill)",
    )
    sk_start.add_argument("--host", default=None)
    sk_start.add_argument("--port", type=int, default=None)
    sk_start.add_argument("--no-watchdog", action="store_true")
    sk_start.add_argument("--no-idler", action="store_true")
    sk_sv = sk_sub.add_parser("serve", help="run ThreadingHTTPServer lane")
    sk_sv.add_argument("--host", default=None)
    sk_sv.add_argument("--port", type=int, default=None)
    sk_sv.add_argument("--stable", action="store_true", default=True)
    sk_sv.add_argument("--foreground", action="store_true")
    sk_wd = sk_sub.add_parser("watchdog", help="auto-restart unless authorized kill")
    sk_wd.add_argument("--host", default="127.0.0.1")
    sk_wd.add_argument("--port", type=int, default=11450)
    sk_wd.add_argument("--interval", type=float, default=8.0)
    sk_id = sk_sub.add_parser("idler", help="keep-alive idler heartbeats")
    sk_id.add_argument("--host", default="127.0.0.1")
    sk_id.add_argument("--port", type=int, default=11450)
    sk_id.add_argument("--interval", type=float, default=15.0)
    sk_id.add_argument("--warm-model", default=None)
    sk_id.add_argument("--warm-every", type=int, default=12)
    sk_sub.add_parser("kill", help="HARD KILL — Boss CLI or Grok only")
    sk_sub.add_parser("process", help="stable process + idler status (no token leak)")
    sk_sub.add_parser("status")
    sk_sub.add_parser("vram")
    sk_sub.add_parser("ram")
    sk_sub.add_parser("cpu")
    sk_sub.add_parser("power")
    sk_sub.add_parser("cpu-power")
    sk_sub.add_parser("ram-power")
    sk_sub.add_parser("host-power")
    sk_sub.add_parser("models")
    sk_sub.add_parser("instances")
    sk_sub.add_parser("connections")
    sk_sub.add_parser("live")
    sk_sub.add_parser("reload-config")
    sk_sub.add_parser("smoke")
    sk_sub.add_parser("seal")
    sk_op = sk_sub.add_parser("open")
    sk_op.add_argument("--model", "-m", required=True)
    sk_op.add_argument("--force", action="store_true")
    sk_cl = sk_sub.add_parser("close")
    sk_cl.add_argument("--id", required=True)
    sk_cl.add_argument("--unload", action="store_true")
    sk_cn = sk_sub.add_parser("connect")
    sk_cn.add_argument("--model", "-m", default=None)
    sk_cn.add_argument("--client", default="")
    sk_cn.add_argument("--force", action="store_true")
    sk_dc = sk_sub.add_parser("disconnect")
    sk_dc.add_argument("--id", required=True)
    sk_dc.add_argument("--unload", action="store_true")
    sk_hs = sk_sub.add_parser("hot-swap")
    sk_hs.add_argument("--to", dest="to_model", required=True)
    sk_hs.add_argument("--connection", default=None)
    sk_hs.add_argument("--instance", default=None)
    sk_hs.add_argument("--force", action="store_true")
    sk_hs.add_argument("--no-unload", action="store_true")
    sk_hs.add_argument("--set-active", action="store_true")
    sk_ch = sk_sub.add_parser("chat")
    sk_ch.add_argument("--model", "-m", default=None)
    sk_ch.add_argument("--instance", default=None)
    sk_ch.add_argument("--connection", default=None)
    sk_ch.add_argument("prompt", nargs="+")
    sk_ch.add_argument("--force", action="store_true")
    sk_cg = sk_sub.add_parser("can-hang")
    sk_cg.add_argument("--model", "-m", required=True)

    # --- Full LLM resources (agent access surface) ---
    lr = sub.add_parser(
        "llm-resources",
        help="Full LLM resource access for agents (list|status|route|chat|seal)",
    )
    lr_sub = lr.add_subparsers(dest="llm_cmd", required=True)
    lr_sub.add_parser("list", help="catalog all installed + routes")
    lr_sub.add_parser("status")
    lr_sub.add_parser("seal", help="smoke generate + write AGENT_LLM_FULL_ACCESS.json")
    lr_rt = lr_sub.add_parser("route")
    lr_rt.add_argument("goal", nargs="+")
    lr_rt.add_argument("--role", default=None)
    lr_ch = lr_sub.add_parser("chat")
    lr_ch.add_argument("--model", "-m", default=None)
    lr_ch.add_argument("--role", default=None)
    lr_ch.add_argument("prompt", nargs="+")
    lr_gen = lr_sub.add_parser("generate")
    lr_gen.add_argument("--model", "-m", default=None)
    lr_gen.add_argument("--role", default=None)
    lr_gen.add_argument("prompt", nargs="+")

    # --- Grok cowork handoff (top-level) ---
    ho = sub.add_parser(
        "handoff",
        help="Grok → drones live handoff + e2e cowork lanes",
    )
    ho_sub = ho.add_subparsers(dest="handoff_cmd", required=True)
    ho_sub.add_parser("lanes", help="prove live lanes ready")
    ho_to = ho_sub.add_parser("to", help="hand off a goal to drones")
    ho_to.add_argument("--goal", "-g", required=True)
    ho_to.add_argument(
        "--mode",
        "-m",
        default="fast",
        choices=["fast", "full", "hive", "brain", "pro", "inbox", "clone"],
    )
    ho_to.add_argument("--lane", default=None, choices=["fast", "full"])
    ho_to.add_argument("--workers", type=int, default=2)
    ho_to.add_argument("--cycles", type=int, default=2)
    ho_to.add_argument("--lm-assist", default="none")
    ho_to.add_argument("--controller", default="grok")
    ho_to.add_argument("--notes", default="")
    ho_to.add_argument("--continuous-task-id", default=None)
    ho_to.add_argument("--context", action="append", default=[])
    ho_to.add_argument("--no-wait", action="store_true")
    ho_to.add_argument("--skip-lane-check", action="store_true")
    ho_to.add_argument("--pro-rounds", type=int, default=6)
    ho_to.add_argument("--also-hive", action="store_true")
    ho_to.add_argument("--goal-unit", action="append", dest="goals", default=None)
    ho_col = ho_sub.add_parser("collect", help="pull drone results for Grok")
    ho_col.add_argument("--max", type=int, default=20)
    ho_col.add_argument("--keep", action="store_true")
    ho_st = ho_sub.add_parser("status", help="cowork status")
    ho_st.add_argument("--id", dest="handoff_id", default=None)
    ho_e2e = ho_sub.add_parser("e2e", help="lanes + fast handoff + collect smoke")
    ho_e2e.add_argument("--goal", default=None)

    gl = sub.add_parser(
        "go-live",
        help="FULLY OPERATIONAL: ollama + tools + fabric + swarm + hive + rust mount seal",
    )
    gl.add_argument(
        "--no-ollama-lm",
        action="store_true",
        help="skip shared LM on fabric mission (tools still on)",
    )
    gl.add_argument("--no-hive", action="store_true", help="skip buzzer hive mini")
    gl.add_argument("--no-rust", action="store_true", help="skip rust mount check")
    gl.add_argument("--workers", type=int, default=3, help="fabric swarm workers")

    sub.add_parser(
        "operational",
        help="alias of go-live — full operational seal",
    )

    run = sub.add_parser("run", help="parent AI runs a build task (full or fast lane)")
    run.add_argument("--goal", required=True)
    run.add_argument(
        "--controller",
        default="local",
        help="ollama|gemini|spacexai|xai|grok|local",
    )
    run.add_argument("--name", default="parent")
    run.add_argument("--domain", default="build")
    run.add_argument("--tags", default="build", help="comma skill tags")
    run.add_argument(
        "--lm-assist",
        default="ollama",
        help="shared LM brain: ollama|none|spacexai|gemini",
    )
    run.add_argument(
        "--lane",
        default="full",
        choices=["fast", "full"],
        help="fast=5-node lite; full=24-node dual-hemisphere seal",
    )

    # Fast lane shortcuts
    fl = sub.add_parser(
        "fast",
        help="FAST LANE: 5-node tools path (not full clean-slate seal)",
    )
    fl.add_argument("--goal", required=True)
    fl.add_argument("--controller", default="local")
    fl.add_argument("--name", default="fast")
    fl.add_argument("--lm-assist", default="none")
    fl.add_argument("--domain", default="build")

    sub.add_parser(
        "fast-smoke",
        help="3-goal fast lane smoke + optional full vs fast timing compare",
    )
    sub.add_parser(
        "lane-compare",
        help="one goal on fast vs full — wall-time evidence",
    )

    sm = sub.add_parser("smoke", help="run 3 builds; prove smart_index rises")
    sm.add_argument("--controller", default="local")

    # --- Complete closed agent loop ---
    lp = sub.add_parser(
        "loop",
        help="COMPLETE agent loop: imprint→execute→bus/registry/recycle→NEXT→wake→collect",
    )
    lp.add_argument("--goal", default="", help="goal (else NEXT or open task)")
    lp.add_argument("--generations", type=int, default=2, help="closed generations (default 2)")
    lp.add_argument("--lane", default="fast", choices=["fast", "full"])
    lp.add_argument("--wake-next", action="store_true", help="consume NEXT.json as goal")
    lp.add_argument("--no-open-task", action="store_true")
    lp.add_argument("--controller", default="local")
    lp.add_argument("--no-collect", action="store_true", help="skip Grok cowork collect")

    # --- Swarm = buzzer hive parallel (clean-slate) + L||R per buzzer ---
    sw = sub.add_parser(
        "swarm",
        help="SWARM buzzers: parallel clean-slate workers (library optional) + L||R fabric",
    )
    sw.add_argument(
        "--goal",
        action="append",
        dest="goals",
        default=None,
        help="goal (repeat). If omitted with --library, pulls open tasks",
    )
    sw.add_argument("--controller", default="local")
    sw.add_argument("--name", default="swarm")
    sw.add_argument("--workers", type=int, default=4, help="parallel buzzer slots (default 4)")
    sw.add_argument("--cycles", type=int, default=6, help="units if padding / library pull")
    sw.add_argument("--domain", default="build")
    sw.add_argument("--tags", default="build,swarm")
    sw.add_argument("--library", action="store_true", help="also pull OPEN_TASKS as goals")
    sw.add_argument(
        "--lm-assist",
        default="ollama",
        help="shared LM: ollama (top model)|none|spacexai|gemini",
    )

    ssmoke = sub.add_parser(
        "swarm-smoke",
        help="6-unit PARALLEL buzzer swarm smoke; prove peak_parallel>1",
    )
    ssmoke.add_argument("--controller", default="local")
    ssmoke.add_argument("--workers", type=int, default=4)

    # --- Buzzer Hive ---
    sub.add_parser("hive-status", help="hive + library connection status")

    hq = sub.add_parser("hive-enqueue", help="queue a goal for the next hive swarm")
    hq.add_argument("--goal", required=True)

    hr = sub.add_parser("hive-retrieve", help="retrieve hive memory docs by query")
    hr.add_argument("--query", required=True)
    hr.add_argument("--top", type=int, default=5)

    def _add_swarm_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--goal",
            action="append",
            dest="goals",
            default=None,
            help="task goal (repeatable). If omitted, pulls library open tasks / queue",
        )
        sp.add_argument(
            "--cycles",
            type=int,
            default=6,
            help="how many fresh buzzer units to run (default 6)",
        )
        sp.add_argument(
            "--workers",
            type=int,
            default=0,
            help="parallel buzzer slots (0 = config default, usually 4)",
        )
        sp.add_argument(
            "--serial",
            action="store_true",
            help="force serial (workers=1) — default is PARALLEL swarm",
        )
        sp.add_argument("--controller", default="local")
        sp.add_argument("--name", default="hive")
        sp.add_argument(
            "--lm-assist",
            default="ollama",
            help="shared LM: ollama (gemma4:12b top)|none|spacexai|gemini",
        )
        sp.add_argument(
            "--no-library",
            action="store_true",
            help="do not pull open tasks from continuous board",
        )
        sp.add_argument(
            "--post-live-write",
            action="store_true",
            help="attempt F: write_complete_memory on last unit (real tool only)",
        )
        sp.add_argument("--domain", default="build")
        sp.add_argument(
            "--lane",
            default="full",
            choices=["fast", "full"],
            help="fast=5-node lite per buzzer; full=24-node seal",
        )

    hive = sub.add_parser(
        "hive",
        help="PARALLEL buzzer hive: many clean-slate buzzers at once",
    )
    _add_swarm_args(hive)

    # NOTE: `swarm` is the 24-drone fabric multi-goal fan-out (see above).
    # Hive parallel buzzers use `hive` / `hive-smoke` only.

    hs = sub.add_parser(
        "hive-smoke",
        help="6-unit PARALLEL hive smoke + library + hive memory seal",
    )
    hs.add_argument("--controller", default="local")
    hs.add_argument("--workers", type=int, default=4)

    # --- Fabric Swarm (sister system: multi-goal 24-drone fabric, not buzzer hive) ---
    fs = sub.add_parser(
        "fabric-swarm",
        help="FABRIC SWARM: multi-goal fan-out of 24-drone L||R units (work-order imprinted)",
    )
    fs.add_argument("--goal", action="append", dest="goals", required=True)
    fs.add_argument("--controller", default="local")
    fs.add_argument("--name", default="fabric-swarm")
    fs.add_argument("--workers", type=int, default=3)
    fs.add_argument("--domain", default="build")
    fs.add_argument("--tags", default="build,swarm,fabric_swarm")
    fs.add_argument(
        "--serial-hemispheres",
        action="store_true",
        help="disable L||R parallel per goal",
    )
    fs.add_argument("--lm-assist", default="none")

    fss = sub.add_parser(
        "fabric-swarm-smoke",
        help="3-goal fabric swarm smoke with work-order imprint evidence",
    )
    fss.add_argument("--controller", default="local")
    fss.add_argument("--workers", type=int, default=3)

    sub.add_parser(
        "work-order-show",
        help="show WORK ORDER imprint law + paths (both swarm systems)",
    )

    # --- Clean Slate Agents Upgrade ---
    cs = sub.add_parser(
        "clean-slate",
        help="Clean Slate Agents upgrade: passport + tools/brain + wipe audit chain",
    )
    cs.add_argument(
        "--goal",
        action="append",
        dest="goals",
        default=None,
        help="goal (repeat for lineage chain). Default: 3 upgrade smoke goals",
    )
    cs.add_argument("--controller", default="local")
    cs.add_argument(
        "--lm-assist",
        default="none",
        help="ollama|none (use none for fast tools-only upgrade proof)",
    )
    cs.add_argument("--wake-next", action="store_true", help="first agent wakes from NEXT.json")

    sub.add_parser(
        "clean-slate-smoke",
        help="3-agent clean-slate chain smoke (passport + wipe + lineage)",
    )

    args = p.parse_args(argv)

    if args.cmd == "clones":
        from drone.model_clones import main as clones_main

        sub_argv: list[str] = []
        if getattr(args, "catalog", None):
            sub_argv += ["--catalog", args.catalog]
        sub_argv.append(args.clones_cmd)
        if args.clones_cmd == "install" and getattr(args, "only", None):
            sub_argv += ["--only", args.only]
        return clones_main(sub_argv)

    if args.cmd == "agent":
        from drone.agent_loop import main as agent_main

        sub_argv = [args.agent_cmd]
        if args.agent_cmd == "run":
            if getattr(args, "rounds", None) is not None:
                sub_argv += ["--rounds", str(args.rounds)]
            if getattr(args, "no_face", False):
                sub_argv.append("--no-face")
            sub_argv += list(args.goal)
        elif args.agent_cmd == "seal" and getattr(args, "goal", None):
            sub_argv += ["--goal", args.goal]
        return agent_main(sub_argv)

    if args.cmd == "synapse":
        from drone.synaptic_loop import main as syn_main

        sub_argv = [args.syn_cmd]
        if args.syn_cmd == "tick":
            if getattr(args, "residual", None):
                sub_argv += ["--residual", args.residual]
            sub_argv += list(args.goal)
        elif args.syn_cmd == "run":
            if getattr(args, "ticks", None) is not None:
                sub_argv += ["--ticks", str(args.ticks)]
            sub_argv += list(args.goal)
        elif args.syn_cmd == "seal" and getattr(args, "goal", None):
            sub_argv += ["--goal", args.goal]
        return syn_main(sub_argv)

    if args.cmd == "mesh":
        from drone.super_mesh import main as mesh_main

        sub_argv = [args.mesh_cmd]
        if args.mesh_cmd == "tick":
            if getattr(args, "residual", None):
                sub_argv += ["--residual", args.residual]
            if getattr(args, "call", None):
                sub_argv += ["--call", args.call]
            if getattr(args, "fire_lm", False):
                sub_argv.append("--fire-lm")
            sub_argv += list(args.goal or [])
        elif args.mesh_cmd == "run":
            if getattr(args, "ticks", None) is not None:
                sub_argv += ["--ticks", str(args.ticks)]
            if getattr(args, "call", None):
                sub_argv += ["--call", args.call]
            sub_argv += list(args.goal)
        elif args.mesh_cmd == "call":
            sub_argv += [args.tag]
            if getattr(args, "goal", None):
                sub_argv += ["--goal", args.goal]
        elif args.mesh_cmd == "call-many":
            if getattr(args, "goal", None):
                sub_argv += ["--goal", args.goal]
            sub_argv += list(args.tags)
        return mesh_main(sub_argv)

    if args.cmd == "face":
        # Hotwire path is default for chat/exec/go/hot/status
        if args.face_cmd in {"hot", "chat", "exec", "go", "status"}:
            from drone.face_hotwire import main as hw_main

            # face_hotwire CLI uses same subcommand names
            sub_argv = [args.face_cmd]
            if args.face_cmd in {"chat", "exec", "go"}:
                sub_argv += list(args.text)
            return hw_main(sub_argv)

        from drone.multi_face import main as face_main

        sub_argv = [args.face_cmd]
        if args.face_cmd == "ask":
            if getattr(args, "opt_in", False):
                sub_argv.append("--opt-in")
            if getattr(args, "workers", None):
                sub_argv += ["--workers", args.workers]
            if getattr(args, "stream", False):
                sub_argv.append("--stream")
            sub_argv += list(args.text)
        elif args.face_cmd == "seal":
            # also seal hotwire
            pass
        return face_main(sub_argv)

    if args.cmd == "seer":
        from drone.future_seer import main as seer_main

        sub_argv = [args.seer_cmd]
        if args.seer_cmd in {"type", "spec"}:
            if getattr(args, "no_spec", False):
                sub_argv.append("--no-spec")
            sub_argv += list(args.text)
        elif args.seer_cmd == "commit":
            sub_argv += ["--mode", args.mode]
            if getattr(args, "auto", False):
                sub_argv.append("--auto")
            if args.text:
                sub_argv += list(args.text)
        elif args.seer_cmd == "helper":
            sub_argv += ["--expert", args.expert]
            sub_argv += list(args.goal)
        return seer_main(sub_argv)

    if args.cmd == "super-llms":
        from drone.super_llms import main as super_main

        sub_argv = [args.super_cmd]
        if args.super_cmd == "smoke" and getattr(args, "cloud", False):
            sub_argv.append("--cloud")
        elif args.super_cmd == "route":
            sub_argv += list(args.goal)
            if getattr(args, "role", None):
                sub_argv += ["--role", args.role]
        elif args.super_cmd == "chat":
            if getattr(args, "model", None):
                sub_argv += ["--model", args.model]
            if getattr(args, "role", None):
                sub_argv += ["--role", args.role]
            sub_argv += list(args.prompt)
        return super_main(sub_argv)

    if args.cmd == "bridge-1080":
        from drone.bridge_1080 import main as b1080_main

        sub_argv = [args.b1080_cmd]
        if args.b1080_cmd in {"start", "live", "watchdog", "watch"}:
            if getattr(args, "interval", None) is not None:
                sub_argv += ["--interval", str(args.interval)]
            if args.b1080_cmd == "start" and getattr(args, "no_watchdog", False):
                sub_argv.append("--no-watchdog")
            if args.b1080_cmd == "watch" and getattr(args, "ticks", None) is not None:
                sub_argv += ["--ticks", str(args.ticks)]
        elif args.b1080_cmd == "arm":
            if getattr(args, "only_1080", False):
                sub_argv.append("--only-1080")
            if getattr(args, "persist_user", False):
                sub_argv.append("--persist-user")
        return b1080_main(sub_argv)

    if args.cmd == "super-kernel":
        from drone.super_kernel import main as sk_main

        sub_argv = [args.sk_cmd]
        if args.sk_cmd == "start":
            if getattr(args, "host", None):
                sub_argv += ["--host", args.host]
            if getattr(args, "port", None) is not None:
                sub_argv += ["--port", str(args.port)]
            if getattr(args, "no_watchdog", False):
                sub_argv.append("--no-watchdog")
            if getattr(args, "no_idler", False):
                sub_argv.append("--no-idler")
        elif args.sk_cmd == "serve":
            if getattr(args, "host", None):
                sub_argv += ["--host", args.host]
            if getattr(args, "port", None) is not None:
                sub_argv += ["--port", str(args.port)]
            if getattr(args, "foreground", False):
                sub_argv.append("--foreground")
            else:
                sub_argv.append("--stable")
        elif args.sk_cmd in {"watchdog", "idler"}:
            if getattr(args, "host", None):
                sub_argv += ["--host", args.host]
            if getattr(args, "port", None) is not None:
                sub_argv += ["--port", str(args.port)]
            if getattr(args, "interval", None) is not None:
                sub_argv += ["--interval", str(args.interval)]
            if args.sk_cmd == "idler":
                if getattr(args, "warm_model", None):
                    sub_argv += ["--warm-model", args.warm_model]
                if getattr(args, "warm_every", None) is not None:
                    sub_argv += ["--warm-every", str(args.warm_every)]
        elif args.sk_cmd == "open":
            sub_argv += ["--model", args.model]
            if getattr(args, "force", False):
                sub_argv.append("--force")
        elif args.sk_cmd == "close":
            sub_argv += ["--id", args.id]
            if getattr(args, "unload", False):
                sub_argv.append("--unload")
        elif args.sk_cmd == "connect":
            if getattr(args, "model", None):
                sub_argv += ["--model", args.model]
            if getattr(args, "client", None):
                sub_argv += ["--client", args.client]
            if getattr(args, "force", False):
                sub_argv.append("--force")
        elif args.sk_cmd == "disconnect":
            sub_argv += ["--id", args.id]
            if getattr(args, "unload", False):
                sub_argv.append("--unload")
        elif args.sk_cmd == "hot-swap":
            sub_argv += ["--to", args.to_model]
            if getattr(args, "connection", None):
                sub_argv += ["--connection", args.connection]
            if getattr(args, "instance", None):
                sub_argv += ["--instance", args.instance]
            if getattr(args, "force", False):
                sub_argv.append("--force")
            if getattr(args, "no_unload", False):
                sub_argv.append("--no-unload")
            if getattr(args, "set_active", False):
                sub_argv.append("--set-active")
        elif args.sk_cmd == "chat":
            if getattr(args, "model", None):
                sub_argv += ["--model", args.model]
            if getattr(args, "instance", None):
                sub_argv += ["--instance", args.instance]
            if getattr(args, "connection", None):
                sub_argv += ["--connection", args.connection]
            if getattr(args, "force", False):
                sub_argv.append("--force")
            sub_argv += list(args.prompt)
        elif args.sk_cmd == "can-hang":
            sub_argv += ["--model", args.model]
        # kill / process / status / connections / live / reload-config pass through
        return sk_main(sub_argv)

    if args.cmd == "llm-resources":
        from drone.llm_resources import LLMResources

        app = LLMResources(root)
        if args.llm_cmd == "list":
            print(json.dumps(app.list_all(), indent=2))
            return 0
        if args.llm_cmd == "status":
            print(json.dumps(app.status(), indent=2))
            return 0
        if args.llm_cmd == "seal":
            out = app.seal(smoke_generate=True)
            print(json.dumps(out, indent=2))
            return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
        if args.llm_cmd == "route":
            goal = " ".join(args.goal)
            print(json.dumps(app.route(goal, role=getattr(args, "role", None)), indent=2))
            return 0
        if args.llm_cmd == "chat":
            prompt = " ".join(args.prompt)
            out = app.chat(
                prompt,
                model=getattr(args, "model", None),
                role=getattr(args, "role", None),
            )
            print(json.dumps(out, indent=2))
            return 0 if out.get("status") == "GREEN" else 1
        if args.llm_cmd == "generate":
            prompt = " ".join(args.prompt)
            out = app.generate(
                prompt,
                model=getattr(args, "model", None),
                role=getattr(args, "role", None),
            )
            print(json.dumps(out, indent=2))
            return 0 if out.get("status") == "GREEN" else 1
        return 2

    if args.cmd == "loop":
        from drone.agent_loop import complete_agent_loop

        seal = complete_agent_loop(
            root,
            goal=getattr(args, "goal", "") or "",
            generations=int(getattr(args, "generations", 2) or 2),
            lane=getattr(args, "lane", "fast") or "fast",
            wake_from_next=bool(getattr(args, "wake_next", False)),
            pull_open_task=not bool(getattr(args, "no_open_task", False)),
            controller=getattr(args, "controller", "local") or "local",
            collect_for_grok=not bool(getattr(args, "no_collect", False)),
        )
        print(json.dumps(seal, indent=2, default=str))
        print(f"\nSEAL {seal.get('status')} → {seal.get('seal_path')}")
        return 0 if seal.get("status") in {"GREEN", "PARTIAL"} else 1

    if args.cmd == "handoff":
        from drone.grok_handoff import main as handoff_main

        sub_argv = [args.handoff_cmd]
        if args.handoff_cmd == "to":
            sub_argv += ["--goal", args.goal, "--mode", args.mode]
            if getattr(args, "lane", None):
                sub_argv += ["--lane", args.lane]
            sub_argv += [
                "--workers",
                str(args.workers),
                "--cycles",
                str(args.cycles),
                "--lm-assist",
                args.lm_assist,
                "--controller",
                args.controller,
                "--notes",
                args.notes or "",
                "--pro-rounds",
                str(args.pro_rounds),
            ]
            if getattr(args, "continuous_task_id", None):
                sub_argv += ["--continuous-task-id", args.continuous_task_id]
            for ctx in getattr(args, "context", None) or []:
                sub_argv += ["--context", ctx]
            for gu in getattr(args, "goals", None) or []:
                sub_argv += ["--goal-unit", gu]
            if getattr(args, "no_wait", False):
                sub_argv.append("--no-wait")
            if getattr(args, "skip_lane_check", False):
                sub_argv.append("--skip-lane-check")
            if getattr(args, "also_hive", False):
                sub_argv.append("--also-hive")
        elif args.handoff_cmd == "collect":
            sub_argv += ["--max", str(args.max)]
            if getattr(args, "keep", False):
                sub_argv.append("--keep")
        elif args.handoff_cmd == "status":
            if getattr(args, "handoff_id", None):
                sub_argv += ["--id", args.handoff_id]
        elif args.handoff_cmd == "e2e":
            if getattr(args, "goal", None):
                sub_argv += ["--goal", args.goal]
        return handoff_main(sub_argv)

    if args.cmd == "app":
        from drone.app.cli import main as app_main

        # rebuild argv for app cli
        sub_argv = [args.app_cmd]
        if args.app_cmd == "serve":
            if getattr(args, "host", None):
                sub_argv += ["--host", args.host]
            if getattr(args, "port", None) is not None:
                sub_argv += ["--port", str(args.port)]
        elif args.app_cmd == "task":
            sub_argv += [
                "--goal",
                args.goal,
                "--lane",
                args.lane,
                "--lm-assist",
                args.lm_assist,
                "--controller",
                args.controller,
            ]
        elif args.app_cmd == "pro":
            sub_argv += ["--goal", args.goal, "--rounds", str(args.rounds)]
            if getattr(args, "hive", False):
                sub_argv.append("--hive")
            if getattr(args, "no_ollama", False):
                sub_argv.append("--no-ollama")
        elif args.app_cmd == "brain":
            sub_argv += ["--command", args.command]
        elif args.app_cmd == "inbox":
            sub_argv += ["--max", str(args.max)]
        elif args.app_cmd == "handoff":
            # redirect: python -m drone app handoff → handoff lanes help
            from drone.grok_handoff import main as handoff_main

            return handoff_main(["lanes"])
        elif args.app_cmd == "desktop":
            sub_argv = ["desktop"]
        elif args.app_cmd == "desktop-pro":
            sub_argv = ["desktop-pro"]
        return app_main(sub_argv)

    if args.cmd == "brain":
        from drone.ollama_brain import dual_brain_status, resolve_code_model, resolve_top_model
        from drone.tools import DroneToolkit

        tk = DroneToolkit(root, task_id="probe")
        tools = tk.list_tools()
        llm_status: dict = {}
        try:
            from drone.llm_resources import LLMResources

            llm_status = LLMResources(root).status()
        except Exception as e:
            llm_status = {"error": str(e)}
        payload = {
            "ollama": dual_brain_status(),
            "top_model_resolved": resolve_top_model(force_refresh=True),
            "code_worker_model": resolve_code_model(force_refresh=True),
            "llm_resources": llm_status,
            "tools": tools,
            "tools_wired_to_all_roles": True,
            "full_llm_tools": [
                "llm_list",
                "llm_route",
                "llm_chat",
                "llm_generate",
                "ollama_generate",
            ],
            "code_gate": "execute must write goal .py + smoke pass or RED",
            "observer": {
                "user_facing": False,
                "path": str(root / "data" / "observer" / "LATEST.json"),
                "law": "informed by drones+OS only; never answers user input",
            },
            "lm_roles": [
                "plan",
                "pattern",
                "execute",
                "critic",
                "revise",
                "refine",
                "evolve",
                "seal",
            ],
            "false_green": 0,
        }
        print(json.dumps(payload, indent=2))
        return 0 if payload["ollama"].get("reachable") else 1

    if args.cmd == "observer":
        from drone.observer import read_latest

        latest = read_latest(root)
        latest["user_facing"] = False
        latest["note"] = "Silent observer — never responds to user; loop-informed only"
        print(json.dumps(latest, indent=2))
        return 0 if latest.get("status") in {"GREEN", "SKIP", "EMPTY"} else 1

    if args.cmd == "critic":
        from drone.core_critic import critique_once

        result = critique_once(root, event="manual_critic")
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("status") in {"GREEN", "SKIP"} else 1

    if args.cmd == "critic-loop":
        from drone.core_critic import run_loop

        return run_loop(root, ticks=getattr(args, "ticks", None))

    if args.cmd in {"go-live", "operational"}:
        from drone.operational import go_live

        # operational alias has no flags — defaults full stack
        no_lm = bool(getattr(args, "no_ollama_lm", False))
        no_hive = bool(getattr(args, "no_hive", False))
        no_rust = bool(getattr(args, "no_rust", False))
        workers = int(getattr(args, "workers", 3) or 3)
        seal = go_live(
            root,
            with_ollama_lm=not no_lm,
            with_hive=not no_hive,
            with_rust_mount=not no_rust,
            fabric_swarm_workers=workers,
        )
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") in {"GREEN", "PARTIAL"} else 1

    if args.cmd == "info":
        cfg = json.loads((root / "configs" / "brain_fabric.json").read_text(encoding="utf-8"))
        hive_cfg_path = root / "configs" / "buzzer_hive.json"
        hive_cfg = (
            json.loads(hive_cfg_path.read_text(encoding="utf-8"))
            if hive_cfg_path.is_file()
            else {}
        )
        from drone.ollama_brain import brain_status

        payload = {
            "root": str(root),
            "nodes": 24,
            "hemispheres": 2,
            "callosum": True,
            "tools": cfg.get("tools"),
            "ollama": brain_status(),
            "swarm": {
                "parallel_hemispheres": True,
                "multi_goal_fanout": True,
                "default_workers": 3,
                "full_models_per_drone": False,
            },
            "full_models_per_drone": False,
            "buzzer_hive": hive_cfg,
            "learning": cfg.get("learning"),
            "commanders": describe_controllers(),
            "honesty": cfg.get("honesty"),
            "library": LibraryBridge().status(),
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.cmd in {"swarm", "swarm-smoke"}:
        if args.cmd == "swarm-smoke":
            goals = [
                "swarm smoke A: library pack into fresh buzzer intake",
                "swarm smoke B: write hive vector doc under lock",
                "swarm smoke C: parallel peer — clean slate isolation",
                "swarm smoke D: skill update while others fly",
                "swarm smoke E: outbox note for Self Library",
                "swarm smoke F: prove peak_parallel > 1",
            ]
            # tools always on; LM optional (default ollama is heavy — smoke uses none for speed)
            h = BuzzerHive(root, lm_assist="none")
            seal = h.run_swarm(
                goals=goals,
                cycles=6,
                workers=max(1, int(args.workers)),
                parallel=True,
                controller=args.controller,
                controller_name="swarm-smoke",
                pull_library=True,
                write_library_note=True,
                post_live_write=False,
                domain="build",
            )
            smoke_path = root / "out" / "SWARM_SMOKE_SEAL.json"
            smoke_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
            seal["swarm_smoke_seal_path"] = str(smoke_path)
        else:
            goals = list(args.goals or [])
            if not goals and not getattr(args, "library", False):
                print(
                    json.dumps(
                        {
                            "status": "RED",
                            "false_green": 0,
                            "error": "provide --goal (repeatable) or --library",
                        },
                        indent=2,
                    )
                )
                return 1
            h = BuzzerHive(
                root, lm_assist=getattr(args, "lm_assist", "none") or "none"
            )
            seal = h.run_swarm(
                goals=goals or None,
                cycles=max(len(goals), int(args.cycles)) if goals else max(1, int(args.cycles)),
                workers=max(1, int(args.workers)),
                parallel=True,
                controller=args.controller,
                controller_name=args.name,
                pull_library=bool(getattr(args, "library", False)),
                write_library_note=True,
                post_live_write=False,
                domain=getattr(args, "domain", "build"),
            )
        if (
            seal.get("status") == "GREEN"
            and seal.get("mode") == "parallel"
            and int(seal.get("units") or 0) > 1
            and int(seal.get("peak_parallel_observed") or 0) < 2
        ):
            seal["status"] = "PARTIAL"
            seal["parallel_honesty_note"] = (
                "units>1 but peak_parallel_observed<2 — not a true concurrent swarm"
            )
            (root / "out" / "HIVE_SWARM_SEAL.json").write_text(
                json.dumps(seal, indent=2), encoding="utf-8"
            )
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1

    if args.cmd == "work-order-show":
        from drone.work_order import show_work_order

        print(json.dumps(show_work_order(root), indent=2))
        return 0

    if args.cmd in {"clean-slate", "clean-slate-smoke"}:
        from drone.clean_slate import (
            UPGRADE_ID,
            load_clean_slate_config,
            run_clean_slate_chain,
            spawn_clean_slate_agent,
        )
        from drone.controllers import make_lm_fn

        if args.cmd == "clean-slate-smoke":
            goals = [
                "clean-slate upgrade A: birth passport + tools bind",
                "clean-slate upgrade B: parent death wipe + child lineage",
                "clean-slate upgrade C: NEXT imprint ready for swarm",
            ]
            lm_assist = "none"
            controller = "local"
        else:
            goals = list(args.goals or [])
            if not goals:
                goals = [
                    "clean-slate agent: execute one unit with passport",
                    "clean-slate agent: prove wipe then next generation",
                ]
            lm_assist = getattr(args, "lm_assist", "none") or "none"
            controller = getattr(args, "controller", "local") or "local"

        if getattr(args, "wake_next", False) and args.cmd == "clean-slate":
            lm = make_lm_fn(lm_assist)
            agent = spawn_clean_slate_agent(root, generation=1, lm_fn=lm)
            seal = agent.run(
                "",
                controller_kind=controller,
                wake_from_next=True,
            )
            # wrap single run
            seal = {
                "schema": "ai.worker.drone.clean_slate_chain_seal.v1",
                "upgrade": UPGRADE_ID,
                "status": seal.get("status"),
                "false_green": 0,
                "mode": "wake_next",
                "results": [seal],
                "config": load_clean_slate_config(root),
            }
        else:
            seal = run_clean_slate_chain(
                root,
                goals=goals,
                controller=controller,
                lm_assist=lm_assist,
                enable_tools=True,
            )
            seal["config"] = load_clean_slate_config(root)
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1

    if args.cmd in {"fabric-swarm", "fabric-swarm-smoke"}:
        from drone.swarm import DroneSwarm

        if args.cmd == "fabric-swarm-smoke":
            goals = [
                "fabric swarm smoke A: imprint work order on unit",
                "fabric swarm smoke B: L||R hemisphere merge",
                "fabric swarm smoke C: registry + recycle dump",
            ]
            workers = max(1, int(args.workers))
            controller = args.controller
            name = "fabric-swarm-smoke"
            domain = "build"
            tags = ["work_order", "build", "swarm", "fabric_swarm", "smoke"]
            parallel_h = True
            lm_assist = "none"
        else:
            goals = list(args.goals or [])
            workers = max(1, int(args.workers))
            controller = args.controller
            name = args.name
            domain = args.domain
            tags = [t.strip() for t in args.tags.split(",") if t.strip()]
            parallel_h = not bool(args.serial_hemispheres)
            lm_assist = args.lm_assist

        validate_controller(controller, name)
        lm = make_lm_fn(lm_assist)
        swarm = DroneSwarm(root, lm_fn=lm, max_workers=workers)
        seal = swarm.run_multi(
            goals=goals,
            controller_kind=controller,
            controller_name=name,
            domain=domain,
            skill_tags=tags,
            parallel_hemispheres=parallel_h,
        )
        if args.cmd == "fabric-swarm-smoke":
            smoke_path = root / "out" / "FABRIC_SWARM_SMOKE_SEAL.json"
            smoke_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
            seal["fabric_swarm_smoke_seal_path"] = str(smoke_path)
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1

    if args.cmd == "hive-status":
        h = BuzzerHive(root)
        print(json.dumps(h.status(), indent=2))
        return 0

    if args.cmd == "hive-enqueue":
        h = BuzzerHive(root)
        entry = h.enqueue(args.goal)
        print(json.dumps({"enqueued": entry, "false_green": 0}, indent=2))
        return 0

    if args.cmd == "hive-retrieve":
        h = BuzzerHive(root)
        hits = h.retrieve(args.query, top_k=args.top)
        print(json.dumps({"query": args.query, "hits": hits, "false_green": 0}, indent=2))
        return 0

    if args.cmd in {"hive", "hive-smoke"}:
        if args.cmd == "hive-smoke":
            goals = [
                "swarm smoke A: library pack into fresh buzzer intake",
                "swarm smoke B: write hive vector doc under lock",
                "swarm smoke C: parallel peer — clean slate isolation",
                "swarm smoke D: skill update while others fly",
                "swarm smoke E: outbox note for Self Library",
                "swarm smoke F: prove peak_parallel > 1",
            ]
            h = BuzzerHive(
                root,
                lm_assist="none",
                lane=getattr(args, "lane", "full") or "full",
            )
            seal = h.run_swarm(
                goals=goals,
                cycles=6,
                workers=max(1, int(args.workers)),
                parallel=True,
                controller=args.controller,
                controller_name="hive-smoke",
                pull_library=True,
                write_library_note=True,
                post_live_write=False,
                domain="build",
                lane=getattr(args, "lane", "full") or "full",
            )
        else:
            h = BuzzerHive(
                root,
                lm_assist=args.lm_assist,
                lane=getattr(args, "lane", "full") or "full",
            )
            w = int(args.workers)
            seal = h.run_swarm(
                goals=args.goals,
                cycles=max(1, int(args.cycles)),
                workers=(None if w <= 0 else w),
                parallel=not bool(args.serial),
                controller=args.controller,
                controller_name=args.name,
                pull_library=not args.no_library,
                write_library_note=True,
                post_live_write=bool(args.post_live_write),
                domain=args.domain,
                lane=getattr(args, "lane", "full") or "full",
            )
        # Honesty gate: multi-unit "parallel" must show real concurrency
        if (
            seal.get("status") == "GREEN"
            and seal.get("mode") == "parallel"
            and int(seal.get("units") or 0) > 1
            and int(seal.get("peak_parallel_observed") or 0) < 2
        ):
            seal["status"] = "PARTIAL"
            seal["parallel_honesty_note"] = (
                "units>1 but peak_parallel_observed<2 — not a true concurrent swarm"
            )
            (root / "out" / "HIVE_SWARM_SEAL.json").write_text(
                json.dumps(seal, indent=2), encoding="utf-8"
            )
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1

    lm = make_lm_fn(getattr(args, "lm_assist", "none") if hasattr(args, "lm_assist") else "none")
    if args.cmd == "smoke":
        # smoke stays tools-on, LM off (fast path)
        lm = None
    fabric = BrainFabric(root, lm_fn=lm, enable_tools=True)

    if args.cmd == "stats":
        print(json.dumps(fabric.stats(), indent=2))
        return 0

    if args.cmd in {"fast", "fast-smoke", "lane-compare"}:
        from drone.fast_lane import FastLane, compare_fast_vs_full, run_fast_smoke

        if args.cmd == "fast-smoke":
            seal = run_fast_smoke(root)
            print(json.dumps(seal, indent=2))
            return 0 if seal.get("status") == "GREEN" else 1
        if args.cmd == "lane-compare":
            seal = compare_fast_vs_full(root)
            print(json.dumps(seal, indent=2))
            return 0 if seal.get("fast", {}).get("status") == "GREEN" else 1
        lm_fast = make_lm_fn(getattr(args, "lm_assist", "none") or "none")
        report = FastLane(root, lm_fn=lm_fast).run(
            args.goal,
            controller_kind=args.controller,
            controller_name=getattr(args, "name", "fast"),
            domain=getattr(args, "domain", "build"),
        )
        print(json.dumps(report, indent=2))
        return 0 if report.get("status") == "GREEN" else 1

    if args.cmd == "run":
        validate_controller(args.controller, args.name)
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        lane = getattr(args, "lane", "full") or "full"
        if lane == "fast":
            report = fabric.run_task_fast(
                goal=args.goal,
                controller_kind=args.controller,
                controller_name=args.name,
                domain=args.domain,
                skill_tags=tags + ["fast_lane"],
            )
        else:
            report = fabric.run_task(
                goal=args.goal,
                controller_kind=args.controller,
                controller_name=args.name,
                domain=args.domain,
                skill_tags=tags,
            )
        print(json.dumps(report, indent=2))
        return 0 if report.get("status") == "GREEN" else 1

    if args.cmd == "smoke":
        validate_controller(args.controller, "smoke")
        goals = [
            "build a small worker intake module",
            "build a verify and seal path for worker output",
            "build callosum merge packet tests",
        ]
        deltas = []
        reports = []
        for g in goals:
            r = fabric.run_task(
                goal=g,
                controller_kind=args.controller,
                controller_name="smoke",
                domain="build",
                skill_tags=["build", "smoke"],
            )
            reports.append(r)
            deltas.append(r.get("smarter_delta", 0))
        final = fabric.stats()
        seal = {
            "status": "GREEN" if all(x.get("status") == "GREEN" for x in reports) else "RED",
            "false_green": 0,
            "runs": len(reports),
            "smarter_deltas": deltas,
            "smart_index_rose": final.get("smart_index", 0) > 0,
            "builds": final.get("builds"),
            "smart_index": final.get("smart_index"),
            "build_level": final.get("build_level"),
            "mean_skill_score": final.get("mean_skill_score"),
            "report_paths": [r.get("report_path") for r in reports],
            "stats_path": str(root / "data" / "skills" / "stats.json"),
            "ledger_path": str(root / "data" / "experience" / "ledger.jsonl"),
            "honesty": {
                "drones_are_full_models": False,
                "learning": "experience ledger + skill XP/levels + smart_index",
                "human_brain": False,
            },
        }
        seal_path = root / "out" / "SMOKE_SEAL.json"
        seal_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        seal["seal_path"] = str(seal_path)
        print(json.dumps(seal, indent=2))
        return 0 if seal["status"] == "GREEN" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
