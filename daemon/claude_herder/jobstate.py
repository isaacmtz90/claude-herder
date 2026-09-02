"""A job's own state file: the same data the fleet view renders from.

`~/.claude/jobs/<short-id>/state.json` is written by the daemon on every state
change and already contains a one-line summary, so there is no need to derive
one. Which field to use depends on the job's state, and that precedence is
taken from the CLI's own row renderer:

    finished  -> output.result, falling back to detail
    blocked   -> needs            e.g. "approve Bash: echo hello"
    working   -> detail, unless detail is a placeholder sentinel

`needs` is the useful one for a small screen: it is already a compact
description of the question, written by the permission bridge.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

#: Placeholder `detail` values that mean "nothing to say yet".
SENTINELS = (
    "starting…",
    "send a prompt to start",
    "(idle — send a prompt to start)",
)

#: `needs` set to this is not a real question.
NOT_A_QUESTION = "send a prompt to start"

TERMINAL_STATES = {"done", "failed", "stopped"}

_REMINDER = re.compile(
    r"<(system-reminder|task-notification)>[\s\S]*?(</\1>|$)", re.IGNORECASE)
_TAG = re.compile(r"</?[\w-]+>")
_SPACE = re.compile(r"\s+")


def jobs_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "jobs"


def clean(text: str | None) -> str:
    """Strip the markup Claude embeds in its own status text."""
    if not text:
        return ""
    text = _REMINDER.sub(" ", text)
    text = _TAG.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def read(job_id: str | None) -> dict:
    """Load a job's state, or {} if it is missing or unreadable."""
    if not job_id:
        return {}
    path = jobs_dir() / job_id / "state.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.debug("no job state for %s: %s", job_id, exc)
        return {}
    return doc if isinstance(doc, dict) else {}


def summarise(state: dict) -> str:
    """The one-line summary the fleet view would show for this job."""
    detail = state.get("detail") or ""
    needs = state.get("needs") or ""

    if state.get("state") in TERMINAL_STATES and state.get("tempo") != "active":
        result = (state.get("output") or {}).get("result")
        return clean(result or detail)

    if state.get("tempo") == "blocked" and needs and needs != NOT_A_QUESTION:
        return clean(needs)

    if detail and detail not in SENTINELS:
        return clean(detail)

    return ""


def summary_for(job_id: str | None) -> str:
    return summarise(read(job_id))


def question_options(job_id: str | None) -> tuple[str, ...]:
    """Options for a pending AskUserQuestion, structurally rather than scraped."""
    block = read(job_id).get("block") or {}
    questions = block.get("questions")
    if not isinstance(questions, list) or not questions:
        return ()
    options = questions[0].get("options") if isinstance(questions[0], dict) else None
    if not isinstance(options, list):
        return ()

    labels = []
    for option in options:
        if isinstance(option, str):
            labels.append(option)
        elif isinstance(option, dict):
            label = option.get("label") or option.get("displayLabel")
            if label:
                labels.append(str(label))
    return tuple(labels)
