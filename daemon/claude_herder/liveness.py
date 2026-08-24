"""Deciding whether a session is still alive.

Two traps this module exists to avoid:

1. A PID on its own proves nothing. PIDs get recycled, and the registry
   records `procStart` precisely so we can tell a live session from an
   unrelated process that inherited its number.
2. A single missed observation is not death. Registry files are written by
   another process and we routinely race it, so eviction needs corroboration.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Iterable, Mapping

log = logging.getLogger(__name__)

#: Consecutive absences before a slot is freed.
STRIKE_LIMIT = 2

_WS = re.compile(r"\s+")


def _normalise(stamp: str) -> str:
    return _WS.sub(" ", stamp).strip()


def probe_proc_starts() -> dict[int, str] | None:
    """Map every live PID to its start time in one `ps` call.

    Returns None if the probe itself failed, which callers must treat as "I
    don't know" rather than "everything died".
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,lstart="],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("liveness probe failed: %s", exc)
        return None

    starts: dict[int, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, rest = line.partition(" ")
        try:
            starts[int(pid_text)] = _normalise(rest)
        except ValueError:
            continue
    return starts


#: Process start times are second-granular on both sides, so allow a little slack.
STAMP_TOLERANCE_SECONDS = 3.0

_STAMP_FORMAT = "%a %b %d %H:%M:%S %Y"


def _parse_stamp(text: str, tz: timezone | None) -> float | None:
    """Parse a `ps`-style timestamp into an epoch, or None if unrecognised."""
    try:
        naive = datetime.strptime(_normalise(text), _STAMP_FORMAT)
    except (ValueError, TypeError):
        return None
    return (naive.replace(tzinfo=tz) if tz else naive).timestamp()


def _same_instant(recorded: str, observed: str) -> bool:
    """Compare a registry `procStart` with a `ps` `lstart`.

    These do not agree on timezone: Claude Code writes procStart in UTC while
    `ps` prints lstart in local time, so on a UTC-5 machine the same process
    reads five hours apart. Rather than hardcode that relationship (and break
    if it ever changes), accept the recorded stamp under either reading. A
    genuinely recycled PID differs by far more than any timezone offset, so
    this keeps PID-reuse detection intact.
    """
    observed_epoch = _parse_stamp(observed, None)  # `ps` prints local time
    if observed_epoch is None:
        # Unrecognised format upstream: do not condemn a running process.
        log.debug("unparseable observed start time %r; assuming alive", observed)
        return True

    for tz in (timezone.utc, None):
        recorded_epoch = _parse_stamp(recorded, tz)
        if recorded_epoch is None:
            continue
        if abs(recorded_epoch - observed_epoch) <= STAMP_TOLERANCE_SECONDS:
            return True
    return False


def is_alive(pid: int, proc_start: str, probe: Mapping[int, str]) -> bool:
    """True if `pid` is running and looks like the same process we recorded."""
    observed = probe.get(pid)
    if observed is None:
        return False
    if not proc_start:
        # Nothing recorded to compare against; presence is the best we have.
        return True
    return _same_instant(proc_start, observed)


@dataclass(frozen=True, slots=True)
class Strikes:
    """Absence counter. Immutable: `observe` returns a new instance."""

    counts: Mapping[str, int] = field(default_factory=dict)

    def observe(
        self, present: Iterable[str] | None, tracked: Iterable[str]
    ) -> tuple["Strikes", frozenset[str]]:
        """Record one round of observation.

        `present=None` means the probe failed. In that case nothing is counted
        and nothing is evicted — blanking the whole pad because `ps` hiccuped
        would be worse than showing slightly stale state.
        """
        if present is None:
            return self, frozenset()

        present_set = set(present)
        next_counts: dict[str, int] = {}
        evicted: set[str] = set()

        for key in tracked:
            if key in present_set:
                continue  # seen: strike count drops away entirely
            count = self.counts.get(key, 0) + 1
            if count >= STRIKE_LIMIT:
                evicted.add(key)
            else:
                next_counts[key] = count

        return Strikes(next_counts), frozenset(evicted)
