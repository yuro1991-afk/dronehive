"""Process-local locks for concurrent buzzer swarm disk IO."""

from __future__ import annotations

import threading
from pathlib import Path

# One hive-root lock registry (thread-safe swarm writes)
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_REG = threading.Lock()


def root_lock(root: Path | str) -> threading.RLock:
    key = str(Path(root).resolve())
    with _REG:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock
