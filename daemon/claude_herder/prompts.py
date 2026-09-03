"""Reading a blocked session's choices out of its terminal pane.

A fallback, not the preferred route. The `PermissionRequest` hook delivers the
same information structurally and can answer the prompt directly; see the
README. Scraping exists for the dialog families that do not route through that
hook -- plan approval, workspace trust, and the model-fallback pauses.

Everything here degrades to "no options" rather than guessing, because the
caller falls back to a safe action menu whereas a mis-parsed list would put the
wrong answer under the user's thumb. Several shapes are refused outright: a
multi-question checkbox UI is not a pick-one list, and accessibility mode uses
a completely different renderer.
"""

from __future__ import annotations

import re

#: Question lines that open a dialog. Verified present in the installed CLI
#: builds; note that "Do you trust the files in this folder?" is NOT among them
#: -- that wording no longer exists and anchoring on it would never match.
HEADERS = (
    "do you want to proceed?",
    "would you like to proceed?",
    "accessing workspace:",
    "make auto mode your default permission mode?",
    "session paused",
)

#: The focus marker. U+276F normally, ASCII ">" when the terminal is judged
#: non-unicode. Both ship.
POINTERS = "❯>"
#: Scroll affordances occupy the same column but are not options.
SCROLL_MARKS = "↑↓"

FOOTERS = ("esc to cancel", "esc to interrupt", "to update rules",
           "tab to amend", "esc to exit")

#: Shapes we refuse rather than misread.
MULTI_SELECT_MARKS = "☐☒"
A11Y_MARKERS = ("select with numbers [1-", "enter y/n:")

MAX_LABEL = 60
MAX_OPTIONS = 9

_OPTION = re.compile(
    r"^(?P<indent>\s*)(?P<gutter>[" + re.escape(POINTERS + SCROLL_MARKS) + r"])?\s*"
    r"(?P<number>\d)\.\s+(?P<label>.+?)\s*$")

#: Keybinding hints appended to a label, which are decoration rather than text.
_HINT = re.compile(r"\s*\((?:esc|shift\+tab|tab|enter|ctrl\+\w)\)\s*$", re.IGNORECASE)


def _normalise(label: str) -> str:
    """Curly apostrophes and keybinding hints both ship; neither is content."""
    label = label.replace("\u2019", "'")
    label = _HINT.sub("", label)
    return label.strip()[:MAX_LABEL]


def _refused(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in A11Y_MARKERS):
        return True
    return any(mark in text for mark in MULTI_SELECT_MARKS)


def _header_line(lines: list[str]) -> int | None:
    """Index of the LAST dialog header; scrollback can hold answered ones."""
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip().lower()
        for header in HEADERS:
            if stripped.endswith(header):
                return index
    return None


def _collect(text: str) -> list[tuple[str, bool, bool]]:
    """Return (label, highlighted, scrolled) for the current dialog.

    Numbered rows only. A family of dialogs renders with indexes hidden -- the
    workspace-trust and bypass-permissions confirmations among them -- and
    those are deliberately NOT parsed: their rows cannot be told apart from the
    prose above them with any confidence, the focus can start on either row,
    and getting it wrong would answer a security question the wrong way. They
    fall back to the action menu, where "Focus" brings the dialog up to be read
    and answered properly.
    """
    if not text or _refused(text):
        return []

    lines = text.splitlines()
    start = _header_line(lines)
    if start is None:
        return []

    found: list[tuple[str, bool, bool]] = []
    for raw in lines[start + 1:]:
        stripped = raw.strip()
        lowered = stripped.lower()
        if not stripped or any(f in lowered for f in FOOTERS):
            break

        match = _OPTION.match(raw)
        if not match:
            if found:
                break            # the list has ended
            continue             # prose between header and options
        # Numbering must run 1,2,3...: a wrapped line beginning with a digit
        # would otherwise be swallowed as an option.
        if int(match.group("number")) != len(found) + 1:
            break
        label = _normalise(match.group("label"))
        if not label:
            continue
        gutter = match.group("gutter") or " "
        found.append((label, gutter in POINTERS, gutter in SCROLL_MARKS))
        if len(found) >= MAX_OPTIONS:
            break
    return found


def has_dialog(text: str) -> bool:
    """True when the pane is showing a decision the session is blocked on."""
    return len(_collect(text)) >= 2


def parse_options(text: str) -> tuple[str, ...]:
    """The option labels, in menu order. Empty when nothing is parseable."""
    found = _collect(text)
    if len(found) < 2:
        return ()
    return tuple(label for label, _hot, _scroll in found)


def highlighted_index(text: str) -> int | None:
    """Which option the dialog's cursor sits on, or None if it is not visible.

    None means "cannot tell", not "the first one". The caller must refuse to
    act on that rather than guess, since a wrong guess answers the prompt.
    """
    for index, (_label, highlighted, _scroll) in enumerate(_collect(text)):
        if highlighted:
            return index
    return None


def is_partial(text: str) -> bool:
    """True when the list is scrolled, so the visible rows are not all of them."""
    return any(scroll for _label, _hot, scroll in _collect(text))


def looks_blocked(text: str) -> bool:
    """True when ANY dialog appears to be on screen.

    Broader than `parse_options`, which only understands numbered lists. A menu
    action finishes by pressing Enter, and an Enter delivered while an
    unexpected dialog is up answers that dialog instead -- observed live, where
    a workspace-trust prompt swallowed a /compact and confirmed its highlighted
    "No, exit". So actions check this first and refuse.
    """
    if not text:
        return False
    lines = text.splitlines()
    if _header_line(lines) is None:
        return False
    # An anchor alone is not enough; require a row that looks selectable, so a
    # header quoted in ordinary output does not read as a live dialog.
    for raw in lines:
        stripped = raw.strip()
        if stripped[:1] in POINTERS and len(stripped) > 1:
            return True
    return False
