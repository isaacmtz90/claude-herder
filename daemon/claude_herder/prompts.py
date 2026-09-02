"""Reading a blocked session's choices out of its terminal pane.

The session registry reports *that* a session is waiting and gives a coarse
reason, but the actual choices exist only on screen. So the options are parsed
back out of the pane text.

This is scraping, and it is treated as such: everything here degrades to "no
options" rather than guessing, because the caller falls back to a safe action
menu, whereas a mis-parsed option list would put the wrong answer under the
user's thumb. Verified against CLI 2.1.241, whose dialog reads:

     Do you want to proceed?
     > 1. Yes
       2. No
     Esc to cancel - Tab to amend - ctrl+e to explain
"""

from __future__ import annotations

import re

#: The question that opens every permission dialog seen so far.
HEADERS = (
    "do you want to proceed?",
    "do you trust the files in this folder?",
)

#: Cursor glyphs that mark the highlighted row. U+276F is what 2.1.241 uses.
CURSORS = "❯>▶▸*"

#: Footer text that closes the option list.
FOOTERS = ("esc to cancel", "esc to interrupt", "to update rules")

MAX_LABEL = 60
#: Claude numbers options from 1 and a single digit answers them, so more than
#: nine would not be answerable by digit anyway.
MAX_OPTIONS = 9

_OPTION = re.compile(
    r"^\s*(?P<cursor>[" + re.escape(CURSORS) + r"])?\s*"
    r"(?P<number>\d)\.\s+(?P<label>.+?)\s*$"
)


def _header_line(lines: list[str]) -> int | None:
    """Index of the LAST dialog header; scrollback can hold answered ones."""
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip().lower()
        for header in HEADERS:
            if stripped.endswith(header):
                return index
    return None


def _collect(text: str) -> list[tuple[int, str, bool]]:
    """Return (number, label, highlighted) for the current dialog."""
    lines = text.splitlines()
    start = _header_line(lines)
    if start is None:
        return []

    found: list[tuple[int, str, bool]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped or any(f in lowered for f in FOOTERS):
            break
        match = _OPTION.match(line)
        if not match:
            # Allow blank-ish decoration between header and first option, but
            # once options have started anything else ends the list.
            if found:
                break
            continue
        number = int(match.group("number"))
        # Numbering must run 1,2,3...: a wrapped line that happens to begin
        # with a digit would otherwise be swallowed as an option.
        if number != len(found) + 1:
            break
        found.append((number, match.group("label")[:MAX_LABEL],
                      bool(match.group("cursor"))))
        if len(found) >= MAX_OPTIONS:
            break
    return found


def has_dialog(text: str) -> bool:
    """True when the pane is showing a decision the session is blocked on."""
    return bool(_collect(text))


def parse_options(text: str) -> tuple[str, ...]:
    """The option labels, in menu order. Empty when nothing is parseable."""
    return tuple(label for _number, label, _hot in _collect(text))


def highlighted_index(text: str) -> int | None:
    """Which option the TUI currently has selected, if it is visible.

    Selection may be conveyed by colour alone in some dialogs, and pane text
    carries no colour, so None means "cannot tell" rather than "none".
    """
    for index, (_number, _label, highlighted) in enumerate(_collect(text)):
        if highlighted:
            return index
    return None
