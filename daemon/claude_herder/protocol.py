"""Newline-delimited JSON wire protocol between daemon and pad.

Deliberately boring: the pad has no parser to speak of, and a wedged device
must never be able to grow the host's buffer without bound.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

#: Longest single frame we will accept. Generous for 6 slots, small enough
#: that a device stuck mid-frame cannot exhaust memory.
MAX_LINE_BYTES = 4096


def encode(frame: dict) -> bytes:
    """Serialise one frame. Compact separators keep the pad's read cheap."""
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def _parse_line(line: bytes) -> dict | None:
    if not line.strip():
        return None
    if len(line) > MAX_LINE_BYTES:
        log.warning("dropping oversized frame (%d bytes)", len(line))
        return None
    try:
        obj = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.warning("dropping malformed frame: %s", exc)
        return None
    if not isinstance(obj, dict):
        log.warning("dropping non-object frame: %r", type(obj).__name__)
        return None
    return obj


def decode(buf: bytes) -> tuple[tuple[dict, ...], bytes]:
    """Split a read buffer into complete frames plus the unconsumed tail.

    Pure: the caller keeps the returned remainder and prepends it to the next
    read. A tail longer than MAX_LINE_BYTES with no newline means the device is
    wedged, so we discard it instead of buffering forever.
    """
    frames: list[dict] = []
    start = 0
    while (nl := buf.find(b"\n", start)) != -1:
        if (obj := _parse_line(buf[start:nl])) is not None:
            frames.append(obj)
        start = nl + 1

    rest = buf[start:]
    if len(rest) > MAX_LINE_BYTES:
        log.warning("discarding %d unterminated bytes; device may be wedged", len(rest))
        rest = b""
    return tuple(frames), rest


# --- host -> pad frame builders ------------------------------------------------

def slots_frame(slots: list[dict]) -> dict:
    return {"t": "slots", "v": slots}


def context_frame(state: str, label: str, summary: str = "", age: str = "",
                  options: list[str] | None = None, live: bool = False) -> dict:
    """Detail for the selected session: what it is doing, and what you can do.

    `options` are what the encoder scrolls. `live` marks them as a real prompt
    the session is blocked on, rather than our own action menu -- the pad shows
    those immediately instead of waiting to be asked.
    """
    frame = {"t": "ctx", "st": state, "label": label, "live": bool(live)}
    if summary:
        frame["sum"] = summary
    if age:
        frame["age"] = age
    if options:
        frame["o"] = [str(o) for o in options]
    return frame


def sel_frame(index: int) -> dict:
    return {"t": "sel", "i": int(index)}


def cursor_frame(index: int) -> dict:
    """Place the pad's highlight, e.g. on the dialog's own current selection."""
    return {"t": "cur", "i": int(index)}


def ping_frame() -> dict:
    """Ask the pad to report what it is showing. Diagnostics only."""
    return {"t": "ping"}


def toast_frame(message: str) -> dict:
    return {"t": "toast", "m": str(message)[:64]}


def heartbeat_frame() -> dict:
    return {"t": "hb"}
