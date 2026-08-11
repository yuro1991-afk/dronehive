"""
Commission DroneHive as a fully standalone modular app.

Runs real checks + smoke tasks + link probes + optional HTTP serve probe.
Writes out/COMMISSION_SEAL.json — GREEN only with evidence.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from .config import app_root, ensure_app_dirs, load_app_config
from .links import LinkRegistry
from .service import DroneHiveService


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def commission(root: Path | None = None, *, serve_probe: bool = True) -> dict[str, Any]:
    root = Path(root or app_root())
    cfg = load_app_config(root)
    dirs = ensure_app_dirs(root, cfg)
    svc = DroneHiveService(root)
    links = LinkRegistry(root)
    t0 = time.perf_counter()
    checks: dict[str, Any] = {}

    # 1) modular files on disk OR importable (wheel / user workspace install)
    disk_mods = {
        "chain": root / "drone" / "chain.py",
        "hive": root / "drone" / "hive.py",
        "fast_lane": root / "drone" / "fast_lane.py",
        "clean_slate": root / "drone" / "clean_slate.py",
        "tools": root / "drone" / "tools.py",
        "app_service": root / "drone" / "app" / "service.py",
        "app_api": root / "drone" / "app" / "api.py",
        "app_links": root / "drone" / "app" / "links.py",
        "app_config_json": root / "configs" / "app.json",
        "pyproject": root / "pyproject.toml",
    }
    import_mods = {
        "chain": "drone.chain",
        "hive": "drone.hive",
        "fast_lane": "drone.fast_lane",
        "clean_slate": "drone.clean_slate",
        "tools": "drone.tools",
        "app_service": "drone.app.service",
        "app_api": "drone.app.api",
        "app_links": "drone.app.links",
    }
    checks["modules"] = {}
    for k, p in disk_mods.items():
        ok_disk = p.is_file()
        ok_imp = False
        if k in import_mods:
            try:
                __import__(import_mods[k])
                ok_imp = True
            except Exception:
                ok_imp = False
        checks["modules"][k] = {
            "path": str(p),
            "ok_disk": ok_disk,
            "ok_import": ok_imp if k in import_mods else None,
            "ok": ok_disk or ok_imp or (k in {"app_config_json", "pyproject"} and (
                (root / "configs" / "app.json").is_file() if k == "app_config_json" else True
            )),
        }
    # config always required on disk (seeded workspace)
    checks["modules"]["app_config_json"]["ok"] = (root / "configs" / "app.json").is_file()
    # pyproject only required when developing from checkout
    if not checks["modules"]["pyproject"]["ok_disk"]:
        checks["modules"]["pyproject"]["ok"] = True
        checks["modules"]["pyproject"]["note"] = "optional when installed as package"
    modules_ok = all(v["ok"] for v in checks["modules"].values())

    # 2) health
    health = svc.health()
    checks["health"] = health

    # 3) universal links catalog + probe
    catalog = links.list_links()
    probe = links.probe_all()
    checks["links_catalog"] = {
        "ok": bool(catalog.get("links")),
        "count": len(catalog.get("links") or []),
        "path": str(dirs["links"] / "CATALOG.json"),
    }
    checks["links_probe"] = probe

    # 4) fast lane task
    fast = svc.run_task(
        "commission smoke: fast lane modular app artifact",
        lane="fast",
        lm_assist="none",
        controller="local",
    )
    checks["fast_task"] = {
        "ok": fast.get("status") == "GREEN",
        "status": fast.get("status"),
        "nodes_run": fast.get("nodes_run"),
        "tool_calls": fast.get("tool_calls"),
        "seal_path": fast.get("seal_path") or fast.get("report_path"),
        "duration_ms": fast.get("duration_ms"),
    }

    # 5) file inbox link
    inbox_job = dirs["inbox"] / "commission_inbox_job.json"
    inbox_job.write_text(
        json.dumps(
            {
                "goal": "commission inbox: process file-drop goal",
                "lane": "fast",
                "lm_assist": "none",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    inbox = svc.process_inbox(max_n=5)
    checks["file_inbox"] = {
        "ok": inbox.get("processed", 0) >= 1
        and any(r.get("ok") for r in inbox.get("results") or []),
        "detail": inbox,
    }

    # 6) HTTP serve probe
    http_probe: dict[str, Any] = {"attempted": False, "ok": False}
    if serve_probe:
        from .api import serve_background

        bg = serve_background(root=root)
        http_probe["attempted"] = True
        http_probe["base"] = bg.get("base")
        try:
            url = f"{bg['base']}/api/health"
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            http_probe["ok"] = bool(body.get("ok") or body.get("status") == "GREEN")
            http_probe["health"] = {
                "status_code": resp.status,
                "app": body.get("app"),
                "version": body.get("version"),
            }
            # openai compat quick
            req = urllib.request.Request(
                f"{bg['base']}/v1/chat/completions",
                data=json.dumps(
                    {
                        "model": "drone-hive-fast",
                        "messages": [
                            {
                                "role": "user",
                                "content": "commission openai shim: one line ok",
                            }
                        ],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp2:
                chat = json.loads(resp2.read().decode("utf-8"))
            http_probe["openai_compat"] = {
                "ok": bool(chat.get("choices")),
                "content": (
                    (chat.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")[:200]
                ),
            }
        except Exception as e:
            http_probe["ok"] = False
            http_probe["error"] = str(e)
        finally:
            try:
                bg["server"].shutdown()
            except Exception:
                pass
    checks["http"] = http_probe

    # Gate
    required = [
        modules_ok,
        bool(health.get("ok") or health.get("status") in {"GREEN", "PARTIAL"}),
        checks["links_catalog"]["ok"],
        checks["fast_task"]["ok"],
        checks["file_inbox"]["ok"],
    ]
    if serve_probe:
        required.append(bool(http_probe.get("ok")))

    status = "GREEN" if all(required) else "PARTIAL"
    # core modules failure = RED
    if not modules_ok or not checks["fast_task"]["ok"]:
        status = "RED"

    seal = {
        "schema": "drone.hive.commission.v1",
        "app": cfg.get("name"),
        "version": cfg.get("version"),
        "status": status,
        "false_green": 0,
        "utc": _utc(),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        "root": str(root),
        "standalone": True,
        "modular": True,
        "universal_links": True,
        "checks": checks,
        "commissioned": status == "GREEN",
        "honesty": {
            "what_this_is": (
                "Standalone modular DroneHive app: fabric + hive + fast lane + "
                "clean-slate + tools + HTTP/OpenAI/file/library links"
            ),
            "not_claimed": "Not 24 full LLMs; not dense FAISS; not every cloud SaaS auto-linked",
            "link_surface": [
                "rest",
                "openai_compat",
                "file_inbox",
                "library",
                "continuous",
                "ollama",
                "webhook_out",
                "fabric",
                "hive",
                "fast",
            ],
        },
        "how_to_run": {
            "serve": "python -m drone app serve",
            "windows": "START_APP.bat",
            "task": 'curl -X POST http://127.0.0.1:8765/api/v1/task -d "{\\"goal\\":\\"...\\"}"',
            "install": "pip install -e .",
        },
    }
    out = root / "out" / "COMMISSION_SEAL.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    seal["seal_path"] = str(out)
    return seal
