"""CLI entry for installed console script: dronehive / drone-hive."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    # Ensure project root on path when run as installed or module
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    p = argparse.ArgumentParser(prog="dronehive", description="DroneHive standalone modular app")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="app health JSON")
    sub.add_parser("status", help="fabric + hive status")
    sub.add_parser("links", help="list universal links")
    sub.add_parser("links-probe", help="probe all link adapters")
    sub.add_parser("commission", help="full commission seal")
    sub.add_parser(
        "desktop",
        help="NATIVE desktop GUI (CustomTkinter) — not a browser",
    )
    sub.add_parser(
        "desktop-pro",
        help="DroneHive Pro v2 desktop — free-form tools + premium UI",
    )

    srv = sub.add_parser("serve", help="optional HTTP API only (not primary UI)")
    srv.add_argument("--host", default=None)
    srv.add_argument("--port", type=int, default=None)

    task = sub.add_parser("task", help="run a goal via service facade")
    task.add_argument("--goal", required=True)
    task.add_argument("--lane", default="fast", choices=["fast", "full"])
    task.add_argument("--lm-assist", default="none")
    task.add_argument("--controller", default="local")

    brain = sub.add_parser(
        "brain",
        help="MAIN PATH: user command → Ollama plan → swarm delegate",
    )
    brain.add_argument("--command", "-c", required=True, help="user command text")

    pro = sub.add_parser(
        "pro",
        help="PRO v2: free-form Ollama tool agent (real files)",
    )
    pro.add_argument("--goal", "-g", required=True, help="user goal")
    pro.add_argument("--rounds", type=int, default=8)
    pro.add_argument(
        "--hive",
        action="store_true",
        help="also run hive swarm follow-up after agent",
    )
    pro.add_argument(
        "--no-ollama",
        action="store_true",
        help="heuristic deliverable only (no Ollama)",
    )

    inbox = sub.add_parser("inbox", help="process file-drop inbox")
    inbox.add_argument("--max", type=int, default=10)

    args = p.parse_args(argv)

    from drone.app.service import DroneHiveService
    from drone.app.links import LinkRegistry
    from drone.app.config import app_root

    r = app_root()
    svc = DroneHiveService(r)

    if args.cmd == "health":
        print(json.dumps(svc.health(), indent=2))
        return 0 if svc.health().get("ok") else 1
    if args.cmd == "status":
        print(json.dumps(svc.status(), indent=2))
        return 0
    if args.cmd == "links":
        print(json.dumps(LinkRegistry(r).list_links(), indent=2))
        return 0
    if args.cmd == "links-probe":
        out = LinkRegistry(r).probe_all()
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "commission":
        from drone.app.commission import commission

        seal = commission(r, serve_probe=True)
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1
    if args.cmd == "desktop":
        from drone.app.desktop import main as desktop_main

        return desktop_main()
    if args.cmd == "desktop-pro":
        from drone.pro.desktop import main as pro_desktop_main

        return pro_desktop_main()
    if args.cmd == "serve":
        from drone.app.api import serve

        serve(host=args.host, port=args.port, root=r)
        return 0
    if args.cmd == "task":
        report = svc.run_task(
            args.goal,
            lane=args.lane,
            lm_assist=args.lm_assist,
            controller=args.controller,
        )
        print(json.dumps(report, indent=2))
        return 0 if report.get("status") == "GREEN" else 1
    if args.cmd == "brain":
        seal = svc.brain_command(args.command)
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1
    if args.cmd == "pro":
        from drone.pro.service import DroneHiveProService

        pro_svc = DroneHiveProService(r)
        seal = pro_svc.pro_run(
            args.goal,
            max_rounds=max(1, int(args.rounds)),
            use_ollama=not bool(args.no_ollama),
            also_hive=bool(args.hive),
        )
        print(json.dumps(seal, indent=2))
        return 0 if seal.get("status") == "GREEN" else 1
    if args.cmd == "inbox":
        print(json.dumps(svc.process_inbox(max_n=args.max), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
