"""Common shape for every way of reaching a console."""

from __future__ import annotations

import logging
import subprocess
from typing import Protocol, Sequence

log = logging.getLogger(__name__)

OSASCRIPT_TIMEOUT = 10.0

#: Control sequences, as ASCII code points. Built inside AppleScript from these
#: numbers rather than passed as raw bytes, so nothing depends on how argv
#: encodes control characters.
KEY_CODES: dict[str, tuple[int, ...]] = {
    "escape": (27,),
    "enter": (13,),
    "up": (27, 91, 65),
    "down": (27, 91, 66),
    "right": (27, 91, 67),
    "left": (27, 91, 68),
    "shift_tab": (27, 91, 90),
    "ctrl_c": (3,),
}


class Adapter(Protocol):
    """How to reach one kind of console."""

    name: str

    def can_control(self) -> bool: ...
    def owns(self, tty: str | None) -> bool: ...
    def send_text(self, tty: str, text: str, submit: bool = False) -> bool: ...
    def send_key(self, tty: str, key: str) -> bool: ...
    def focus(self, tty: str) -> bool: ...


def run_osascript(script: str, args: Sequence[str] = ()) -> tuple[bool, str]:
    """Run AppleScript, passing data as argv.

    Text is never interpolated into the script: that would be an injection hole
    and it mangles non-ASCII. The script reads `argv` instead.
    """
    try:
        result = subprocess.run(
            ["osascript", "-", *[str(a) for a in args]],
            input=script, capture_output=True, text=True,
            timeout=OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Usually a permission dialog waiting for a human.
        log.error("osascript timed out; a macOS permission prompt may be open")
        return False, "timeout"
    except OSError as exc:
        log.error("cannot run osascript: %s", exc)
        return False, str(exc)

    if result.returncode != 0:
        message = (result.stderr or "").strip()
        log.warning("osascript failed: %s", message[:200])
        return False, message
    return True, (result.stdout or "").strip()
