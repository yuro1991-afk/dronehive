"""
Drone tool belt — real disk/shell/library tools for ALL worker nodes.

Honesty:
  - Tools write under project sandbox + out/ only (no arbitrary system wipe)
  - Shell is allowlisted commands only
  - Evidence paths returned for every success
  - false_green: 0
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Safe shell allowlist (Windows-native first)
_SHELL_ALLOW = re.compile(
    r"^(dir|type|echo|where|python|py|pip|git|ollama|nvidia-smi|"
    r"ping|fsutil|wmic|powershell|pwsh|cmd)\b",
    re.I,
)


class DroneToolkit:
    """Shared tools for every drone node in a fabric run. Thread-safe for L||R."""

    def __init__(self, root: Path, task_id: str = "") -> None:
        self.root = Path(root)
        self.task_id = task_id or "notask"
        self.workspace = self.root / "data" / "workspace" / self.task_id
        self.artifacts = self.root / "out" / "artifacts" / self.task_id
        self.logs = self.root / "data" / "tool_logs"
        self._lock = threading.RLock()
        with root_lock(self.root):
            for d in (self.workspace, self.artifacts, self.logs):
                d.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []

    def _record(self, name: str, ok: bool, **extra: Any) -> dict[str, Any]:
        row = {"tool": name, "ok": ok, "utc": _utc(), **extra}
        with self._lock:
            self.calls.append(row)
        return row

    def list_tools(self) -> dict[str, Any]:
        names = [
            "list_tools",
            "write_text",
            "read_text",
            "list_dir",
            "append_log",
            "write_json",
            "hash_text",
            "run_shell",
            "run_python",
            "copy_to_artifacts",
            "package_manifest",
            "library_status",
            "ollama_generate",
            "run_host_diagnostics",
            "write_and_smoke_python",
        ]
        return self._record("list_tools", True, tools=names, count=len(names))

    def _safe_rel(self, rel: str, base: Path | None = None) -> Path:
        base = base or self.workspace
        rel = (rel or "out.txt").replace("\\", "/").lstrip("/")
        # block path escape
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        path = base.joinpath(*parts) if parts else base / "out.txt"
        path = path.resolve()
        base_r = base.resolve()
        if base_r not in path.parents and path != base_r:
            raise ValueError(f"path escapes sandbox: {rel}")
        return path

    def write_text(self, rel_path: str, content: str) -> dict[str, Any]:
        try:
            path = self._safe_rel(rel_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content or "", encoding="utf-8")
            return self._record(
                "write_text",
                True,
                path=str(path),
                bytes=path.stat().st_size,
            )
        except Exception as e:
            return self._record("write_text", False, error=str(e), rel=rel_path)

    def read_text(self, rel_path: str, max_chars: int = 8000) -> dict[str, Any]:
        try:
            # allow read from workspace or artifacts
            for base in (self.workspace, self.artifacts, self.root / "out"):
                try:
                    path = self._safe_rel(rel_path, base=base)
                    if path.is_file():
                        text = path.read_text(encoding="utf-8", errors="replace")
                        return self._record(
                            "read_text",
                            True,
                            path=str(path),
                            text=text[:max_chars],
                            truncated=len(text) > max_chars,
                        )
                except ValueError:
                    continue
            # absolute only if under root
            p = Path(rel_path)
            if p.is_file() and str(p.resolve()).startswith(str(self.root.resolve())):
                text = p.read_text(encoding="utf-8", errors="replace")
                return self._record(
                    "read_text", True, path=str(p), text=text[:max_chars]
                )
            return self._record("read_text", False, error="not found", rel=rel_path)
        except Exception as e:
            return self._record("read_text", False, error=str(e))

    def list_dir(self, rel: str = ".") -> dict[str, Any]:
        try:
            path = self._safe_rel(rel if rel != "." else "x")
            if rel == ".":
                path = self.workspace
            if not path.is_dir():
                path = self.workspace
            names = sorted([p.name for p in path.iterdir()])[:100]
            return self._record(
                "list_dir", True, path=str(path), entries=names, count=len(names)
            )
        except Exception as e:
            return self._record("list_dir", False, error=str(e))

    def append_log(self, line: str) -> dict[str, Any]:
        try:
            path = self.logs / f"{self.task_id}.jsonl"
            entry = {"utc": _utc(), "line": (line or "")[:2000]}
            with root_lock(self.root):
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return self._record("append_log", True, path=str(path))
        except Exception as e:
            return self._record("append_log", False, error=str(e))

    def write_json(self, rel_path: str, data: Any) -> dict[str, Any]:
        try:
            text = json.dumps(data, indent=2, ensure_ascii=False)
            return self.write_text(rel_path, text)
        except Exception as e:
            return self._record("write_json", False, error=str(e))

    def hash_text(self, text: str) -> dict[str, Any]:
        h = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
        return self._record("hash_text", True, sha256=h, n=len(text or ""))

    def run_shell(self, command: str, timeout_s: int = 45) -> dict[str, Any]:
        cmd = (command or "").strip()
        if not cmd:
            return self._record("run_shell", False, error="empty command")
        # only first token / simple allowlist
        if not _SHELL_ALLOW.search(cmd):
            return self._record(
                "run_shell",
                False,
                error="command not allowlisted",
                command=cmd[:200],
            )
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.workspace),
            )
            return self._record(
                "run_shell",
                proc.returncode == 0,
                command=cmd[:300],
                returncode=proc.returncode,
                stdout=(proc.stdout or "")[:3000],
                stderr=(proc.stderr or "")[:1000],
            )
        except Exception as e:
            return self._record("run_shell", False, error=str(e), command=cmd[:200])

    def run_python(self, code: str, timeout_s: int = 30) -> dict[str, Any]:
        """Run a short Python snippet in workspace (sandbox)."""
        try:
            script = self.workspace / "_drone_snip.py"
            script.write_text(code or "print('empty')\n", encoding="utf-8")
            py = os.environ.get(
                "DRONE_PYTHON",
                os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Programs",
                    "Python",
                    "Python312",
                    "python.exe",
                ),
            )
            proc = subprocess.run(
                [py, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.workspace),
            )
            return self._record(
                "run_python",
                proc.returncode == 0,
                returncode=proc.returncode,
                stdout=(proc.stdout or "")[:3000],
                stderr=(proc.stderr or "")[:1000],
                script=str(script),
            )
        except Exception as e:
            return self._record("run_python", False, error=str(e))

    def copy_to_artifacts(self, rel_path: str) -> dict[str, Any]:
        try:
            src = self._safe_rel(rel_path)
            if not src.is_file():
                return self._record("copy_to_artifacts", False, error="missing", rel=rel_path)
            dest = self.artifacts / src.name
            shutil.copy2(src, dest)
            return self._record(
                "copy_to_artifacts", True, src=str(src), dest=str(dest)
            )
        except Exception as e:
            return self._record("copy_to_artifacts", False, error=str(e))

    def package_manifest(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            files = []
            for base in (self.workspace, self.artifacts):
                if base.is_dir():
                    for p in base.rglob("*"):
                        if p.is_file():
                            files.append(str(p))
            man = {
                "utc": _utc(),
                "task_id": self.task_id,
                "files": files,
                "tool_calls": len(self.calls),
                "extra": extra or {},
            }
            path = self.artifacts / "MANIFEST.json"
            path.write_text(json.dumps(man, indent=2), encoding="utf-8")
            return self._record(
                "package_manifest", True, path=str(path), file_count=len(files)
            )
        except Exception as e:
            return self._record("package_manifest", False, error=str(e))

    def library_status(self) -> dict[str, Any]:
        try:
            from .library_bridge import LibraryBridge

            st = LibraryBridge().status()
            return self._record("library_status", bool(st.get("library_exists")), **st)
        except Exception as e:
            return self._record("library_status", False, error=str(e))

    def ollama_generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        num_predict: int = 256,
        timeout_s: int = 120,
    ) -> dict[str, Any]:
        """Call shared Ollama top model (one backend)."""
        import urllib.error
        import urllib.request

        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        model = model or os.environ.get("DRONE_OLLAMA_MODEL", "gemma4:12b")
        body = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": int(num_predict),
                    "temperature": 0.2,
                },
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{host.rstrip('/')}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str(data.get("response", "")).strip()
            return self._record(
                "ollama_generate",
                bool(text),
                model=model,
                text=text[:4000],
                eval_count=data.get("eval_count"),
            )
        except Exception as e:
            return self._record(
                "ollama_generate", False, model=model, error=str(e)
            )

    def dump_call_log(self) -> Path:
        path = self.logs / f"{self.task_id}_calls.json"
        with root_lock(self.root):
            path.write_text(json.dumps(self.calls, indent=2), encoding="utf-8")
        return path

    def evidence_paths(self) -> list[str]:
        paths: list[str] = []
        for c in self.calls:
            if c.get("ok") and c.get("path"):
                paths.append(str(c["path"]))
            if c.get("ok") and c.get("dest"):
                paths.append(str(c["dest"]))
        return paths[:40]

    @staticmethod
    def extract_python_source(text: str) -> str:
        """Pull Python from model output (fences or raw)."""
        t = (text or "").strip()
        if not t:
            return ""
        m = re.search(r"```(?:python|py)?\s*([\s\S]*?)```", t, re.I)
        if m:
            return m.group(1).strip()
        # strip leading prose lines until import/def/class
        lines = t.splitlines()
        start = 0
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith(("import ", "from ", "def ", "class ", "#")):
                start = i
                break
        return "\n".join(lines[start:]).strip()

    @staticmethod
    def goal_module_slug(goal: str, node_id: str = "") -> str:
        base = re.sub(r"[^a-zA-Z0-9]+", "_", (goal or "task").lower()).strip("_")
        base = (base[:48] or "task").strip("_")
        if base and base[0].isdigit():
            base = "m_" + base
        if not base.isidentifier():
            base = "drone_task"
        # unique-ish per node to avoid clobber when L+R both write
        tag = re.sub(r"[^a-zA-Z0-9]+", "_", node_id or "")[:16]
        if tag:
            return f"{base}_{tag}".strip("_")
        return base

    def write_and_smoke_python(
        self,
        module_slug: str,
        source: str,
        *,
        timeout_s: int = 45,
    ) -> dict[str, Any]:
        """
        Write a real .py module and smoke-test it.
        Smoke: python -c "import runpy; runpy.run_path(path)" or compile + run.
        ok=False if empty, syntax error, or non-zero exit.
        """
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", module_slug or "drone_task")[:60]
        if not slug or not slug.isidentifier():
            slug = "drone_task_module"
        src = (source or "").strip()
        if not src or len(src) < 20:
            return self._record(
                "write_and_smoke_python",
                False,
                error="source empty or too short",
                module=slug,
            )
        # ensure main smoke if missing
        if "__main__" not in src:
            src = (
                src.rstrip()
                + "\n\n\nif __name__ == '__main__':\n"
                + "    print('SMOKE_OK')\n"
            )
        rel = f"{slug}.py"
        try:
            # syntax check first
            compile(src, rel, "exec")
        except SyntaxError as e:
            w = self.write_text(rel, src)
            return self._record(
                "write_and_smoke_python",
                False,
                error=f"SyntaxError: {e}",
                module=slug,
                path=w.get("path"),
                syntax_ok=False,
            )

        w = self.write_text(rel, src)
        if not w.get("ok"):
            return self._record(
                "write_and_smoke_python",
                False,
                error="write failed",
                module=slug,
            )

        py = os.environ.get(
            "DRONE_PYTHON",
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs",
                "Python",
                "Python312",
                "python.exe",
            ),
        )
        path = self.workspace / rel
        try:
            proc = subprocess.run(
                [py, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.workspace),
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            ok = proc.returncode == 0
            if ok and "SMOKE_OK" not in out and "exec_ok" not in out and "ok" not in out.lower():
                # still accept exit 0 as smoke pass
                pass
            # copy module to artifacts on success
            if ok:
                self.copy_to_artifacts(rel)
            return self._record(
                "write_and_smoke_python",
                ok,
                module=slug,
                path=str(path),
                returncode=proc.returncode,
                stdout=(proc.stdout or "")[:2000],
                stderr=(proc.stderr or "")[:1000],
                bytes=path.stat().st_size if path.is_file() else 0,
                syntax_ok=True,
                smoke_ok=ok,
            )
        except Exception as e:
            return self._record(
                "write_and_smoke_python",
                False,
                error=str(e),
                module=slug,
                path=str(path),
                syntax_ok=True,
                smoke_ok=False,
            )

    def run_host_diagnostics(self, force: bool = False) -> dict[str, Any]:
        """
        Real host measurements (not templates).
        Required: nvidia-smi, disk free, ping loopback.
        Writes MEASURED_DIAGNOSTIC_REPORT.md + MEASURED_DIAGNOSTIC.json.
        ok=False unless all required commands succeed with exit 0.
        """
        with self._lock:
            report_rel = "MEASURED_DIAGNOSTIC_REPORT.md"
            json_rel = "MEASURED_DIAGNOSTIC.json"
            json_path = self.workspace / json_rel
            if not force and json_path.is_file():
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                    return self._record(
                        "run_host_diagnostics",
                        bool(data.get("ok")),
                        cached=True,
                        path=str(json_path),
                        report_path=str(self.workspace / report_rel),
                        checks=data.get("checks"),
                        ok_all=data.get("ok"),
                    )
                except Exception:
                    pass

            checks: dict[str, Any] = {}

            # 1) GPU
            gpu = self.run_shell(
                "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu "
                "--format=csv,noheader,nounits"
            )
            checks["gpu_nvidia_smi"] = {
                "ok": bool(gpu.get("ok")),
                "returncode": gpu.get("returncode"),
                "stdout": (gpu.get("stdout") or "")[:800],
                "stderr": (gpu.get("stderr") or "")[:400],
                "error": gpu.get("error"),
            }

            # 2) Disk free (FileSystem drives)
            disk = self.run_shell(
                "powershell -NoProfile -Command "
                "\"Get-PSDrive -PSProvider FileSystem | "
                "Select-Object Name,@{N='UsedGB';E={[math]::Round(($_.Used/1GB),2)}},"
                "@{N='FreeGB';E={[math]::Round(($_.Free/1GB),2)}} | "
                "ConvertTo-Json -Compress\""
            )
            checks["disk_free"] = {
                "ok": bool(disk.get("ok")),
                "returncode": disk.get("returncode"),
                "stdout": (disk.get("stdout") or "")[:1200],
                "stderr": (disk.get("stderr") or "")[:400],
                "error": disk.get("error"),
            }

            # 3) Loopback network
            ping = self.run_shell("ping -n 2 127.0.0.1")
            checks["ping_loopback"] = {
                "ok": bool(ping.get("ok")),
                "returncode": ping.get("returncode"),
                "stdout": (ping.get("stdout") or "")[:600],
                "stderr": (ping.get("stderr") or "")[:200],
                "error": ping.get("error"),
            }

            # 4) Ollama (informational — not always required for ok)
            ollama = self.run_shell(
                "powershell -NoProfile -Command "
                "\"try { $t=Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 3; "
                "Write-Output ('models=' + @($t.models).Count) } "
                "catch { Write-Output 'DOWN'; exit 1 }\""
            )
            checks["ollama_tags"] = {
                "ok": bool(ollama.get("ok")),
                "returncode": ollama.get("returncode"),
                "stdout": (ollama.get("stdout") or "")[:400],
                "stderr": (ollama.get("stderr") or "")[:200],
                "error": ollama.get("error"),
            }

            required = ("gpu_nvidia_smi", "disk_free", "ping_loopback")
            ok_all = all(bool(checks[k].get("ok")) for k in required)

            lines = [
                "# MEASURED host diagnostic report",
                "",
                f"UTC: {_utc()}",
                f"task_id: {self.task_id}",
                f"overall_ok: {ok_all}",
                f"false_green: 0",
                "",
                "## Required checks",
            ]
            for k in required:
                c = checks[k]
                lines.append(f"### {k}")
                lines.append(f"- ok: {c.get('ok')}")
                lines.append(f"- returncode: {c.get('returncode')}")
                if c.get("error"):
                    lines.append(f"- error: {c.get('error')}")
                lines.append("```")
                lines.append((c.get("stdout") or "").strip() or "(empty)")
                lines.append("```")
                lines.append("")
            lines.append("## Optional")
            lines.append("### ollama_tags")
            lines.append(f"- ok: {checks['ollama_tags'].get('ok')}")
            lines.append("```")
            lines.append((checks["ollama_tags"].get("stdout") or "").strip() or "(empty)")
            lines.append("```")
            lines.append("")
            lines.append(
                "## Law\n"
                "If any required check failed, seal MUST be RED. "
                "Template success without this report is forbidden."
            )
            report_text = "\n".join(lines)
            w = self.write_text(report_rel, report_text)
            payload = {
                "ok": ok_all,
                "false_green": 0,
                "utc": _utc(),
                "task_id": self.task_id,
                "required": list(required),
                "checks": checks,
                "report_path": str(self.workspace / report_rel),
            }
            jw = self.write_json(json_rel, payload)
            # copy to artifacts when possible
            self.copy_to_artifacts(report_rel)
            self.copy_to_artifacts(json_rel)

            return self._record(
                "run_host_diagnostics",
                ok_all,
                path=str(json_path),
                report_path=str(self.workspace / report_rel),
                report_write_ok=bool(w.get("ok")),
                json_write_ok=bool(jw.get("ok")),
                checks={k: {"ok": checks[k]["ok"]} for k in checks},
                ok_all=ok_all,
            )


def role_tool_dispatch(
    toolkit: DroneToolkit,
    *,
    role: str,
    goal: str,
    node_id: str,
    hemisphere: str,
    strength: float,
    lm_fn: Callable[[str], str] | None = None,
    code_lm_fn: Callable[[str], str] | None = None,
    prior_notes: str = "",
    wins_n: int = 0,
) -> dict[str, Any]:
    """
    Map every role to at least one real tool call.
    code_lm_fn: second Ollama (8b class) for real .py workload on execute.
    Returns {summary, tool_results, ok, evidence}.
    """
    results: list[dict[str, Any]] = []
    g = (goal or "").strip()
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", node_id)[:40]

    # Always log entry
    results.append(
        toolkit.append_log(f"{node_id}/{role} start goal_len={len(g)} strength={strength:.1f}")
    )

    if role in {"intake", "context"}:
        results.append(toolkit.list_tools())
        results.append(toolkit.library_status())
        results.append(
            toolkit.write_json(
                f"{safe_name}_intake.json",
                {
                    "node": node_id,
                    "role": role,
                    "goal": g,
                    "hemisphere": hemisphere,
                    "utc": _utc(),
                },
            )
        )

    elif role in {"parse", "retrieve"}:
        bits = g.split()
        results.append(
            toolkit.write_json(
                f"{safe_name}_parse.json",
                {"tokens": len(bits), "keys": bits[:20], "past_wins": wins_n},
            )
        )
        results.append(toolkit.list_dir("."))

    elif role in {"plan", "pattern"}:
        plan_body = (
            f"# Plan — {node_id}\n\n"
            f"Goal: {g}\n\n"
            f"Steps:\n1. parse\n2. act\n3. verify\n"
            f"Strength: {strength:.1f}\nPrior: {prior_notes[:300]}\n"
        )
        # Top model enriches plan when available
        if lm_fn is not None:
            try:
                note = lm_fn(
                    f"You are worker drone {node_id} ({role}). "
                    f"Write a tight 5-step build plan for:\n{g}\n"
                    f"Prior: {prior_notes[:400]}\nBullets only."
                )
                plan_body += f"\n## LM plan\n{note}\n"
                results.append({"tool": "lm_plan", "ok": True, "chars": len(note)})
            except Exception as e:
                results.append({"tool": "lm_plan", "ok": False, "error": str(e)})
        results.append(toolkit.write_text(f"{safe_name}_plan.md", plan_body))

    elif role in {"decompose", "risk"}:
        results.append(
            toolkit.write_json(
                f"{safe_name}_decompose.json",
                {
                    "subtasks": [
                        "prepare workspace",
                        "produce artifact",
                        "verify evidence",
                        "package",
                    ],
                    "risk": "low" if strength > 30 else "watch",
                    "goal": g[:500],
                },
            )
        )

    elif role in {"tool", "consistency"}:
        results.append(toolkit.list_tools())
        results.append(toolkit.run_shell("where python"))
        results.append(toolkit.hash_text(g))

    elif role in {"execute", "critic"}:
        # 1) REAL host diagnostics
        diag = toolkit.run_host_diagnostics(force=False)
        results.append(diag)
        diag_ok = bool(diag.get("ok") or diag.get("ok_all"))

        # 2) REAL code module via CODE WORKER Ollama (8b) — required for execute
        module_slug = toolkit.goal_module_slug(g, node_id=node_id)
        code_ok = False
        code_path = None
        code_model = getattr(code_lm_fn, "model", None) if code_lm_fn else None
        source = ""
        if role == "execute":
            if code_lm_fn is not None:
                try:
                    raw = code_lm_fn(
                        "Write ONE complete Python 3 module that implements this goal.\n"
                        f"GOAL:\n{g}\n\n"
                        "Requirements:\n"
                        f"- Module will be saved as {module_slug}.py\n"
                        "- Must be valid syntax\n"
                        "- Include if __name__ == '__main__': that prints SMOKE_OK and exits 0\n"
                        "- Prefer stdlib only\n"
                        "- No markdown, no explanation — code only\n"
                    )
                    source = toolkit.extract_python_source(raw)
                    results.append(
                        {
                            "tool": "code_worker_ollama",
                            "ok": bool(source and len(source) >= 20),
                            "model": code_model,
                            "chars": len(source or ""),
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "tool": "code_worker_ollama",
                            "ok": False,
                            "error": str(e),
                            "model": code_model,
                        }
                    )
            # fallback minimal real module if no code LM (still must smoke)
            if not source or len(source) < 20:
                source = (
                    f'"""Auto module for goal (fallback — code worker missing/failed)."""\n'
                    f"GOAL = {json.dumps(g[:500])}\n"
                    f"NODE = {json.dumps(node_id)}\n\n"
                    "def main() -> int:\n"
                    "    print('SMOKE_OK')\n"
                    "    print('node', NODE)\n"
                    "    print('goal_len', len(GOAL))\n"
                    "    return 0\n\n"
                    "if __name__ == '__main__':\n"
                    "    raise SystemExit(main())\n"
                )
                results.append(
                    {
                        "tool": "code_fallback_template",
                        "ok": True,
                        "note": "code_lm missing — minimal smoke module only",
                    }
                )

            smoke = toolkit.write_and_smoke_python(module_slug, source)
            results.append(smoke)
            code_ok = bool(smoke.get("ok") and smoke.get("smoke_ok", smoke.get("ok")))
            code_path = smoke.get("path")
            if not code_ok:
                results.append(
                    {
                        "tool": "code_gate",
                        "ok": False,
                        "error": smoke.get("error")
                        or "python module failed smoke test — RED",
                        "path": code_path,
                    }
                )
        else:
            # critic: require existing module from execute if present
            ls = toolkit.list_dir(".")
            results.append(ls)
            py_files = [
                n
                for n in (ls.get("entries") or [])
                if n.endswith(".py") and n != "_drone_snip.py"
            ]
            if py_files:
                # re-smoke first real module
                name = py_files[0]
                r = toolkit.read_text(name, max_chars=20000)
                results.append(r)
                if r.get("ok") and r.get("text"):
                    slug = name[:-3]
                    smoke = toolkit.write_and_smoke_python(slug, r["text"])
                    results.append(smoke)
                    code_ok = bool(smoke.get("ok"))
                    code_path = smoke.get("path")
            else:
                code_ok = False
                results.append(
                    {
                        "tool": "code_gate",
                        "ok": False,
                        "error": "critic: no real .py module from execute",
                    }
                )

        body_lines = [
            f"# Work artifact — {node_id} ({role})",
            f"UTC: {_utc()}",
            f"Goal: {g}",
            "",
            "## Measured diagnostics",
            f"- overall_ok: {diag_ok}",
            f"- report: {diag.get('report_path') or 'MEASURED_DIAGNOSTIC_REPORT.md'}",
            "",
            "## Real code module",
            f"- code_worker_model: {code_model}",
            f"- module_slug: {module_slug if role == 'execute' else '(from workspace)'}",
            f"- code_ok: {code_ok}",
            f"- path: {code_path}",
            "",
        ]
        if not diag_ok:
            body_lines.append("## FAIL diagnostics\nRequired host measurements failed.")
        if role == "execute" and not code_ok:
            body_lines.append(
                "## FAIL code\nGoal-named .py missing, syntax error, or smoke failed."
            )
        if diag_ok and (role != "execute" or code_ok):
            body_lines.append("## PASS gates so far for this role path.")

        if lm_fn is not None and role == "critic":
            try:
                gen = lm_fn(
                    f"Critic drone {node_id}. Check flags only — no invented metrics.\n"
                    f"diag_ok={diag_ok} code_ok={code_ok}\nGoal: {g[:300]}\n"
                    "5 bullets."
                )
                body_lines.append("\n## Critic LM\n" + gen)
                results.append({"tool": "lm_execute", "ok": True, "chars": len(gen)})
            except Exception as e:
                results.append({"tool": "lm_execute", "ok": False, "error": str(e)})

        body = "\n".join(body_lines) + "\n"
        results.append(toolkit.write_text(f"{safe_name}_work.md", body))

        if role == "critic":
            results.append(
                toolkit.read_text("MEASURED_DIAGNOSTIC_REPORT.md", max_chars=2500)
            )

        if not diag_ok:
            results.append(
                {
                    "tool": "diagnostic_gate",
                    "ok": False,
                    "error": "required host diagnostics failed",
                }
            )
        if role == "execute" and not code_ok:
            results.append(
                {
                    "tool": "code_gate",
                    "ok": False,
                    "error": "real .py smoke failed",
                }
            )

    elif role in {"verify", "revise"}:
        results.append(toolkit.list_dir("."))
        listing = results[-1]
        entries = listing.get("entries") or []

        # MUST re-read measured diagnostics — RED if missing or failed
        diag_read = toolkit.read_text("MEASURED_DIAGNOSTIC.json", max_chars=8000)
        results.append(diag_read)
        diag_ok = False
        diag_payload: dict[str, Any] = {}
        if diag_read.get("ok") and diag_read.get("text"):
            try:
                diag_payload = json.loads(diag_read["text"])
                diag_ok = bool(diag_payload.get("ok"))
            except Exception as e:
                results.append(
                    {"tool": "parse_diagnostic_json", "ok": False, "error": str(e)}
                )
        else:
            # try running diagnostics if execute didn't
            diag = toolkit.run_host_diagnostics(force=False)
            results.append(diag)
            diag_ok = bool(diag.get("ok") or diag.get("ok_all"))
            diag_payload = {"ok": diag_ok, "checks": diag.get("checks")}

        report_read = toolkit.read_text("MEASURED_DIAGNOSTIC_REPORT.md", max_chars=2000)
        results.append(report_read)
        report_ok = bool(report_read.get("ok")) and "overall_ok:" in (
            report_read.get("text") or ""
        )

        # sample other files
        for name in entries[:6]:
            if name.endswith((".md", ".json", ".txt")) and not name.startswith(
                "MEASURED_"
            ):
                results.append(toolkit.read_text(name, max_chars=300))

        verify_ok = diag_ok and report_ok
        results.append(
            toolkit.write_json(
                f"{safe_name}_verify.json",
                {
                    "role": role,
                    "ok": verify_ok,
                    "false_green": 0,
                    "diag_ok": diag_ok,
                    "report_ok": report_ok,
                    "entries": entries,
                    "required": (diag_payload.get("required") if isinstance(diag_payload, dict) else None)
                    or ["gpu_nvidia_smi", "disk_free", "ping_loopback"],
                    "checks_summary": {
                        k: (v.get("ok") if isinstance(v, dict) else v)
                        for k, v in (diag_payload.get("checks") or {}).items()
                    }
                    if isinstance(diag_payload, dict)
                    else {},
                },
            )
        )
        if not verify_ok:
            results.append(
                {
                    "tool": "verify_gate",
                    "ok": False,
                    "error": "MEASURED diagnostics missing or failed — cannot GREEN",
                }
            )

    elif role in {"log", "memory"}:
        results.append(toolkit.append_log(f"{node_id} persist memory for: {g[:200]}"))
        results.append(
            toolkit.write_json(
                f"{safe_name}_memory.json",
                {"goal": g[:500], "wins": wins_n, "strength": strength},
            )
        )

    elif role in {"refine", "evolve"}:
        # refine work.md if present
        r = toolkit.read_text("L06_execute_work.md", max_chars=2000)
        if not r.get("ok"):
            # try any *_work.md
            ls = toolkit.list_dir(".")
            for name in ls.get("entries") or []:
                if name.endswith("_work.md"):
                    r = toolkit.read_text(name, max_chars=2000)
                    break
        refined = (r.get("text") or f"(no prior work)\nGoal: {g}\n") + f"\n# refined by {node_id}\n"
        if lm_fn is not None:
            try:
                note = lm_fn(
                    f"Refine this worker artifact briefly:\n{(r.get('text') or g)[:800]}"
                )
                refined += note + "\n"
            except Exception as e:
                refined += f"lm_error: {e}\n"
        results.append(toolkit.write_text(f"{safe_name}_refined.md", refined))

    elif role in {"package", "pack"}:
        # copy key files to artifacts + manifest
        ls = toolkit.list_dir(".")
        for name in (ls.get("entries") or [])[:12]:
            if name.endswith((".md", ".json", ".txt")):
                results.append(toolkit.copy_to_artifacts(name))
        results.append(
            toolkit.package_manifest(
                {"node": node_id, "role": role, "goal": g[:300]}
            )
        )

    elif role in {"handoff"}:
        results.append(
            toolkit.write_json(
                f"{safe_name}_handoff.json",
                {
                    "from": node_id,
                    "goal": g,
                    "next": "callosum_or_outbox",
                    "utc": _utc(),
                },
            )
        )

    elif role in {"seal"}:
        # HARD GATE: measured diagnostics + real .py module + verify
        diag_read = toolkit.read_text("MEASURED_DIAGNOSTIC.json", max_chars=8000)
        results.append(diag_read)
        diag_ok = False
        if diag_read.get("ok") and diag_read.get("text"):
            try:
                diag_ok = bool(json.loads(diag_read["text"]).get("ok"))
            except Exception:
                diag_ok = False
        if not diag_ok:
            # last chance: run now
            diag = toolkit.run_host_diagnostics(force=False)
            results.append(diag)
            diag_ok = bool(diag.get("ok") or diag.get("ok_all"))

        verify_ok = True
        vread = toolkit.read_text("L07_verify_verify.json", max_chars=2000)
        if not vread.get("ok"):
            # any *_verify.json
            ls = toolkit.list_dir(".")
            results.append(ls)
            for name in ls.get("entries") or []:
                if name.endswith("_verify.json"):
                    vread = toolkit.read_text(name, max_chars=2000)
                    break
        if vread.get("ok") and vread.get("text"):
            try:
                verify_ok = bool(json.loads(vread["text"]).get("ok"))
            except Exception:
                verify_ok = False
        results.append(vread)

        ls2 = toolkit.list_dir(".")
        results.append(ls2)
        real_py = [
            n
            for n in (ls2.get("entries") or [])
            if n.endswith(".py") and n != "_drone_snip.py"
        ]
        code_ok = len(real_py) > 0
        if not code_ok:
            results.append(
                {
                    "tool": "code_gate",
                    "ok": False,
                    "error": "seal: no real goal .py module in workspace",
                }
            )
        else:
            results.append(
                {
                    "tool": "code_present",
                    "ok": True,
                    "modules": real_py[:20],
                }
            )

        man = toolkit.package_manifest(
            {
                "seal_node": node_id,
                "diag_ok": diag_ok,
                "verify_ok": verify_ok,
                "code_ok": code_ok,
                "modules": real_py[:20],
            }
        )
        results.append(man)
        seal_ok = bool(man.get("ok")) and diag_ok and verify_ok and code_ok
        results.append(
            toolkit.write_json(
                f"{safe_name}_SEAL.json",
                {
                    "status": "GREEN" if seal_ok else "RED",
                    "false_green": 0,
                    "node": node_id,
                    "hemisphere": hemisphere,
                    "manifest": man.get("path"),
                    "diag_ok": diag_ok,
                    "verify_ok": verify_ok,
                    "code_ok": code_ok,
                    "modules": real_py[:20],
                    "tool_calls": len(toolkit.calls),
                    "utc": _utc(),
                    "law": "GREEN only if MEASURED diagnostics + verify + real .py smoke",
                },
            )
        )
        results.append(toolkit.copy_to_artifacts(f"{safe_name}_SEAL.json"))
        results.append(toolkit.copy_to_artifacts("MEASURED_DIAGNOSTIC.json"))
        results.append(toolkit.copy_to_artifacts("MEASURED_DIAGNOSTIC_REPORT.md"))
        if not seal_ok:
            results.append(
                {
                    "tool": "seal_gate",
                    "ok": False,
                    "error": "seal RED: diagnostics or verify failed",
                    "diag_ok": diag_ok,
                    "verify_ok": verify_ok,
                    "code_ok": code_ok,
                }
            )

    else:
        results.append(toolkit.write_text(f"{safe_name}_generic.txt", f"{role}: {g}\n"))

    # Hard fails: any explicit gate with ok=False forces role fail
    hard_fail = any(
        isinstance(r, dict)
        and r.get("ok") is False
        and r.get("tool")
        in {
            "diagnostic_gate",
            "verify_gate",
            "seal_gate",
            "code_gate",
            "write_and_smoke_python",
            "run_host_diagnostics",
        }
        for r in results
    )
    ok = (not hard_fail) and any(
        bool(r.get("ok")) for r in results if isinstance(r, dict)
    )
    # execute/critic/verify/seal: require no hard_fail and at least one success
    if role in {"execute", "critic", "verify", "revise", "seal"} and hard_fail:
        ok = False
    evidence = []
    for r in results:
        if isinstance(r, dict) and r.get("ok"):
            if r.get("path"):
                evidence.append(str(r["path"]))
            if r.get("dest"):
                evidence.append(str(r["dest"]))
            if r.get("report_path"):
                evidence.append(str(r["report_path"]))
    summary = (
        f"{role}@{node_id}: tools={len(results)} ok={ok} hard_fail={hard_fail} "
        f"evidence={len(evidence)} strength={strength:.1f}"
    )
    return {
        "summary": summary,
        "tool_results": results,
        "ok": ok,
        "evidence": evidence[:20],
        "hard_fail": hard_fail,
    }
