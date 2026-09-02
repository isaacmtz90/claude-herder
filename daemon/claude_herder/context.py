"""What the OLED's detail area says about the selected session.

Two jobs. First, translate machine states into Claude Code's own voice, because
"busy" and "idle" are accurate and joyless. Second, decide what the encoder can
do right now: when a session is blocked, that is the prompt's real options; the
rest of the time it is a small action menu, so the dial is never inert.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from .models import ConsoleKind, Session, SessionState
from .protocol import context_frame

log = logging.getLogger(__name__)

#: Rotating gerunds for a session that is working, in Claude Code's register.
#: Kept to 12 characters so they fit beside the elapsed time and spinner.
WORKING_WORDS = (
    "Wrangling", "Herding", "Noodling", "Percolating", "Simmering",
    "Cogitating", "Tinkering", "Whirring", "Puttering", "Brewing",
    "Chewing", "Scheming", "Pondering", "Fussing",
)

#: Settled states get one stable word each. Rotating these would suggest
#: activity where there is none.
STABLE_WORDS = {
    SessionState.IDLE: "Ready",
    SessionState.WAITING: "Needs you",
    SessionState.SHELL: "In shell",
    SessionState.ERROR: "Fell over",
    SessionState.UNKNOWN: "Unsure",
}

MAX_SUMMARY = 42          # two 21-column rows
MAX_OPTION = 19           # one row minus the cursor marker

#: Offered when nothing is blocking; keeps the dial useful at all times.
IDLE_ACTIONS = ("Focus", "Interrupt", "/compact", "/clear")
#: A session with no window can only be looked at.
MONITOR_ACTIONS = ("Open output",)


def label_for(state: SessionState, seed: int = 0) -> str:
    """A human label for a state. Only 'working' varies, and only by seed."""
    if state is SessionState.BUSY:
        return WORKING_WORDS[seed % len(WORKING_WORDS)]
    return STABLE_WORDS.get(state, "Unsure")


def format_age(seconds: float | None) -> str:
    """Compact elapsed time. Blank when unknown rather than a misleading zero."""
    if seconds is None:
        return ""
    total = int(max(0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        hours, minutes = divmod(total // 60, 60)
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    return f"{total // 86400}d"


def _actions_for(session: Session) -> tuple[str, ...]:
    if not session.controllable:
        return MONITOR_ACTIONS
    return IDLE_ACTIONS


def build(
    session: Session,
    options_provider: Callable[[Session], Iterable[str]],
    summary_provider: Callable[[Session], str] | None = None,
    now_ms: int = 0,
    seed: int = 0,
) -> dict:
    """Assemble the detail frame for one session."""
    live_options: list[str] = []
    if session.state is SessionState.WAITING:
        try:
            live_options = [str(o)[:MAX_OPTION] for o in options_provider(session)]
        except Exception as exc:
            # Reading options means scraping a terminal, which can fail for
            # reasons that have nothing to do with us. Fall back to the action
            # menu rather than leaving the dial dead.
            log.warning("cannot read options for %s: %s", session.label, exc)
            live_options = []

    summary = ""
    if summary_provider is not None and not live_options:
        try:
            summary = str(summary_provider(session) or "")[:MAX_SUMMARY]
        except Exception as exc:
            log.debug("no summary for %s: %s", session.label, exc)

    options = live_options or list(_actions_for(session))

    age = ""
    if session.status_updated_at and now_ms:
        age = format_age((now_ms - session.status_updated_at) / 1000.0)

    return context_frame(
        state=session.state.value,
        label=label_for(session.state, seed),
        summary=summary,
        age=age,
        options=options,
        live=bool(live_options),
    )
