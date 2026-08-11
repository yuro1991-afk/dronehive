"""
DroneHive HTTP API — stdlib only (no FastAPI required).

Universal surface:
  GET  /                 → dashboard HTML
  GET  /api/health
  GET  /api/status
  GET  /api/v1/links
  POST /api/v1/task      {"goal","lane","lm_assist","controller"}
  POST /api/v1/fast      {"goal"}
  POST /api/v1/hive      {"goals":[],"cycles","workers","lane"}
  POST /api/v1/handoff   {"goal","mode","lane","workers","notes"}  # Grok cowork
  GET  /api/v1/lanes     live lane status for e2e cowork
  GET  /api/v1/cowork/collect
  GET  /api/v1/super-llms        Super LLMs roster status
  POST /api/v1/super-llms/route  {"goal","role"}
  POST /api/v1/super-llms/chat   {"prompt","model","role"}
  GET  /api/v1/seer              Future Seer status
  POST /api/v1/seer/hot          keep lanes hot + jane/ever
  POST /api/v1/seer/type         {"text"} typeahead speculate
  POST /api/v1/seer/commit       {"text","mode","auto"}
  POST /api/v1/inbox/process
  POST /v1/chat/completions  OpenAI-compatible shim
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import app_root, env_overrides, load_app_config
from .links import LinkRegistry
from .service import DroneHiveService


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _AppState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.service = DroneHiveService(root)
        self.links = LinkRegistry(root)
        self.cfg = env_overrides(load_app_config(root))
        self.started = _utc()


def make_handler(state: _AppState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # whisper-friendly: still log to file optionally
            pass

        def _send(self, code: int, body: Any, content_type: str = "application/json") -> None:
            if isinstance(body, (dict, list)):
                raw = json.dumps(body, indent=2).encode("utf-8")
            elif isinstance(body, str):
                raw = body.encode("utf-8")
            else:
                raw = bytes(body)
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                html = _dashboard_html(state)
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/health":
                self._send(200, state.service.health())
                return
            if path == "/api/status":
                self._send(200, state.service.status())
                return
            if path in {"/api/v1/links", "/api/links"}:
                self._send(200, state.links.list_links())
                return
            if path == "/api/v1/links/probe":
                self._send(200, state.links.probe_all())
                return
            if path in {"/api/v1/lanes", "/api/lanes"}:
                self._send(200, state.service.lanes_live())
                return
            if path in {"/api/v1/cowork/collect", "/api/cowork/collect"}:
                self._send(200, state.service.collect_cowork(max_n=20))
                return
            if path in {"/api/v1/super-llms", "/api/super-llms"}:
                self._send(200, state.service.super_llms_status())
                return
            if path in {"/api/v1/seer", "/api/seer"}:
                self._send(200, state.service.seer_status())
                return
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                f = state.root / "static" / rel
                if f.is_file():
                    ctype = "text/plain"
                    if rel.endswith(".css"):
                        ctype = "text/css"
                    elif rel.endswith(".js"):
                        ctype = "application/javascript"
                    elif rel.endswith(".html"):
                        ctype = "text/html"
                    self._send(200, f.read_text(encoding="utf-8"), ctype)
                    return
            self._send(404, {"error": "not found", "path": path})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            data = self._read_json()

            if path in {"/api/v1/task", "/api/task"}:
                goal = (data.get("goal") or data.get("prompt") or "").strip()
                if not goal:
                    self._send(400, {"error": "goal required", "false_green": 0})
                    return
                report = state.service.run_task(
                    goal,
                    lane=data.get("lane"),
                    controller=data.get("controller"),
                    lm_assist=data.get("lm_assist"),
                    domain=data.get("domain") or "build",
                )
                self._send(200 if report.get("status") == "GREEN" else 500, report)
                return

            if path in {"/api/v1/fast", "/api/fast"}:
                goal = (data.get("goal") or "").strip()
                if not goal:
                    self._send(400, {"error": "goal required"})
                    return
                report = state.service.run_task(
                    goal, lane="fast", lm_assist=data.get("lm_assist") or "none"
                )
                self._send(200 if report.get("status") == "GREEN" else 500, report)
                return

            if path in {"/api/v1/hive", "/api/hive"}:
                goals = data.get("goals")
                seal = state.service.run_hive(
                    goals=goals if isinstance(goals, list) else None,
                    cycles=int(data.get("cycles") or 2),
                    workers=int(data.get("workers") or 2),
                    lane=str(data.get("lane") or "fast"),
                    lm_assist=str(data.get("lm_assist") or "none"),
                    controller=str(data.get("controller") or "local"),
                )
                self._send(200 if seal.get("status") == "GREEN" else 500, seal)
                return

            if path in {"/api/v1/inbox/process", "/api/inbox/process"}:
                self._send(200, state.service.process_inbox(max_n=int(data.get("max") or 10)))
                return

            if path in {"/api/v1/handoff", "/api/handoff"}:
                goal = (data.get("goal") or data.get("prompt") or data.get("command") or "").strip()
                if not goal:
                    self._send(400, {"error": "goal required", "false_green": 0})
                    return
                report = state.service.handoff(
                    goal,
                    mode=str(data.get("mode") or "fast"),
                    lane=data.get("lane"),
                    workers=int(data.get("workers") or 2),
                    cycles=int(data.get("cycles") or 2),
                    lm_assist=str(data.get("lm_assist") or "none"),
                    controller=str(data.get("controller") or "grok"),
                    notes=str(data.get("notes") or ""),
                    context_paths=data.get("context_paths")
                    if isinstance(data.get("context_paths"), list)
                    else None,
                    continuous_task_id=data.get("continuous_task_id"),
                    goals=data.get("goals") if isinstance(data.get("goals"), list) else None,
                    wait=bool(data.get("wait", True)),
                )
                code = 200 if report.get("status") in {"GREEN", "OPEN", "PARTIAL"} else 500
                self._send(code, report)
                return

            if path in {"/api/v1/super-llms/route", "/api/super-llms/route"}:
                goal = str(data.get("goal") or data.get("prompt") or "").strip()
                self._send(200, state.service.super_llms_route(goal, role=data.get("role")))
                return

            if path in {"/api/v1/super-llms/chat", "/api/super-llms/chat"}:
                prompt = str(data.get("prompt") or data.get("goal") or "").strip()
                if not prompt:
                    self._send(400, {"error": "prompt required", "false_green": 0})
                    return
                report = state.service.super_llms_chat(
                    prompt, model=data.get("model"), role=data.get("role")
                )
                code = 200 if report.get("status") == "GREEN" else 500
                self._send(code, report)
                return

            if path in {"/api/v1/seer/hot", "/api/seer/hot"}:
                self._send(200, state.service.seer_hot())
                return

            if path in {"/api/v1/seer/type", "/api/seer/type"}:
                text = str(data.get("text") or data.get("partial") or data.get("prompt") or "").strip()
                if not text:
                    self._send(400, {"error": "text required", "false_green": 0})
                    return
                report = state.service.seer_type(text)
                code = 200 if report.get("status") in {"GREEN", "PARTIAL", "WAIT"} else 500
                self._send(code, report)
                return

            if path in {"/api/v1/seer/commit", "/api/seer/commit"}:
                text = str(data.get("text") or data.get("prompt") or data.get("goal") or "").strip()
                if not text:
                    self._send(400, {"error": "text required", "false_green": 0})
                    return
                report = state.service.seer_commit(
                    text,
                    mode=str(data.get("mode") or "preview"),
                    allow_auto=bool(data.get("auto") or data.get("allow_auto")),
                )
                code = 200 if report.get("status") in {"GREEN", "PARTIAL"} else 500
                self._send(code, report)
                return

            if path == "/v1/chat/completions":
                # OpenAI-compatible: extract last user message as goal
                messages = data.get("messages") or []
                goal = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        goal = str(m.get("content") or "").strip()
                        break
                if not goal:
                    goal = str(data.get("prompt") or "").strip()
                if not goal:
                    self._send(400, {"error": "no user message"})
                    return
                lane = "fast"
                model = str(data.get("model") or "drone-hive-fast")
                if "full" in model.lower():
                    lane = "full"
                report = state.service.run_task(goal, lane=lane, lm_assist="none")
                text = (
                    f"[{report.get('status')}] lane={lane} "
                    f"nodes={report.get('nodes_run')} "
                    f"tools={report.get('tool_calls')} "
                    f"path={report.get('seal_path') or report.get('report_path')}"
                )
                self._send(
                    200,
                    {
                        "id": f"chatcmpl-{report.get('task_id') or 'x'}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "drone_hive": {
                            "status": report.get("status"),
                            "lane": lane,
                            "false_green": 0,
                            "report_path": report.get("report_path")
                            or report.get("seal_path"),
                        },
                    },
                )
                return

            self._send(404, {"error": "not found", "path": path})

    return Handler


def _dashboard_html(state: _AppState) -> str:
    cfg = state.cfg
    host = cfg.get("host")
    port = cfg.get("port")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>DroneHive — Modular Swarm App</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; background:#0b1220; color:#e8eefc; margin:0; padding:2rem; }}
    h1 {{ margin:0 0 .25rem; font-size:1.6rem; }}
    .sub {{ color:#8aa0c8; margin-bottom:1.5rem; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:1rem; }}
    .card {{ background:#121a2b; border:1px solid #243049; border-radius:12px; padding:1rem 1.1rem; }}
    .card h2 {{ font-size:.95rem; margin:0 0 .6rem; color:#9ec1ff; }}
    code, pre {{ background:#0a101c; padding:.2rem .4rem; border-radius:6px; font-size:.85rem; }}
    pre {{ padding:.8rem; overflow:auto; }}
    a {{ color:#7dd3fc; }}
    button {{ background:#2563eb; color:white; border:0; padding:.55rem 1rem; border-radius:8px; cursor:pointer; margin-right:.5rem; margin-top:.5rem; }}
    button.secondary {{ background:#334155; }}
    input, textarea {{ width:100%; background:#0a101c; border:1px solid #243049; color:#e8eefc; border-radius:8px; padding:.55rem; box-sizing:border-box; }}
    #out {{ margin-top:1rem; white-space:pre-wrap; max-height:320px; overflow:auto; }}
    .pill {{ display:inline-block; background:#14532d; color:#bbf7d0; padding:.15rem .5rem; border-radius:999px; font-size:.75rem; }}
  </style>
</head>
<body>
  <h1>DroneHive <span class="pill">v{cfg.get("version")}</span></h1>
  <p class="sub">Standalone modular swarm · universal links · fast + full lanes · host {host}:{port}</p>
  <div class="grid">
    <div class="card">
      <h2>Health</h2>
      <button onclick="load('/api/health')">GET /api/health</button>
      <button class="secondary" onclick="load('/api/status')">Status</button>
      <button class="secondary" onclick="load('/api/v1/links')">Links</button>
    </div>
    <div class="card">
      <h2>Run task (fast lane)</h2>
      <textarea id="goal" rows="3" placeholder="goal text…"></textarea>
      <button onclick="runTask()">POST /api/v1/task</button>
    </div>
    <div class="card">
      <h2>Universal links</h2>
      <p>REST · OpenAI-compat · file inbox · library · continuous · Ollama · modules</p>
      <code>POST {host}:{port}/v1/chat/completions</code>
    </div>
  </div>
  <pre id="out">Ready.</pre>
  <script>
    async function load(path) {{
      const r = await fetch(path); const j = await r.json();
      document.getElementById('out').textContent = JSON.stringify(j, null, 2);
    }}
    async function runTask() {{
      const goal = document.getElementById('goal').value || 'fast app smoke: write hello artifact';
      const r = await fetch('/api/v1/task', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ goal, lane:'fast', lm_assist:'none' }})
      }});
      const j = await r.json();
      document.getElementById('out').textContent = JSON.stringify(j, null, 2);
    }}
  </script>
</body>
</html>
"""


def serve(
    host: str | None = None,
    port: int | None = None,
    root: Path | None = None,
) -> None:
    root = Path(root or app_root())
    cfg = env_overrides(load_app_config(root))
    host = host or str(cfg.get("host") or "127.0.0.1")
    port = int(port or cfg.get("port") or 8765)
    state = _AppState(root)
    handler = make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(
        json.dumps(
            {
                "app": cfg.get("name"),
                "version": cfg.get("version"),
                "serve": f"http://{host}:{port}",
                "health": f"http://{host}:{port}/api/health",
                "false_green": 0,
                "utc": _utc(),
            },
            indent=2,
        ),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def serve_background(
    host: str | None = None,
    port: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Start server thread (for commission smoke)."""
    root = Path(root or app_root())
    cfg = env_overrides(load_app_config(root))
    host = host or str(cfg.get("host") or "127.0.0.1")
    port = int(port or cfg.get("port") or 8765)
    state = _AppState(root)
    handler = make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    return {
        "ok": True,
        "host": host,
        "port": port,
        "base": f"http://{host}:{port}",
        "server": httpd,
        "thread": t,
    }
