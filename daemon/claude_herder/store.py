"""Remembering which session sat on which key.

Stickiness within one run comes from passing the previous slots back in, but the
point of hardware is muscle memory, and a daemon restart would otherwise
re-sort everything. So the layout is persisted and used to seed the next run.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Mapping

from .models import Slot

log = logging.getLogger(__name__)


def layout_path() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "herder" / "slots.json"


def layout_of(slots: Iterable[Slot]) -> dict[str, int]:
    """Extract {session_id: key index} from an assignment."""
    return {
        slot.session.session_id: slot.index
        for slot in slots
        if slot.session is not None
    }


def load_layout(path: Path | None = None) -> dict[str, int]:
    """Read a saved layout. Any problem yields an empty layout, never an error."""
    path = path or layout_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable slot layout %s: %s", path, exc)
        return {}

    if not isinstance(doc, dict):
        return {}
    return {
        str(key): value
        for key, value in doc.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def save_layout(layout: Mapping[str, int], path: Path | None = None) -> None:
    """Persist the layout. Failing to save is a nuisance, not a stop condition."""
    path = path or layout_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash cannot leave a half-written layout.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(layout, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        log.warning("cannot save slot layout to %s: %s", path, exc)
