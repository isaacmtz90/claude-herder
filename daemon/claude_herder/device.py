"""The serial link to the pad.

One non-obvious detail drives this module: enabling a second CDC channel makes
the pad enumerate as *two* usbmodem devices. The lower interface is the REPL
console. Connecting to it succeeds, then silently does nothing useful, which
looks exactly like a dead daemon. So port selection is deliberate, and tested.
"""

from __future__ import annotations

import logging
import os
import re
import time

import serial
from serial.tools import list_ports

from . import protocol

log = logging.getLogger(__name__)

MACROPAD_VID = 0x239A
MACROPAD_PID = 0x8108
BAUD = 115200

#: Env override for unusual setups (a hub, a second pad, a renamed device).
PORT_ENV = "CLAUDE_HERDER_PORT"

_TRAILING_DIGITS = re.compile(r"(\d+)$")


def _interface_rank(name: str) -> int:
    match = _TRAILING_DIGITS.search(name)
    return int(match.group(1)) if match else -1


def select_data_port(ports=None) -> str | None:
    """Pick the pad's data interface.

    Returns the highest-numbered matching interface, which is the data channel
    when two are present. With only one interface we return it anyway: failing
    loudly on a console port beats reporting "no pad" when one is attached.
    """
    override = os.environ.get(PORT_ENV)
    if override:
        return override

    ports = list(list_ports.comports()) if ports is None else list(ports)
    matching = [p for p in ports if p.vid == MACROPAD_VID and p.pid == MACROPAD_PID]
    if not matching:
        return None
    if len(matching) == 1:
        log.debug("only one CDC interface on the pad; firmware may lack the data channel")
    best = max(matching, key=lambda p: (_interface_rank(p.device), p.device))
    return best.device


class Pad:
    """Framed, reconnecting link to the pad. Never raises on a missing device."""

    def __init__(self, port: str | None = None, baud: int = BAUD):
        self._explicit_port = port
        self._baud = baud
        self._serial: serial.Serial | None = None
        self._buffer = b""
        self._last_attempt = 0.0
        self._retry_after = 2.0

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self) -> bool:
        """Try to open the pad. Rate-limited so an unplugged pad is cheap."""
        if self.connected:
            return True
        now = time.monotonic()
        if now - self._last_attempt < self._retry_after:
            return False
        self._last_attempt = now

        port = self._explicit_port or select_data_port()
        if port is None:
            return False
        try:
            self._serial = serial.Serial(port, self._baud, timeout=0, write_timeout=1)
        except (OSError, serial.SerialException) as exc:
            log.warning("cannot open pad at %s: %s", port, exc)
            self._serial = None
            return False

        log.info("pad connected on %s", port)
        self._buffer = b""
        return True

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except (OSError, serial.SerialException):
                pass
        self._serial = None

    def _drop(self, reason: str) -> None:
        log.warning("pad link lost: %s", reason)
        self.close()
        self._buffer = b""

    def send(self, frame: dict) -> bool:
        if not self.connect():
            return False
        try:
            self._serial.write(protocol.encode(frame))
            return True
        except (OSError, serial.SerialException) as exc:
            self._drop(str(exc))
            return False

    def poll(self) -> tuple[dict, ...]:
        """Return any complete frames the pad has sent since the last call."""
        if not self.connect():
            return ()
        try:
            waiting = self._serial.in_waiting
            if waiting:
                self._buffer += self._serial.read(waiting)
        except (OSError, serial.SerialException) as exc:
            self._drop(str(exc))
            return ()

        frames, self._buffer = protocol.decode(self._buffer)
        return frames
