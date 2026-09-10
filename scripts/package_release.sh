#!/usr/bin/env bash
#
# Package DroneHive as REAL, on-disk, installable artifacts on Linux.
#
# Produces (under release/ and dist/):
#   1. Python wheel + sdist                (pip-installable backend + CLI)
#   2. A clean-venv REAL (non-editable) install smoke, proving the installed
#      `dronehive` console script runs end-to-end (health / task / pro).
#   3. A standalone `dronehive` CLI binary (PyInstaller onefile) that needs no
#      system Python.
#   4. The lean Rust TUI release binary (when a Rust toolchain is available).
#   5. A distributable tarball + release/RELEASE_MANIFEST.json and
#      out/INSTALL_SEAL.json (on-disk evidence — false_green: 0).
#
# Usage:  bash scripts/package_release.sh [--no-binary] [--no-tui]
# Env:    PYTHON=python3   (override interpreter)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
BUILD_BINARY=1
BUILD_TUI=1
for arg in "$@"; do
  case "$arg" in
    --no-binary) BUILD_BINARY=0 ;;
    --no-tui) BUILD_TUI=0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

VERSION="$("$PY" - <<'PYEOF'
import pathlib, re
try:
    import tomllib
    print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])
except Exception:
    m = re.search(r'^version\s*=\s*"([^"]+)"', pathlib.Path("pyproject.toml").read_text(), re.M)
    print(m.group(1) if m else "0.0.0")
PYEOF
)"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PLAT="linux-$(uname -m)"
REL="$ROOT/release"
DIST="$ROOT/dist"
OUT="$ROOT/out"
mkdir -p "$REL" "$OUT"

pip_user() { "$PY" -m pip install --user --break-system-packages -q "$@"; }

echo "== [1/6] Build wheel + sdist (v$VERSION) =="
rm -rf "$DIST" build
"$PY" -c 'import build' 2>/dev/null || pip_user build
"$PY" -m build --outdir "$DIST"
WHEEL="$(ls "$DIST"/dronehive-"$VERSION"-*.whl | head -1)"
SDIST="$(ls "$DIST"/dronehive-"$VERSION".tar.gz | head -1)"
echo "   wheel: $(basename "$WHEEL")"
echo "   sdist: $(basename "$SDIST")"

echo "== [2/6] Clean-venv REAL install + CLI smoke =="
VENV_PARENT="$(mktemp -d)"
VENV="$VENV_PARENT/venv"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/pip" install -q "$WHEEL"
# Run from a neutral directory so the source checkout never shadows the install.
SMOKE_DIR="$(mktemp -d)"
(
  cd "$SMOKE_DIR"
  "$VENV/bin/dronehive" health > "$OUT/_smoke_health.json"
  "$VENV/bin/dronehive" task --goal "release smoke" --lane fast \
    --lm-assist none --controller local > "$OUT/_smoke_task.json"
  "$VENV/bin/dronehive" pro --goal "release proof" --rounds 3 \
    --no-ollama > "$OUT/_smoke_pro.log" 2>/dev/null
)
read -r HEALTH_OK TASK_OK PRO_OK <<PYEOF
$("$PY" - "$OUT" <<'PYIN'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
h = json.loads((out / "_smoke_health.json").read_text())
t = json.loads((out / "_smoke_task.json").read_text())
pro = "RED"
for line in (out / "_smoke_pro.log").read_text().splitlines():
    if line.startswith("SEAL|"):
        pro = json.loads(line[5:]).get("status", "RED")
print(("GREEN" if h.get("ok") else "RED"), t.get("status", "RED"), pro)
PYIN
)
PYEOF
echo "   installed CLI: health=$HEALTH_OK task=$TASK_OK pro=$PRO_OK"
if [ "$HEALTH_OK" != "GREEN" ] || [ "$TASK_OK" != "GREEN" ] || [ "$PRO_OK" != "GREEN" ]; then
  echo "   ERROR: installed CLI smoke did not pass" >&2
  exit 1
fi

CLI_BIN=""
if [ "$BUILD_BINARY" = "1" ]; then
  echo "== [3/6] Standalone dronehive CLI binary (PyInstaller onefile) =="
  # Freeze from the venv where 'drone' is a real installed package so PyInstaller
  # can collect its lazily-imported submodules.
  "$VENV/bin/pip" install -q pyinstaller
  rm -rf build/pyi
  "$VENV/bin/pyinstaller" --onefile --name dronehive \
    --distpath "$DIST/bin" --workpath build/pyi --specpath build/pyi \
    --collect-submodules drone --collect-data drone \
    "$ROOT/scripts/dronehive_cli.py" >/dev/null 2>&1
  CLI_BIN="$DIST/bin/dronehive"
  ( cd "$SMOKE_DIR" && "$CLI_BIN" app health >/dev/null ) \
    && echo "   standalone binary OK: $(du -h "$CLI_BIN" | cut -f1) $CLI_BIN"
else
  echo "== [3/6] Standalone CLI binary skipped (--no-binary) =="
fi

