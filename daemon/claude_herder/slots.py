"""Which session sits on which key.

Two properties matter more than cleverness here:

* **Sticky.** A session keeps its key for as long as it lives. The whole point
  of hardware is muscle memory, and a pad that reshuffles is worse than no pad.
* **Honest.** A key is only lit for something we can actually say something
  about. Sessions we can neither read nor reach are filtered out rather than
  shown as mystery lights.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .models import ConsoleKind, Session, Slot

#: Every key on the 3x4 pad is a session. There is no action row: all input
#: goes through the rotary encoder, which keeps twelve sessions visible at once
#: rather than six.
DEFAULT_CAPACITY = 12


def _is_herdable(session: Session) -> bool:
    if session.entrypoint == "claude-vscode":
        # stdio/stream-json, no controlling tty, and no status field either.
        return False
    if session.spare:
        # Daemon spare-pool worker; not work the user started.
        return False
    if session.kind is ConsoleKind.BGJOB:
        # Monitor-only, but only worth a key if it is a real named job.
        return bool(session.job_id)
    return session.kind in (ConsoleKind.INTERACTIVE, ConsoleKind.ATTACH)


def herdable(sessions: Iterable[Session]) -> tuple[Session, ...]:
    """Filter to sessions worth putting on a key."""
    return tuple(s for s in sessions if _is_herdable(s))


def index_of(slots: Iterable[Slot], session_id: str) -> int | None:
    for slot in slots:
        if slot.session is not None and slot.session.session_id == session_id:
            return slot.index
    return None


def assign(
    previous: Iterable[Slot],
    sessions: Iterable[Session],
    capacity: int = DEFAULT_CAPACITY,
    pinned: Mapping[str, int] | None = None,
) -> tuple[Slot, ...]:
    """Place sessions onto `capacity` slots, preserving existing positions.

    Precedence, strongest first:

    1. a session already on the pad keeps its key,
    2. `pinned` (a layout remembered from a previous run) reclaims its key,
    3. anything left fills the lowest free key, ordered by session id so the
       result never depends on the order the registry happened to be read in.
    """
    pending = {s.session_id: s for s in herdable(sessions)}

    placed: dict[int, Session] = {}
    for slot in previous:
        if slot.session is None or slot.index >= capacity:
            continue
        existing = pending.pop(slot.session.session_id, None)
        if existing is not None:
            placed[slot.index] = existing  # refreshed state, same key

    # Reclaim remembered keys before handing out fresh ones.
    for session_id, index in sorted((pinned or {}).items()):
        if session_id in pending and 0 <= index < capacity and index not in placed:
            placed[index] = pending.pop(session_id)

    free = (i for i in range(capacity) if i not in placed)
    for session_id in sorted(pending):
        index = next(free, None)
        if index is None:
            break  # pad is full; remaining sessions simply are not shown
        placed[index] = pending[session_id]

    return tuple(Slot(index=i, session=placed.get(i)) for i in range(capacity))
