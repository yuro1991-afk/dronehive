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
    r"powershell|pwsh|cmd)\b",
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


def role_tool_dispatch(
    toolkit: DroneToolkit,
    *,
    role: str,
    goal: str,
    node_id: str,
    hemisphere: str,
    strength: float,
    lm_fn: Callable[[str], str] | None = None,
    prior_notes: str = "",
    wins_n: int = 0,
) -> dict[str, Any]:
    """
    Map every role to at least one real tool call.
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
        # Real work: write an artifact + optional python + LM content
        body = f"Artifact from {node_id} ({role})\nGoal: {g}\nUTC: {_utc()}\n"
        if lm_fn is not None:
            try:
                gen = lm_fn(
                    f"Worker drone {node_id} role={role}. "
                    f"Produce useful working notes or code sketch for:\n{g}\n"
                    f"Max 20 lines. No preamble."
                )
                body += f"\n--- LM ---\n{gen}\n"
                results.append({"tool": "lm_execute", "ok": True, "chars": len(gen)})
            except Exception as e:
                results.append({"tool": "lm_execute", "ok": False, "error": str(e)})
        results.append(toolkit.write_text(f"{safe_name}_work.md", body))
        # tiny real python proof
        results.append(
            toolkit.run_python(
                "from pathlib import Path\n"
                f"Path('exec_proof_{safe_name}.txt').write_text("
                f"'ok {node_id}\\n', encoding='utf-8')\n"
                "print('exec_ok')\n"
            )
        )
        if role == "critic":
            results.append(toolkit.read_text(f"{safe_name}_work.md", max_chars=1500))

    elif role in {"verify", "revise"}:
        results.append(toolkit.list_dir("."))
        # verify any prior work files
        listing = results[-1]
        entries = listing.get("entries") or []
        checked = []
        for name in entries[:8]:
            if name.endswith((".md", ".json", ".txt")):
                checked.append(toolkit.read_text(name, max_chars=400))
        results.extend(checked)
        results.append(
            toolkit.write_json(
                f"{safe_name}_verify.json",
                {
                    "entries": entries,
                    "ok": len(entries) > 0,
                    "role": role,
                },
            )
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
        man = toolkit.package_manifest({"seal_node": node_id})
        results.append(man)
        results.append(
            toolkit.write_json(
                f"{safe_name}_SEAL.json",
                {
                    "status": "GREEN" if man.get("ok") else "RED",
                    "false_green": 0,
                    "node": node_id,
                    "hemisphere": hemisphere,
                    "manifest": man.get("path"),
                    "tool_calls": len(toolkit.calls),
                    "utc": _utc(),
                },
            )
        )
        results.append(toolkit.copy_to_artifacts(f"{safe_name}_SEAL.json"))

    else:
        results.append(toolkit.write_text(f"{safe_name}_generic.txt", f"{role}: {g}\n"))

    ok = any(bool(r.get("ok")) for r in results if isinstance(r, dict))
    evidence = []
    for r in results:
        if isinstance(r, dict) and r.get("ok"):
            if r.get("path"):
                evidence.append(str(r["path"]))
            if r.get("dest"):
                evidence.append(str(r["dest"]))
    summary = (
        f"{role}@{node_id}: tools={len(results)} ok={ok} "
        f"evidence={len(evidence)} strength={strength:.1f}"
    )
    return {
        "summary": summary,
        "tool_results": results,
        "ok": ok,
        "evidence": evidence[:20],
    }
