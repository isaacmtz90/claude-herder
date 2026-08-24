"""User configuration, kept out of the code.

Everything version-coupled lives here: the permission menu digits, the canned
key bindings. When Claude Code changes its prompt UI, this is the only file
that should need touching.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(
    os.environ.get("CLAUDE_HERDER_CONFIG")
    or Path.home() / ".claude" / "herder" / "config.toml"
)


@dataclass(frozen=True, slots=True)
class CannedKey:
    """One configurable action key."""

    label: str
    text: str
    #: Whether to press Enter afterwards. Typing without submitting is often
    #: safer, and the right answer differs per key, so it is not a global.
    submit: bool = False


@dataclass(frozen=True, slots=True)
class Config:
    #: Digits sent to Claude Code's permission menu. Version-coupled: verify
    #: against your installed CLI before trusting these.
    approve_key: str = "1"
    deny_key: str = "3"
    #: Verified against this CLI version; recorded so a mismatch is diagnosable.
    verified_against: str = "unverified"

    canned: tuple[CannedKey, ...] = (
        CannedKey("plan mode", "\x1b[Z", submit=False),   # Shift-Tab
        CannedKey("compact", "/compact", submit=True),
        CannedKey("clear", "/clear", submit=False),       # destructive: confirm by hand
    )

    poll_interval: float = 1.0
    heartbeat_interval: float = 1.0
    #: Gap between text and Enter. Claude Code's TUI reads an Enter arriving in
    #: the same input batch as a newline rather than a submit.
    submit_delay: float = 0.5


#: A real instance, because on a slots=True dataclass `Config.field` resolves to
#: a slot descriptor rather than the default value -- which silently produced
#: garbage defaults for any key absent from the file.
_DEFAULTS = Config()


def load(path: Path | None = None) -> Config:
    """Load config, falling back to defaults on any problem."""
    path = path or DEFAULT_PATH
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("ignoring unreadable config %s: %s", path, exc)
        return Config()

    canned = _DEFAULTS.canned
    if isinstance(raw.get("canned"), list):
        parsed = []
        for entry in raw["canned"][:3]:
            if not isinstance(entry, dict) or "text" not in entry:
                continue
            parsed.append(CannedKey(
                label=str(entry.get("label", "key")),
                text=str(entry["text"]),
                submit=bool(entry.get("submit", False)),
            ))
        if parsed:
            canned = tuple(parsed)

    permissions = raw.get("permissions") or {}
    timing = raw.get("timing") or {}
    return Config(
        approve_key=str(permissions.get("approve", _DEFAULTS.approve_key)),
        deny_key=str(permissions.get("deny", _DEFAULTS.deny_key)),
        verified_against=str(
            permissions.get("verified_against", _DEFAULTS.verified_against)),
        canned=canned,
        poll_interval=float(timing.get("poll_interval", _DEFAULTS.poll_interval)),
        heartbeat_interval=float(
            timing.get("heartbeat_interval", _DEFAULTS.heartbeat_interval)),
        submit_delay=float(timing.get("submit_delay", _DEFAULTS.submit_delay)),
    )
