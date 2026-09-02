"""A one-line "what did it last say" for a session.

Shown on the pad while a session is idle, where the option list would otherwise
be. Taken from the transcript rather than the terminal, so it works for
background jobs too -- they have no pane to read.

Transcripts are long (thousands of lines), so only the tail is read.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from .models import Session

log = logging.getLogger(__name__)

#: Two 21-column rows on the OLED.
MAX_SUMMARY = 42
#: Enough tail to contain a recent assistant message without reading the lot.
TAIL_BYTES = 262_144

_MARKDOWN = re.compile(r"[*_`#]+")
_WHITESPACE = re.compile(r"\s+")


def projects_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "projects"


def transcript_for(session_id: str) -> Path | None:
    """Locate a session's transcript.

    Searched by session id rather than by deriving the directory name from the
    cwd: that mangling replaces both "/" and "." with "-" and cannot be
    inverted reliably.
    """
    if not session_id:
        return None
    try:
        for path in projects_dir().glob(f"*/{session_id}.jsonl"):
            return path
    except OSError as exc:
        log.debug("cannot search transcripts: %s", exc)
    return None


def _tidy(text: str) -> str:
    """First line, de-marked-down, collapsed, and cut to fit the panel."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = _MARKDOWN.sub("", first)
    first = _WHITESPACE.sub(" ", first).strip()
    return first[:MAX_SUMMARY]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    # Skip thinking and tool_use: neither reads as "what it said".
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", ""))
    return ""


def last_assistant_text(path: Path | str) -> str:
    """The most recent thing the assistant actually said, or ''."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()          # discard a partial line
            raw = handle.read().decode("utf-8", "replace")
    except OSError as exc:
        log.debug("cannot read %s: %s", path, exc)
        return ""

    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line or '"assistant"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        text = _text_of((entry.get("message") or {}).get("content"))
        if text.strip():
            return _tidy(text)
    return ""


def summarise(session: Session) -> str:
    """One line describing what this session last did."""
    path = transcript_for(session.session_id)
    return last_assistant_text(path) if path else ""