TUI_BIN=""
if [ "$BUILD_TUI" = "1" ]; then
  echo "== [4/6] Rust TUI release binary =="
  if command -v rustup >/dev/null 2>&1 || command -v cargo >/dev/null 2>&1; then
    (
      cd apps/dronehive-tui
      if command -v rustup >/dev/null 2>&1; then
        rustup run stable cargo build --release --locked
      else
        cargo build --release --locked
      fi
    )
    TUI_BIN="$ROOT/apps/dronehive-tui/target/release/dronehive-tui"
    echo "   TUI OK: $(du -h "$TUI_BIN" | cut -f1) $TUI_BIN"
  else
    echo "   no Rust toolchain — skipping TUI"
  fi
else
  echo "== [4/6] Rust TUI skipped (--no-tui) =="
fi

echo "== [5/6] Stage distributable tarball =="
STAGE_PARENT="$(mktemp -d)"
STAGE="$STAGE_PARENT/dronehive-$VERSION-$PLAT"
mkdir -p "$STAGE"
cp "$WHEEL" "$SDIST" "$STAGE"/
[ -n "$CLI_BIN" ] && cp "$CLI_BIN" "$STAGE"/dronehive
[ -n "$TUI_BIN" ] && cp "$TUI_BIN" "$STAGE"/dronehive-tui
cat > "$STAGE/INSTALL.md" <<EOF
# DroneHive $VERSION — $PLAT

## Option A — pip install the backend + CLI (recommended)
    python3 -m pip install ./$(basename "$WHEEL")
    dronehive health
    dronehive task --goal "hello" --lane fast --lm-assist none --controller local
    dronehive pro   --goal "write proof.txt" --rounds 3 --no-ollama

The installed app seeds its own on-disk workspace under
~/.local/share/DroneHive/workspace (configs + data). Point it elsewhere with
DRONE_HIVE_ROOT=/path/to/workspace.

## Option B — standalone CLI binary (no system Python)
    ./dronehive app health

## Option C — lean Rust TUI
    ./dronehive-tui --python python3

false_green: 0 — every GREEN is backed by on-disk evidence.
EOF
TARBALL="$REL/dronehive-$VERSION-$PLAT.tar.gz"
rm -f "$TARBALL"
tar -czf "$TARBALL" -C "$STAGE_PARENT" "$(basename "$STAGE")"
cp "$WHEEL" "$SDIST" "$REL"/

echo "== [6/6] Write RELEASE_MANIFEST.json + INSTALL_SEAL.json =="
"$PY" - "$ROOT" "$VERSION" "$STAMP" "$PLAT" "$WHEEL" "$SDIST" "$TARBALL" "${CLI_BIN:-}" "${TUI_BIN:-}" "$HEALTH_OK" "$TASK_OK" "$PRO_OK" <<'PYIN'
import hashlib, json, os, sys
(root, version, stamp, plat, wheel, sdist, tarball, cli_bin, tui_bin,
 health_ok, task_ok, pro_ok) = sys.argv[1:13]

def rec(path):
    if not path or not os.path.isfile(path):
        return None
    b = open(path, "rb").read()
    return {
        "name": os.path.basename(path),
        "path": path,
        "bytes": len(b),
        "sha256": hashlib.sha256(b).hexdigest(),
    }

all_ok = (health_ok == "GREEN" and task_ok == "GREEN" and pro_ok == "GREEN")
manifest = {
    "schema": "drone.hive.release.linux.v1",
    "app": "DroneHive",
    "version": version,
    "platform": plat,
    "utc": stamp,
    "false_green": 0,
    "status": "GREEN" if all_ok else "RED",
    "artifacts": {
        "wheel": rec(wheel),
        "sdist": rec(sdist),
        "cli_binary": rec(cli_bin),
        "tui_binary": rec(tui_bin),
        "tarball": rec(tarball),
    },
    "installed_cli_smoke": {"health": health_ok, "task": task_ok, "pro": pro_ok},
}
rel = os.path.join(root, "release", "RELEASE_MANIFEST.json")
open(rel, "w").write(json.dumps(manifest, indent=2) + "\n")

seal = {
    "schema": "drone.hive.install_seal.v1",
    "status": "GREEN" if all_ok else "RED",
    "false_green": 0,
    "utc": stamp,
    "mode": "wheel_install",
    "package": "dronehive",
    "version": version,
    "installed_cli_smoke": {"health": health_ok, "task": task_ok, "pro": pro_ok},
    "artifacts": [a["name"] for a in manifest["artifacts"].values() if a],
}
os.makedirs(os.path.join(root, "out"), exist_ok=True)
open(os.path.join(root, "out", "INSTALL_SEAL.json"), "w").write(json.dumps(seal, indent=2) + "\n")
print("   RELEASE_MANIFEST.json + INSTALL_SEAL.json written")
PYIN

# Cleanup scratch smoke artifacts (keep the seals + manifest).
rm -f "$OUT/_smoke_health.json" "$OUT/_smoke_task.json" "$OUT/_smoke_pro.log"
rm -rf "$VENV_PARENT" "$SMOKE_DIR" "$STAGE_PARENT"

echo
echo "RELEASE OK — v$VERSION ($PLAT)"
ls -la "$REL"
