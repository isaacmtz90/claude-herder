"""Immutable value types shared across the daemon.

Every type here is frozen: state transitions produce new objects rather than
mutating existing ones, so a frame already handed to the renderer can never
change underneath it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum


class SessionState(str, Enum):
    """What a session is doing right now.

    Mirrors the `status` enum Claude Code writes into its session registry,
    plus two states the registry never reports: ERROR (an API failure, which
    needs the StopFailure hook) and UNKNOWN.
    """

    BUSY = "busy"
    IDLE = "idle"
    WAITING = "waiting"
    SHELL = "shell"
    ERROR = "error"
    UNKNOWN = "unknown"


class ConsoleKind(str, Enum):
    """How a session is hosted, which decides whether we can control it."""

    INTERACTIVE = "interactive"
    ATTACH = "attach"
    BGJOB = "bgjob"
    UNKNOWN = "unknown"


#: Kinds whose input we can actually reach. A fleet attach client forwards its
#: tty input to the worker's PTY, so it is controllable even though the
#: registry never lists it.
CONTROLLABLE_KINDS = frozenset({ConsoleKind.INTERACTIVE, ConsoleKind.ATTACH})


@dataclass(frozen=True, slots=True)
class Session:
    """One Claude Code session as the herder understands it."""

    session_id: str
    pid: int
    proc_start: str
    cwd: str
    name: str
    state: SessionState
    kind: ConsoleKind
    entrypoint: str = "cli"
    waiting_for: str | None = None
    job_id: str | None = None
    updated_at: int = 0
    #: When the STATUS last changed (epoch ms), which is what "how long
    #: has it been stuck?" actually depends on.
    status_updated_at: int = 0
    tty: str | None = None
    #: Daemon spare-pool worker: real session, but not the user's work.
    spare: bool = False
    #: Which terminal application actually displays this tty ("iterm2",
    #: "ghostty"), or None when nothing does. A daemon-hosted worker owns a real
    #: pseudo-terminal that no window shows, so a tty alone proves nothing.
    terminal: str | None = None

    @property
    def controllable(self) -> bool:
        return (self.kind in CONTROLLABLE_KINDS
                and self.tty is not None
                and self.terminal is not None)

    @property
    def label(self) -> str:
        """Short human label for the 128x64 OLED."""
        return self.name or self.cwd.rsplit("/", 1)[-1] or self.session_id[:8]

    def with_(self, **changes) -> Session:
        """Return a copy with fields replaced. Never mutates."""
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Slot:
    """A key position on the pad, and whatever session currently owns it."""

    index: int
    session: Session | None = None

    @property
    def occupied(self) -> bool:
        return self.session is not None
