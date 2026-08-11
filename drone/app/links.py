"""
Universal link registry — pluggable adapters so DroneHive can attach anywhere.

Link kinds (honest scope):
  rest          — this app's HTTP API
  openai_compat — OpenAI-style /v1/chat/completions
  file_inbox    — drop JSON into data/app/inbox
  library       — F:\\GrokSelfLibrary
  continuous    — D:\\GrokCoreMemory\\continuous\\OPEN_TASKS.json
  ollama        — local Ollama brain
  webhook_out   — optional POST of seals to a URL (env DRONE_HIVE_WEBHOOK)

Not claimed: automatic discovery of every SaaS; only registered adapters.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import app_root, ensure_app_dirs, load_app_config
from .service import DroneHiveService


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LinkRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or app_root())
        self.cfg = load_app_config(self.root)
        self.dirs = ensure_app_dirs(self.root, self.cfg)
        self.service = DroneHiveService(self.root)
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "rest": self._link_rest,
            "openai_compat": self._link_openai,
            "file_inbox": self._link_file_inbox,
            "library": self._link_library,
            "continuous": self._link_continuous,
            "ollama": self._link_ollama,
            "webhook_out": self._link_webhook,
            "fabric": self._link_fabric,
            "hive": self._link_hive,
            "fast": self._link_fast,
        }

    def list_links(self) -> dict[str, Any]:
        enabled = self.cfg.get("universal_links") or {}
        host = self.cfg.get("host", "127.0.0.1")
        port = int(self.cfg.get("port", 8765))
        base = f"http://{host}:{port}"
        catalog = [
            {
                "id": "rest",
                "kind": "http",
                "uri": base,
                "enabled": bool(enabled.get("rest", True)),
                "how": "GET /api/health · POST /api/v1/task {goal,lane}",
            },
            {
                "id": "openai_compat",
                "kind": "http",
                "uri": f"{base}/v1/chat/completions",
                "enabled": bool(enabled.get("openai_compat", True)),
                "how": "OpenAI-style messages → fast/full lane task",
            },
            {
                "id": "file_inbox",
                "kind": "filesystem",
                "uri": str(self.dirs["inbox"]),
                "enabled": bool(enabled.get("file_inbox", True)),
                "how": 'Drop {"goal":"...","lane":"fast"} .json files',
            },
            {
                "id": "library",
                "kind": "host_library",
                "uri": r"F:\GrokSelfLibrary",
                "enabled": bool(enabled.get("library", True)),
                "how": "LibraryBridge HOT + recall + outbox notes",
            },
            {
                "id": "continuous",
                "kind": "task_board",
                "uri": r"D:\GrokCoreMemory\continuous\OPEN_TASKS.json",
                "enabled": bool(enabled.get("continuous", True)),
                "how": "Hive pull_library / pack_for_buzzer open tasks",
            },
            {
                "id": "ollama",
                "kind": "llm",
                "uri": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
                "enabled": bool(enabled.get("ollama", True)),
                "how": "Shared top model via drone.ollama_brain",
            },
            {
                "id": "webhook_out",
                "kind": "http_callback",
                "uri": os.environ.get("DRONE_HIVE_WEBHOOK") or None,
                "enabled": bool(enabled.get("webhook_out", True)),
                "how": "Set DRONE_HIVE_WEBHOOK to POST seals outward",
            },
            {
                "id": "fabric",
                "kind": "module",
                "uri": "drone.chain.BrainFabric",
                "enabled": True,
                "how": "Internal modular fabric (full 24-node)",
            },
            {
                "id": "hive",
                "kind": "module",
                "uri": "drone.hive.BuzzerHive",
                "enabled": True,
                "how": "Buzzer swarm clean-slate units",
            },
            {
                "id": "fast",
                "kind": "module",
                "uri": "drone.fast_lane.FastLane",
                "enabled": True,
                "how": "5-node lite path",
            },
            {
                "id": "grok_handoff",
                "kind": "cowork",
                "uri": "drone.grok_handoff.GrokDroneHandoff",
                "enabled": True,
                "how": (
                    "Grok e2e cowork: python -m drone handoff lanes|to|collect|e2e · "
                    "POST /api/v1/handoff · data/app/cowork/"
                ),
            },
            {
                "id": "super_llms",
                "kind": "multi_model",
                "uri": "drone.super_llms.SuperLLMs",
                "enabled": True,
                "how": (
                    "All safe full LLMs: python -m drone super-llms · START_SUPER_LLMS.bat · "
                    "GET /api/v1/super-llms · route/chat by role"
                ),
            },
            {
                "id": "future_seer",
                "kind": "speculative",
                "uri": "drone.future_seer.FutureSeer",
                "enabled": True,
                "how": (
                    "Typeahead multi-model seer: python -m drone seer · START_SEER.bat · "
                    "POST /api/v1/seer/type · hot lanes · Jane helpers · Everest"
                ),
            },
        ]
        # persist catalog
        cat_path = self.dirs["links"] / "CATALOG.json"
        blob = {
            "schema": "drone.hive.links.v1",
            "utc": _utc(),
            "app": self.cfg.get("name"),
            "links": catalog,
            "false_green": 0,
        }
        cat_path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        return blob

    def probe(self, link_id: str) -> dict[str, Any]:
        fn = self._handlers.get(link_id)
        if not fn:
            return {"ok": False, "link": link_id, "error": "unknown link", "false_green": 0}
        try:
            return fn()
        except Exception as e:
            return {"ok": False, "link": link_id, "error": str(e), "false_green": 0}

    def probe_all(self) -> dict[str, Any]:
        results = {}
        for lid in self._handlers:
            results[lid] = self.probe(lid)
        ok_n = sum(1 for r in results.values() if r.get("ok"))
        return {
            "ok": ok_n >= 5,  # core modules must work; external optional
            "probed": len(results),
            "ok_count": ok_n,
            "results": results,
            "false_green": 0,
            "utc": _utc(),
        }

    def _link_rest(self) -> dict[str, Any]:
        host = self.cfg.get("host", "127.0.0.1")
        port = int(self.cfg.get("port", 8765))
        return {
            "ok": True,
            "link": "rest",
            "uri": f"http://{host}:{port}",
            "note": "available when app serve is running",
        }

    def _link_openai(self) -> dict[str, Any]:
        return {
            "ok": True,
            "link": "openai_compat",
            "path": "/v1/chat/completions",
            "note": "maps user message to run_task lane=fast by default",
        }

    def _link_file_inbox(self) -> dict[str, Any]:
        p = self.dirs["inbox"]
        return {"ok": p.is_dir(), "link": "file_inbox", "path": str(p)}

    def _link_library(self) -> dict[str, Any]:
        from drone.library_bridge import LibraryBridge

        st = LibraryBridge().status()
        return {
            "ok": bool(st.get("library_exists")),
            "link": "library",
            "status": st,
        }

    def _link_continuous(self) -> dict[str, Any]:
        from drone.library_bridge import LibraryBridge

        st = LibraryBridge().status()
        return {
            "ok": bool(st.get("continuous_tasks_exists")),
            "link": "continuous",
            "path": st.get("continuous_tasks"),
        }

    def _link_ollama(self) -> dict[str, Any]:
        from drone.ollama_brain import brain_status

        st = brain_status()
        return {
            "ok": bool(st.get("reachable")),
            "link": "ollama",
            "top_model": st.get("top_model"),
            "reachable": st.get("reachable"),
        }

    def _link_webhook(self) -> dict[str, Any]:
        url = (os.environ.get("DRONE_HIVE_WEBHOOK") or "").strip()
        if not url:
            return {
                "ok": True,
                "link": "webhook_out",
                "configured": False,
                "note": "optional — set DRONE_HIVE_WEBHOOK",
            }
        return {"ok": True, "link": "webhook_out", "configured": True, "uri": url}

    def _link_fabric(self) -> dict[str, Any]:
        return {
            "ok": (self.root / "drone" / "chain.py").is_file(),
            "link": "fabric",
            "module": "drone.chain.BrainFabric",
        }

    def _link_hive(self) -> dict[str, Any]:
        return {
            "ok": (self.root / "drone" / "hive.py").is_file(),
            "link": "hive",
            "module": "drone.hive.BuzzerHive",
        }

    def _link_fast(self) -> dict[str, Any]:
        return {
            "ok": (self.root / "drone" / "fast_lane.py").is_file(),
            "link": "fast",
            "module": "drone.fast_lane.FastLane",
        }

    def post_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = (os.environ.get("DRONE_HIVE_WEBHOOK") or "").strip()
        if not url:
            return {"ok": False, "attempted": False, "error": "DRONE_HIVE_WEBHOOK not set"}
        body = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return {
                    "ok": 200 <= resp.status < 300,
                    "attempted": True,
                    "status": resp.status,
                }
        except Exception as e:
            return {"ok": False, "attempted": True, "error": str(e)}
