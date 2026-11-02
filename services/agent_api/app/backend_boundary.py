"""One backend process per canonical database; orchestrators share cancellation."""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def backend_database_lock(database_path: Path):
    """Hold an OS lock through startup recovery and the complete application lifespan."""
    canonical = database_path.resolve()
    path = canonical.with_name(canonical.name + ".backend.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Only one backend process may use this database at a time.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ProcessCancellation:
    """Coordinate cooperative actions across instances/threads in this backend process."""

    _lock = threading.Lock()
    _actions: dict[tuple[str, str], tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}

    @staticmethod
    def key(database_path: Path, task_id: str) -> tuple[str, str]:
        return os.path.normcase(str(database_path.resolve())), task_id

    @classmethod
    def register(cls, key, action):
        with cls._lock:
            cls._actions[key] = (asyncio.get_running_loop(), action)

    @classmethod
    def remove(cls, key, action):
        with cls._lock:
            if key in cls._actions and cls._actions[key][1] is action:
                del cls._actions[key]

    @classmethod
    def cancel(cls, key):
        with cls._lock:
            active = cls._actions.get(key)
        if active is None:
            return
        loop, action = active
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            action.cancel()
        else:
            loop.call_soon_threadsafe(action.cancel)

    @classmethod
    def cancel_database(cls, database_path):
        database = os.path.normcase(str(database_path.resolve()))
        with cls._lock:
            keys = [key for key in cls._actions if key[0] == database]
        for key in keys:
            cls.cancel(key)
