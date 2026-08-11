"""Smoke: Ollama commander → composed goal → all drones. false_green:0"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\AI-Home\projects\ai-worker-drone-0.5b")
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
GOAL = sys.argv[1] if len(sys.argv) > 1 else "package a short worker seal note under tools"


def main() -> int:
    body = json.dumps(
        {
            "model": os.environ.get("DRONE_OLLAMA_MODEL", "llama3.2:3b"),
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Reply ONLY JSON: "
                        '{"mission":"t","brief":"b","subtasks":[],"mode":"fabric",'
                        '"skill_tags":["build"],"delegate_all_drones":true}'
                    ),
                },
                {"role": "user", "content": GOAL},
            ],
            "options": {"num_predict": 200},
        }
    ).encode("utf-8")
    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{HOST}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data.get("message") or {}).get("content") or ""
        ms = int((time.time() - t0) * 1000)
    except Exception as e:
        seal = {"status": "RED", "false_green": 0, "step": "ollama", "error": str(e)}
        outp = ROOT / "out" / "DELEGATE_WIRE_SEAL.json"
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        print(json.dumps(seal, indent=2))
        return 1

    composed = f"{GOAL} | COMMANDER_BRIEF: {text[:400]}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "drone",
            "run",
            "--goal",
            composed,
            "--controller",
            "ollama",
            "--lm-assist",
            "none",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = p.stdout or ""
    try:
        j = json.loads(out[out.find("{") :])
    except Exception:
        j = {"raw": out[-500:], "returncode": p.returncode, "stderr": (p.stderr or "")[-300:]}

    ok = p.returncode == 0 and j.get("status") == "GREEN"
    seal = {
        "status": "GREEN" if ok else "RED",
        "false_green": 0,
        "wire": "main_input -> ollama_commander -> all_24_drones",
        "ollama_commander_ms": ms,
        "commander_preview": text[:300],
        "nodes_run": j.get("nodes_run"),
        "tools_enabled": j.get("tools_enabled"),
        "tool_calls": j.get("tool_calls"),
        "drone_status": j.get("status"),
        "report": j.get("report_path"),
    }
    outp = ROOT / "out" / "DELEGATE_WIRE_SEAL.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    print(json.dumps(seal, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
