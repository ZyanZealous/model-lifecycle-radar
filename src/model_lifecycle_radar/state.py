"""Validated state persistence with same-directory atomic replacement."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import RadarState, StateError


class ConcurrentWriteError(StateError):
    """Another process currently owns the state lock."""


def load_state(path: str | Path, *, now: datetime | None = None) -> RadarState:
    target = Path(path)
    if not target.exists():
        return RadarState.empty(now or datetime.now(timezone.utc))
    try:
        if target.stat().st_size > 64 * 1024 * 1024:
            raise StateError("state file exceeds the 64 MiB safety limit")
        value = json.loads(target.read_text(encoding="utf-8"))
    except StateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("state file is unreadable or invalid JSON") from exc
    return RadarState.from_dict(value)


def save_state(path: str | Path, state: RadarState) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        if os.name != "nt":
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


class StateLock:
    """Portable exclusive create lock; a leftover lock requires human review."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._owned = False

    def __enter__(self) -> "StateLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConcurrentWriteError("another writer holds the radar state lock") from exc
        try:
            message = f"pid={os.getpid()}\nstarted_at={datetime.now(timezone.utc).isoformat()}\n".encode()
            os.write(fd, message)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._owned = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._owned = False
