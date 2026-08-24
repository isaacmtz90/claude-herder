"""Read Claude Code's live session registry.

Claude Code writes one JSON file per running session to
`$CLAUDE_CONFIG_DIR/sessions/<pid>.json` and rewrites it on every state
transition. That is the herder's primary state source, chosen over hooks
because hooks only load when a session starts: a hooks-first design would be
blind to every console that was already open when the user installed us.

This is an internal, undocumented file format. Everything here is written to
degrade rather than raise when a field is missing, renamed, or new.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .models import ConsoleKind, Session, SessionState

log = logging.getLogger(__name__)

#: `status` values Claude Code is known to write, as of CLI 2.1.241.
_STATE_BY_STATUS = {
    "busy": SessionState.BUSY,
    "idle": SessionState.IDLE,
    "waiting": SessionState.WAITING,
    "shell": SessionState.SHELL,
}

#: `kind` values, mapped to how we can reach the session.
_KIND_BY_REGISTRY_KIND = {
    "bg": ConsoleKind.BGJOB,
    "interactive": ConsoleKind.INTERACTIVE,
}


def sessions_dir() -> Path:
    """Honour CLAUDE_CONFIG_DIR; never hardcode ~/.claude."""
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "sessions"


def _coerce_state(raw: object) -> SessionState:
    """Map a registry status to a state.

    A missing status is UNKNOWN, never IDLE. Some sessions (VS Code-hosted,
    CLI 2.1.231) carry no status field at all, and showing those as a green
    "available" light would be a confident lie.
    """
    if not isinstance(raw, str):
        return SessionState.UNKNOWN
    state = _STATE_BY_STATUS.get(raw)
    if state is None:
        log.info("unrecognised session status %r; treating as unknown", raw)
        return SessionState.UNKNOWN
    return state


def parse_session_file(path: Path | str) -> Session | None:
    """Parse one registry file, or return None if it is not usable.

    Returns None rather than raising: the directory is written by another
    process and we will routinely catch files mid-write.
    """
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.debug("skipping %s: %s", path.name, exc)
        return None

    if not isinstance(doc, dict):
        return None

    # Identity is the one thing we cannot do without.
    session_id = doc.get("sessionId")
    pid = doc.get("pid")
    if not isinstance(session_id, str) or not session_id or not isinstance(pid, int):
        log.debug("skipping %s: missing sessionId/pid", path.name)
        return None

    return Session(
        session_id=session_id,
        pid=pid,
        proc_start=str(doc.get("procStart") or ""),
        cwd=str(doc.get("cwd") or ""),
        name=str(doc.get("name") or ""),
        state=_coerce_state(doc.get("status")),
        kind=_KIND_BY_REGISTRY_KIND.get(str(doc.get("kind")), ConsoleKind.UNKNOWN),
        entrypoint=str(doc.get("entrypoint") or "cli"),
        waiting_for=doc.get("waitingFor") if isinstance(doc.get("waitingFor"), str) else None,
        job_id=doc.get("jobId") if isinstance(doc.get("jobId"), str) else None,
        updated_at=doc.get("updatedAt") if isinstance(doc.get("updatedAt"), int) else 0,
        spare=bool(doc.get("spare", False)),
    )


def read_all(directory: Path | str | None = None) -> tuple[Session, ...]:
    """Parse every session file in the registry directory.

    A missing directory is normal (no sessions have ever run) and yields ().
    """
    directory = Path(directory) if directory is not None else sessions_dir()
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        log.warning("cannot list %s: %s", directory, exc)
        return ()

    sessions = (parse_session_file(p) for p in paths)
    return tuple(s for s in sessions if s is not None)
