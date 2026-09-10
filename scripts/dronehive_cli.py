"""Frozen-binary entry point for the DroneHive CLI backend.

PyInstaller freezes this into a standalone `dronehive` executable that needs no
system Python. It simply delegates to the full `python -m drone ...` CLI.
"""

from __future__ import annotations

import sys

from drone.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
