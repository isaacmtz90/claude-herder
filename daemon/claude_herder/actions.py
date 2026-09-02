"""Turning a key press into something happening in the right console.

The invariant this module protects: **never send input to a session we cannot
positively address**. A background job has no terminal, so there is nowhere for
a keystroke to go; sending one anyway would land it in whatever happens to be
focused. Every path therefore checks for a tty first and refuses politely.
"""

from __future__ import annotations

import logging
import time

from .adapters import BgJobAdapter, GhosttyAdapter, ITerm2Adapter
from .config import Config
from .models import ConsoleKind, Session

log = logging.getLogger(__name__)

VIEW_ONLY = "view only - no terminal"

#: Menu labels the router knows how to act on. Anything else is typed as-is if
#: it looks like a slash command, and refused otherwise.
ACTION_FOCUS = "Focus"
ACTION_INTERRUPT = "Interrupt"
ACTION_OUTPUT = "Open output"

#: More rows than any observed dialog offers; a larger index means we misread.
MAX_OPTIONS = 9


class Router:
    """Maps pad keys to adapter calls, choosing the adapter per session."""

    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self._iterm = ITerm2Adapter()
        self._bg = BgJobAdapter()
        self._ghostty: GhosttyAdapter | None = None
        #: Test seam: forces a single adapter for every session.
        self._override = None

    # --- adapter selection ----------------------------------------------------
    def adapter_for(self, session: Session):
        """Choose an adapter from the session's attributed terminal.

        Attribution is positive. Previously anything that was not an iTerm2 tty
        was assumed to be Ghostty, which is wrong for a daemon-hosted worker:
        it owns a real pseudo-terminal that no window displays, and the Ghostty
        path would tag it and then click at a window that does not exist.
        """
        if self._override is not None:
            return self._override
        if session.tty is None or session.terminal is None:
            return self._bg
        if session.terminal == "iterm2":
            return self._iterm
        if session.terminal == "ghostty":
            if self._ghostty is None:
                self._ghostty = GhosttyAdapter()
            return self._ghostty
        log.warning("no adapter for terminal %r; treating as monitor-only",
                    session.terminal)
        return self._bg

    def _reachable(self, session: Session | None) -> bool:
        """Reachable means a window exists AND its kind accepts input."""
        return session is not None and session.controllable

    # --- primitives -----------------------------------------------------------
    def _text(self, session: Session, text: str, submit: bool) -> str:
        if self.dry_run:
            return f"dry run: text {text!r}"
        adapter = self.adapter_for(session)
        if submit:
            # Claude Code's TUI treats an Enter arriving in the same input batch
            # as a newline rather than a submit, so the gap is load-bearing.
            if not adapter.send_text(session.tty, text, submit=False):
                return "send failed"
            time.sleep(self.config.submit_delay)
            return "sent" if adapter.send_key(session.tty, "enter") else "submit failed"
        return "sent" if adapter.send_text(session.tty, text, submit=False) else "send failed"

    def _key(self, session: Session, key: str) -> str:
        if self.dry_run:
            return f"dry run: key {key}"
        adapter = self.adapter_for(session)
        return key if adapter.send_key(session.tty, key) else f"{key} failed"

    # --- public API -----------------------------------------------------------
    def perform(self, session: Session | None, index: int, label: str,
                live: bool, current: int | None = None) -> str:
        """Act on the option the user landed the dial on.

        `live` distinguishes a real prompt the session is blocked on from our
        own action menu. `current` is where the dialog's own cursor sits.
        """
        if session is None:
            return "no session selected"
        if live:
            return self._answer_prompt(session, index, current)
        return self._menu_action(session, label)

    def _answer_prompt(self, session: Session, index: int,
                       current: int | None) -> str:
        """Answer by moving the dialog's cursor, not by typing a number.

        Typing the option number looks simpler and is wrong in general: a whole
        family of dialogs renders with indexes hidden, where the digit is
        silently ignored, and the rows a dialog offers vary with feature flags,
        so a fixed position does not mean a fixed answer. Arrow keys and Enter
        behave identically in every variant.
        """
        if not 0 <= index < MAX_OPTIONS:
            return f"option {index + 1} out of range"
        if not self._reachable(session):
            return VIEW_ONLY
        if current is None:
            # The pointer was not visible, so we do not know where the cursor
            # is. Moving blindly could approve what the user meant to refuse.
            return "cursor unknown; not guessing"
        if self.dry_run:
            return f"dry run: move {index - current:+d} and confirm"

        distance = index - current
        key = "down" if distance > 0 else "up"
        for _ in range(abs(distance)):
            if self._key(session, key).endswith("failed"):
                return "navigation failed"
        return "sent" if self._key(session, "enter") == "enter" else "confirm failed"

    def _menu_action(self, session: Session, label: str) -> str:
        if label == ACTION_OUTPUT:
            return ("opened output" if self._bg.open_output(session.job_id)
                    else "no output found")
        if not self._reachable(session):
            return VIEW_ONLY
        if label == ACTION_FOCUS:
            if self.dry_run:
                return "dry run: focus"
            return "focused" if self.adapter_for(session).focus(session.tty) else "focus failed"
        if label == ACTION_INTERRUPT:
            return self._key(session, "escape")
        if label.startswith("/"):
            # Typed but not submitted: /clear and friends deserve a human Enter.
            return self._text(session, label, submit=False)
        return f"no action for {label!r}"

    def focus(self, session: Session | None) -> bool:
        """Raise the session's terminal window, if it has one."""
        if not self._reachable(session):
            return False
        if self.dry_run:
            return True
        return self.adapter_for(session).focus(session.tty)
