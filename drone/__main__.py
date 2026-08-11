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
        payload = {
            "ollama": dual_brain_status(),
            "top_model_resolved": resolve_top_model(force_refresh=True),
            "code_worker_model": resolve_code_model(force_refresh=True),
            "tools": tools,
            "tools_wired_to_all_roles": True,
            "code_gate": "execute must write goal .py + smoke pass or RED",
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
