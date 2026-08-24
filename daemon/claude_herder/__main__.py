"""Daemon entry point: watch the fleet, paint the pad, act on key presses."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import fleet, protocol, render
from .actions import Router
from .config import load as load_config
from .device import Pad
from .singleton import InstanceLock
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
        self.router = Router(config, dry_run=dry_run)
        self._running = True

    def stop(self, *_):
        self._running = False

    def selected_session(self):
        for slot in self.slots:
            if slot.index == self.selected and slot.occupied:
                return slot.session
        return None

    def refresh(self) -> None:
        self.slots = fleet.snapshot(self.slots, SLOT_KEYS)
        if self.selected is not None and self.selected_session() is None:
            # Selection pointed at a session that has gone away.
            self.selected = None
        payload = render.slots_payload(self.slots, self.selected)
        if payload != self.last_payload:
            if self.pad.send(protocol.slots_frame(payload)):
                self.last_payload = payload
            log.debug("slots: %s", payload)

    def on_frame(self, frame: dict) -> None:
        kind = frame.get("t")
        if kind == "hello":
            log.info("pad firmware %s connected", frame.get("fw"))
            self.last_payload = None          # force a full repaint
        elif kind == "key":
            self.on_key(int(frame.get("k", -1)))
        elif kind == "enc":
            self.router.scroll(self.selected_session(), int(frame.get("d", 0)))
        elif kind == "encbtn":
            self.router.select_option(self.selected_session())

    def on_key(self, key: int) -> None:
        if 0 <= key < SLOT_KEYS:
            self.selected = key
            session = self.selected_session()
            log.info("selected slot %d: %s", key, session.label if session else "(empty)")
            self.last_payload = None          # selection changed; repaint
            return

        session = self.selected_session()
        if session is None:
            self.pad.send(protocol.toast_frame("no session selected"))
            return
        message = self.router.act(key, session)
        if message:
            self.pad.send(protocol.toast_frame(message))

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
