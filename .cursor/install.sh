#!/usr/bin/env bash
#
# Idempotent Cloud Agent setup for DroneHive.
#
# Prepares the two developer-facing surfaces that run on a headless Linux agent:
#   1. The Python CLI  (`python -m drone ...` / `dronehive`) — stdlib-only core.
#   2. The lean Rust TUI (`apps/dronehive-tui`) — needs Rust >= 1.85 (edition2024).
#
# Kept dependency-only on purpose: it must never run `drone` subcommands, because
# those write runtime state (and hardcoded Windows-style paths) into the working
# directory and would pollute the checked-out snapshot.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "[install] Python package (stdlib-only core, editable install)"
# --user keeps it in ~/.local; --break-system-packages tolerates Debian's
# PEP 668 EXTERNALLY-MANAGED marker (harmless no-op when absent). The core has
# zero third-party deps, so only the editable 'dronehive' package is installed;
# the build backend (setuptools/wheel) is fetched in pip's isolated build env.
python3 -m pip install --user --break-system-packages -e .

echo "[install] Rust stable toolchain for the lean TUI"
export RUSTUP_HOME="${RUSTUP_HOME:-/usr/local/rustup}"
rustup toolchain install stable --profile minimal --no-self-update
rustup default stable

echo "[install] Prebuild dronehive-tui release binary"
( cd apps/dronehive-tui && cargo build --release --locked )

echo "[install] done"
