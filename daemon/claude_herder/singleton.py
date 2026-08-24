"""One daemon at a time.

Two herders on one pad fight over the serial port: writes time out, each side
drops and reconnects, and the pad flickers between two views of the world. That
is easy to cause by accident -- a launchd agent plus a manual run in a terminal
-- so starting is gated on an advisory lock rather than trusted to the operator.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def lock_path() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "herder" / "daemon.lock"


class InstanceLock:
    """Advisory whole-file lock, released when the process exits for any reason."""

    def __init__(self, path: Path | None = None):
        self.path = path or lock_path()
        self._handle = None

    def acquire(self) -> bool:
        """True if we now hold the lock, False if another daemon does."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "w")
        except OSError as exc:
            # Without a lock we would rather run than refuse to start at all.
            log.warning("cannot open lock file %s: %s", self.path, exc)
            return True

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def holder_pid(self) -> int | None:
        """PID recorded by whoever holds the lock, for a useful error message."""
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        except OSError:
            pass
        self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
