"""Pairing a terminal tab with the Claude session it is showing.

Fleet attach consoles are the hard case: the terminal-owning process is not in
the session registry, and the registry entry that has the state belongs to a
worker with no terminal. Neither side knows about the other.

Claude Code closes that gap incidentally -- it sets the terminal title to the
session name (which is why CLAUDE_CODE_DISABLE_TERMINAL_TITLE exists), and
iTerm2 publishes each tab's title. Matching one against the other joins the two
halves using only supported surfaces.

Titles are cosmetic, so matching is deliberately conservative: an ambiguous
title is refused rather than guessed. A wrong join would send an Approve to the
wrong session, which is far worse than showing one slot as unknown.
"""

from __future__ import annotations

import re

#: Trailing "(claude)", "(node)", "(-zsh)" that the terminal appends.
_PROGRAM_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")

#: Leading spinner/state glyphs Claude Code prefixes to the title.
_LEADING_GLYPHS = re.compile(r"^[^\w.]+")

#: How much of a title must survive to be worth matching on.
MIN_MATCH_CHARS = 6


def normalise(title: str) -> str:
    """Reduce a terminal title to the bare session name."""
    if not title:
        return ""
    cleaned = _PROGRAM_SUFFIX.sub("", title)
    cleaned = _LEADING_GLYPHS.sub("", cleaned)
    return cleaned.strip()


def _core(text: str) -> str:
    """Comparison form: lowercased and trimmed."""
    return text.lower().strip()


def match(title: str, names: tuple[str, ...] | list[str]) -> str | None:
    """Find the one session name this title refers to, or None.

    Elision is directional and that matters. A leading ".." means the terminal
    dropped characters from the FRONT, so the visible text is the *end* of the
    real title -- which makes suffix comparison correct and substring
    comparison wrong. Treating it as a substring match made the zsh tab
    "..ments-backend" match the session "payments-backend-77", and a wrong join
    sends Approve to the wrong session.

    Returns None unless exactly one candidate matches.
    """
    cleaned = normalise(title)
    elided = cleaned.startswith("..")
    needle = _core(cleaned.lstrip("."))
    if not needle:
        return None

    candidates = [n for n in names if n and n.strip()]
    if not candidates:
        return None

    exact = [n for n in candidates if _core(n) == needle]
    if len(exact) == 1:
        return exact[0]

    if len(needle) < MIN_MATCH_CHARS:
        return None

    partial = []
    for name in candidates:
        hay = _core(name)
        if elided:
            # Only the tail survived, so the full name must end with it.
            if hay.endswith(needle):
                partial.append(name)
            continue
        if needle in hay or hay in needle:
            partial.append(name)
            continue
        # Tolerate disagreement on the final character only (e.g. a trailing
        # plural the two sources disagree about).
        shared = min(len(hay), len(needle))
        if shared >= MIN_MATCH_CHARS and hay[:shared - 1] == needle[:shared - 1]:
            partial.append(name)

    return partial[0] if len(partial) == 1 else None
