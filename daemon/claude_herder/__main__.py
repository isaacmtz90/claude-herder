"""Daemon entry point: watch the fleet, paint the pad, act on key presses."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import context, fleet, prompts, protocol, render, summaries
from .actions import Router
from .config import load as load_config
from .device import Pad
from .singleton import InstanceLock
from .store import layout_of, load_layout, save_layout
from .slots import DEFAULT_CAPACITY

log = logging.getLogger("claude_herder")

SLOT_KEYS = DEFAULT_CAPACITY


class Herder:
    def __init__(self, config, pad: Pad, dry_run: bool = False):
        self.config = config
        self.pad = pad
        self.slots: tuple = ()
        self.selected: int | None = None
        self.last_payload: list | None = None
        self.last_context: dict | None = None
        self.router = Router(config, dry_run=dry_run)
        # Injected so the option and summary sources can be swapped in tests.
        self.options_provider = self._read_options
        self.summary_provider = summaries.summarise
        # Seed from the layout the last run left behind, so a restart does not
        # reshuffle the keys the user has learned.
        self._layout = load_layout()
        self._running = True

    def stop(self, *_):
        self._running = False

    def _read_options(self, session) -> tuple[str, ...]:
        """Read a blocked session's choices off its terminal.

        Only ever called for a session already reported as waiting, so the
        scrape is rare rather than per-poll.
        """
        if session.terminal != "iterm2" or not session.tty:
            return ()
        return prompts.parse_options(self.router._iterm.pane_text(session.tty))

    def selected_session(self):
        for slot in self.slots:
            if slot.index == self.selected and slot.occupied:
                return slot.session
        return None

    def refresh(self) -> None:
        self.slots = fleet.snapshot(self.slots, SLOT_KEYS, pinned=self._layout)
        if self.selected is not None and self.selected_session() is None:
            # Selection pointed at a session that has gone away.
            self.selected = None
        layout = layout_of(self.slots)
        if layout and layout != self._layout:
            self._layout = layout
            save_layout(layout)

        payload = render.slots_payload(self.slots, self.selected)
        if payload != self.last_payload:
            if self.pad.send(protocol.slots_frame(payload)):
                self.last_payload = payload
            log.debug("slots: %s", payload)
        self.push_context()

    def push_context(self) -> None:
        """Send the selected session's detail, when it has changed."""
        session = self.selected_session()
        if session is None:
            if self.last_context is not None:
                self.last_context = None
            return
        frame = context.build(
            session,
            options_provider=self.options_provider,
            summary_provider=self.summary_provider,
            now_ms=int(time.time() * 1000),
            # Stable while a session stays in one state, so the word does not
            # flicker; different per session and per episode, so it varies.
            seed=abs(hash(session.session_id)) + session.status_updated_at // 1000,
        )
        if frame != self.last_context:
            if self.pad.send(frame):
                self.last_context = frame

    def on_frame(self, frame: dict) -> None:
        kind = frame.get("t")
        if kind == "hello":
            log.info("pad firmware %s connected", frame.get("fw"))
            self.last_payload = None          # force a full repaint
        elif kind == "key":
            self.on_key(int(frame.get("k", -1)))
        elif kind == "pick":
            self.on_pick(int(frame.get("i", -1)), str(frame.get("l", "")))
        elif kind in ("enc", "encbtn"):
            # The pad drives its own cursor so the dial feels instant; these are
            # informational only.
            log.debug("encoder: %s", frame)

    def on_key(self, key: int) -> None:
        if 0 <= key < SLOT_KEYS:
            self.selected = key
            session = self.selected_session()
            log.info("selected slot %d: %s", key, session.label if session else "(empty)")
            self.last_payload = None          # selection changed; repaint
            # Raising the window makes the pad and the screen agree on what you
            # are looking at. Skipped for anything without a window to raise.
            if self.config.focus_on_tap and session is not None and session.controllable:
                self.router.focus(session)
            return

        log.debug("key %d is not a slot", key)

    def on_pick(self, index: int, label: str) -> None:
        session = self.selected_session()
        if session is None:
            self.pad.send(protocol.toast_frame("no session selected"))
            return
        live = bool((self.last_context or {}).get("live"))
        message = self.router.perform(session, index, label, live)
        log.info("picked %r on %s -> %s", label, session.label, message)
        if message:
            self.pad.send(protocol.toast_frame(message))
        # State will have moved on; force a fresh detail frame.
        self.last_context = None

    def run(self) -> int:
        next_refresh = 0.0
        next_beat = 0.0
        while self._running:
            now = time.monotonic()
            if now >= next_refresh:
                self.refresh()
                next_refresh = now + self.config.poll_interval
            if now >= next_beat:
                self.pad.send(protocol.heartbeat_frame())
                next_beat = now + self.config.heartbeat_interval
            for frame in self.pad.poll():
                self.on_frame(frame)
            time.sleep(0.02)
        self.pad.close()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-herder", description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--port", help="serial port override")
    parser.add_argument("--dry-run", action="store_true",
                        help="log actions instead of sending them to a terminal")
    parser.add_argument("--once", action="store_true",
                        help="print one snapshot and exit (no pad required)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = load_config()

    if args.once:
        slots = fleet.snapshot((), SLOT_KEYS)
        for entry in render.slots_payload(slots, selected=None):
            print(entry)
        return 0

    lock = InstanceLock()
    if not lock.acquire():
        holder = lock.holder_pid()
        log.error("another herder is already running%s. Two daemons fight over "
                  "the pad's serial port; refusing to start.",
                  f" (pid {holder})" if holder else "")
        return 1

    pad = Pad(port=args.port)
    if not pad.connect():
        log.error("no MacroPad found. Is it plugged in? "
                  "Set %s to override port detection.", "CLAUDE_HERDER_PORT")
        return 1

    herder = Herder(config, pad, dry_run=args.dry_run)
    signal.signal(signal.SIGINT, herder.stop)
    signal.signal(signal.SIGTERM, herder.stop)
    log.info("herding %d slots", SLOT_KEYS)
    try:
        return herder.run()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
