#!/usr/bin/env python3
"""
Atomic, crash-safe JSON state store.

Production requirement: a container killed mid-write must never leave corrupt
state. Every write goes to a temp file in the same directory, is fsync'd, then
atomically rename()'d over the target. rename() within a filesystem is atomic
on POSIX, so a reader either sees the old file or the new one — never a partial.

An advisory flock guards against two processes writing the same file.
"""
import errno
import fcntl
import json
import os
import shutil
import tempfile
import time
from typing import Any


class StateStore:
    """Atomic JSON document store rooted at a data directory."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    # ---------- read ----------

    def read(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            return default if default is not None else {}
        except json.JSONDecodeError:
            # Corrupt file: fall back to the most recent good snapshot if present
            recovered = self._recover(name)
            if recovered is not None:
                return recovered
            return default if default is not None else {}

    def _recover(self, name: str) -> Any:
        bak = self.path(name + ".bak")
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    # ---------- write ----------

    def write(self, name: str, data: Any) -> str:
        """Atomically replace `name` with `data`. Keeps a .bak of the prior good copy."""
        target = self.path(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)

        lock_path = target + ".lock"
        with open(lock_path, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                # keep previous version as .bak for corruption recovery
                if os.path.exists(target):
                    try:
                        shutil.copy2(target, target + ".bak")
                    except OSError:
                        pass

                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(target), prefix=".tmp-", suffix=".json"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, target)  # atomic
                    # fsync the directory so the rename itself is durable
                    dfd = os.open(os.path.dirname(target), os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return target

    def update(self, name: str, fn, default: Any = None) -> Any:
        """Read-modify-write under one lock. `fn(current) -> new`."""
        lock_path = self.path(name) + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current = self.read(name, default)
                new = fn(current)
                # inline write (already hold the lock)
                target = self.path(name)
                if os.path.exists(target):
                    try:
                        shutil.copy2(target, target + ".bak")
                    except OSError:
                        pass
                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(target), prefix=".tmp-", suffix=".json"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(new, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, target)
                return new
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # ---------- introspection ----------

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def size(self, name: str) -> int:
        try:
            return os.path.getsize(self.path(name))
        except OSError:
            return 0

    def mtime(self, name: str) -> float:
        try:
            return os.path.getmtime(self.path(name))
        except OSError:
            return 0.0
