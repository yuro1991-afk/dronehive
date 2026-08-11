"""Application configuration for standalone DroneHive (portable / installable)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

APP_SCHEMA = "drone.hive.app.v1"
DEFAULT_VERSION = "1.0.0"


def user_install_dir() -> Path:
    """Windows: %LOCALAPPDATA%\\Programs\\DroneHive · else ~/.local/share/DroneHive."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Programs" / "DroneHive"
    return Path.home() / ".local" / "share" / "DroneHive"


def user_workspace() -> Path:
    """Persistent data/configs workspace for installed app (not the git checkout)."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "DroneHive" / "workspace"
    return Path.home() / ".local" / "share" / "DroneHive" / "workspace"


def _package_seed_root() -> Path | None:
    """Bundled seed configs shipped inside the package (or next to frozen exe)."""
    # 1) frozen: PyInstaller _MEIPASS or exe dir
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = Path(meipass)
            if (p / "configs").is_dir():
                return p
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "configs").is_dir():
            return exe_dir
        if (exe_dir / "_internal" / "configs").is_dir():
            return exe_dir / "_internal"
    # 2) package-adjacent seed (drone/seed/…)
    here = Path(__file__).resolve().parent
    seed = here / "seed"
    if (seed / "configs").is_dir():
        return seed
    # 3) source tree root (drone/app → parents[2])
    src = here.parents[1]  # drone/
    root = here.parents[2]  # project root when developing
    if (root / "configs").is_dir():
        return root
    return None


def seed_workspace(dest: Path) -> dict[str, Any]:
    """
    Copy seed configs + create data/out dirs into dest if missing.
    Safe to call repeatedly (does not overwrite existing config files).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    seed = _package_seed_root()
    copied: list[str] = []
    cfg_dest = dest / "configs"
    cfg_dest.mkdir(parents=True, exist_ok=True)

    if seed and (seed / "configs").is_dir():
        for src in (seed / "configs").glob("*.json"):
            target = cfg_dest / src.name
            if not target.is_file():
                shutil.copy2(src, target)
                copied.append(str(target))
    else:
        # Minimal app.json if no seed available
        app_json = cfg_dest / "app.json"
        if not app_json.is_file():
            app_json.write_text(
                json.dumps(
                    {
                        "schema": APP_SCHEMA,
                        "name": "DroneHive",
                        "version": DEFAULT_VERSION,
                        "host": "127.0.0.1",
                        "port": 8765,
                        "lane_default": "fast",
                        "lm_assist_default": "none",
                        "controller_default": "local",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            copied.append(str(app_json))

    for rel in (
        "data/app/inbox",
        "data/app/outbox",
        "data/app/links",
        "data/experience",
        "data/skills",
        "data/builds",
        "data/hive/buzzers",
        "data/hive/library_outbox",
        "data/hive/memory_recycle",
        "data/hive/imprints/active",
        "data/hive/imprints/history",
        "data/workspace",
        "out",
        "static",
        "docs",
    ):
        (dest / rel).mkdir(parents=True, exist_ok=True)

    # Copy docs if seed has them and dest missing
    if seed and (seed / "docs").is_dir():
        docs_dest = dest / "docs"
        for src in (seed / "docs").glob("*.md"):
            target = docs_dest / src.name
            if not target.is_file():
                shutil.copy2(src, target)
                copied.append(str(target))

    marker = dest / ".dronehive_workspace"
    if not marker.is_file():
        marker.write_text(
            json.dumps(
                {
                    "schema": "drone.hive.workspace.v1",
                    "version": DEFAULT_VERSION,
                    "seeded_from": str(seed) if seed else None,
                    "false_green": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return {"ok": True, "root": str(dest), "copied": copied, "seed": str(seed) if seed else None}


def app_root() -> Path:
    """
    Project / workspace root (data + configs live here).

    Order:
      1) DRONE_HIVE_ROOT env
      2) APP_ROOT.txt next to frozen exe or install dir
      3) Source checkout (configs/ next to package parents)
      4) User workspace under LOCALAPPDATA (auto-seeded)
    """
    env = (os.environ.get("DRONE_HIVE_ROOT") or "").strip()
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        if not (p / "configs" / "app.json").is_file():
            seed_workspace(p)
        return p

    # APP_ROOT.txt near install / frozen exe
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "APP_ROOT.txt",
                exe_dir.parent / "APP_ROOT.txt",
            ]
        )
    candidates.append(user_install_dir() / "APP_ROOT.txt")

    for candidate in candidates:
        if candidate.is_file():
            try:
                p = Path(candidate.read_text(encoding="utf-8").strip())
                if p.is_dir():
                    if not (p / "configs").is_dir():
                        seed_workspace(p)
                    return p
            except OSError:
                pass

    # Developing from source: package is drone/app/config.py → project root = parents[2]
    src = Path(__file__).resolve().parents[2]
    if (src / "configs").is_dir() and (src / "drone").is_dir():
        return src

    # Installed wheel / no checkout: portable user workspace
    ws = user_workspace()
    seed_workspace(ws)
    return ws


def load_app_config(root: Path | None = None) -> dict[str, Any]:
    root = Path(root or app_root())
    path = root / "configs" / "app.json"
    defaults = {
        "schema": APP_SCHEMA,
        "name": "DroneHive",
        "version": DEFAULT_VERSION,
        "host": "127.0.0.1",
        "port": 8765,
        "lane_default": "fast",
        "lm_assist_default": "none",
        "controller_default": "local",
        "universal_links": {
            "rest": True,
            "openai_compat": True,
            "file_inbox": True,
            "library": True,
            "continuous": True,
            "ollama": True,
            "webhook_out": True,
        },
        "paths": {
            "inbox": "data/app/inbox",
            "outbox": "data/app/outbox",
            "links": "data/app/links",
            "commission": "out/COMMISSION_SEAL.json",
        },
        "modularity": {
            "fabric": "drone.chain.BrainFabric",
            "hive": "drone.hive.BuzzerHive",
            "fast_lane": "drone.fast_lane.FastLane",
            "clean_slate": "drone.clean_slate",
            "tools": "drone.tools.DroneToolkit",
            "brain": "drone.ollama_brain",
            "links": "drone.app.links",
        },
        "honesty": {
            "full_models_per_drone": False,
            "dense_vector_db": False,
            "commission_requires_evidence": True,
        },
    }
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
            elif isinstance(v, dict) and isinstance(data.get(k), dict):
                merged = dict(v)
                merged.update(data[k])
                data[k] = merged
        data["root"] = str(root)
        data["config_path"] = str(path)
        return data
    defaults["root"] = str(root)
    defaults["config_path"] = str(path)
    defaults["missing_on_disk"] = True
    return defaults


def ensure_app_dirs(root: Path | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Path]:
    root = Path(root or app_root())
    cfg = cfg or load_app_config(root)
    paths = cfg.get("paths") or {}
    out: dict[str, Path] = {}
    for key in ("inbox", "outbox", "links"):
        rel = paths.get(key) or f"data/app/{key}"
        p = root / rel
        p.mkdir(parents=True, exist_ok=True)
        out[key] = p
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "static").mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir(parents=True, exist_ok=True)
    return out


def env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    c = dict(cfg)
    if os.environ.get("DRONE_HIVE_HOST"):
        c["host"] = os.environ["DRONE_HIVE_HOST"]
    if os.environ.get("DRONE_HIVE_PORT"):
        c["port"] = int(os.environ["DRONE_HIVE_PORT"])
    if os.environ.get("DRONE_HIVE_LANE"):
        c["lane_default"] = os.environ["DRONE_HIVE_LANE"]
    return c
