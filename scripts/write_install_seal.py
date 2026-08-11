"""Write out/INSTALL_SEAL.json after a successful install."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from drone.app.config import app_root
    from drone.app.service import DroneHiveService

    r = app_root()
    h = DroneHiveService(r).health()
    seal = {
        "schema": "drone.hive.install_seal.v1",
        "status": "GREEN" if h.get("ok") else "RED",
        "false_green": 0,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(r),
        "health": h,
        "python": sys.executable,
        "mode": "editable_pip",
        "package": "dronehive",
        "version": h.get("version") or "1.0.0",
    }
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "INSTALL_SEAL.json"
    path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(path), "status": seal["status"]}, indent=2))
    return 0 if h.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
