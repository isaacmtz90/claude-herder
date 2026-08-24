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

# Bottom two rows of the pad.
APPROVE_KEY = 6
DENY_KEY = 7
INTERRUPT_KEY = 8
CANNED_KEYS = (9, 10, 11)

#: Built-in bindings a canned key can reference instead of literal text.
BUILTIN_FOCUS = "@focus"
BUILTIN_OUTPUT = "@output"

VIEW_ONLY = "view only - no terminal"


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
        if self._override is not None:
            return self._override
        if session.tty is None:
            return self._bg
        if self._iterm.owns(session.tty):
            return self._iterm
        # Not an iTerm2 session but it has a terminal: assume Ghostty, which
        # cannot enumerate its own tabs.
        if self._ghostty is None:
            self._ghostty = GhosttyAdapter(iterm_ttys=frozenset(self._iterm.session_titles()))
        return self._ghostty

    def _reachable(self, session: Session | None) -> bool:
        return session is not None and session.tty is not None

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
    def act(self, key: int, session: Session | None) -> str:
        if session is None:
            return "no session selected"

        if not self._reachable(session):
            # Background job: the one useful action is showing its output.
            if key == APPROVE_KEY and session.kind is ConsoleKind.BGJOB:
                return ("opened output" if self._bg.open_output(session.job_id)
                        else "no output found")
            return VIEW_ONLY

        if key == APPROVE_KEY:
            return self._answer(session, self.config.approve_key)
        if key == DENY_KEY:
            return self._answer(session, self.config.deny_key)
        if key == INTERRUPT_KEY:
            return self._key(session, "escape")
        if key in CANNED_KEYS:
            return self._canned(CANNED_KEYS.index(key), session)
        return ""

    def _answer(self, session: Session, answer: str) -> str:
        """Send a permission answer, which may be a digit or a named key."""
        if answer.startswith("@"):
            return self._key(session, answer[1:])
        return self._text(session, answer, submit=False)

    def _canned(self, index: int, session: Session) -> str:
        if index >= len(self.config.canned):
            return ""
        binding = self.config.canned[index]
        if binding.text == BUILTIN_FOCUS:
            if self.dry_run:
                return "dry run: focus"
            return "focused" if self.adapter_for(session).focus(session.tty) else "focus failed"
        if binding.text == BUILTIN_OUTPUT:
            return ("opened output" if self._bg.open_output(session.job_id)
                    else "no output found")
        return self._text(session, binding.text, submit=binding.submit)

    def scroll(self, session: Session | None, delta: int) -> str:
        """Encoder rotation moves through whatever options are on screen."""
        if not self._reachable(session) or delta == 0:
            return "" if delta == 0 else VIEW_ONLY
        key = "up" if delta < 0 else "down"
        result = ""
        for _ in range(min(abs(delta), 5)):    # cap a fast spin
            result = self._key(session, key)
        return result

    def select_option(self, session: Session | None) -> str:
        if not self._reachable(session):
            return VIEW_ONLY
        return self._key(session, "enter")
