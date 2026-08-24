"""Turn slots into the compact payload the pad renders.

Pure functions only: no I/O, no device, no clock. That keeps the interesting
part of the display logic testable without hardware.
"""

from __future__ import annotations

from typing import Iterable

from .models import ConsoleKind, SessionState, Slot

#: The OLED is 128x64 with a 6px font; longer labels are unreadable anyway.
MAX_LABEL_CHARS = 18
MAX_REASON_CHARS = 20

_STATE_CODE = {
    SessionState.BUSY: "busy",
    SessionState.IDLE: "idle",
    SessionState.WAITING: "waiting",
    SessionState.SHELL: "shell",
    SessionState.ERROR: "error",
    SessionState.UNKNOWN: "unknown",
}

_KIND_CODE = {
    ConsoleKind.INTERACTIVE: "tty",
    ConsoleKind.ATTACH: "attach",
    ConsoleKind.BGJOB: "bg",
    ConsoleKind.UNKNOWN: "unk",
}

EMPTY = "empty"


def slots_payload(slots: Iterable[Slot], selected: int | None) -> list[dict]:
    """Build one entry per slot, smallest representation that stays readable."""
    payload: list[dict] = []
    for slot in slots:
        entry: dict = {"i": slot.index}
        session = slot.session

        if session is None:
            entry["st"] = EMPTY
        else:
            entry["st"] = _STATE_CODE.get(session.state, "unknown")
            entry["nm"] = session.label[:MAX_LABEL_CHARS]
            entry["k"] = _KIND_CODE.get(session.kind, "unk")
            entry["ctl"] = bool(session.controllable)
            if session.waiting_for:
                entry["wf"] = session.waiting_for[:MAX_REASON_CHARS]

        if selected is not None and slot.index == selected:
            entry["sel"] = True

        payload.append(entry)
    return payload
