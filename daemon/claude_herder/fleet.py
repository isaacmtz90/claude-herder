"""One place that answers "what should the pad show right now?".

Composition only: every fact comes from a provider that can be swapped out in
tests, so the interesting behaviour (liveness, filtering, stickiness) is
verifiable without a live machine.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Mapping

from . import liveness, processes, registry, slots, titles
from .adapters import ITerm2Adapter
from .models import ConsoleKind, Session, Slot

log = logging.getLogger(__name__)


def attach_journal_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "daemon" / "attach-journal"


def attach_journal_pids(directory: Path | None = None) -> frozenset[int]:
    """PIDs the daemon recorded as fleet attach clients.

    Corroborating evidence only — a console we can see and type into gets a key
    whether or not the daemon wrote a journal entry for it.
    """
    directory = directory or attach_journal_dir()
    pids: set[int] = set()
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        return frozenset()
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("pid"), int):
            pids.add(doc["pid"])
    return frozenset(pids)



#: After a failed title lookup, wait this long before trying again. Under
#: launchd the daemon has a different TCC identity, so the first attempt can sit
#: on an Automation consent dialog until osascript times out. Retrying every
#: poll would stall the whole loop behind that dialog.
TITLE_RETRY_SECONDS = 60.0
_titles_blocked_until = 0.0


def _default_titles() -> Mapping[str, str]:
    """Terminal titles, keyed by tty. Only iTerm2 can report these."""
    global _titles_blocked_until
    if time.monotonic() < _titles_blocked_until:
        return {}
    try:
        titles_by_tty = ITerm2Adapter().session_titles()
    except Exception as exc:            # never let discovery kill the loop
        log.warning("cannot read terminal titles: %s", exc)
        titles_by_tty = {}

    if not titles_by_tty:
        _titles_blocked_until = time.monotonic() + TITLE_RETRY_SECONDS
        log.warning(
            "no terminal titles available; fleet-attached consoles will show "
            "'unknown' state. If macOS is asking to control iTerm2, approve it. "
            "Retrying in %.0fs.", TITLE_RETRY_SECONDS)
    return titles_by_tty


#: Last-logged join set, so a stable pairing is announced once, not every poll.
_LOGGED_JOINS: frozenset = frozenset()


def _join_consoles(
    sessions: tuple[Session, ...],
    tty_by_pid: Mapping[int, str],
    attach_pids: frozenset[int],
    titles_by_tty: Mapping[str, str],
) -> tuple[dict[str, str], list[int]]:
    """Pair each attach console with the session it is displaying.

    Returns (session_id -> tty) for consoles we could identify, plus the pids we
    could not, which become state-unknown entries of their own.

    Only Claude-owned ttys are considered, which is what keeps an ordinary shell
    tab from being joined to a similarly named session.
    """
    names = tuple(s.name for s in sessions if s.name)
    by_name = {s.name: s for s in sessions if s.name}

    claimed: dict[str, str] = {}
    unmatched: list[int] = []

    for pid in sorted(attach_pids):
        tty = tty_by_pid.get(pid)
        if tty is None:
            continue
        matched = titles.match(titles_by_tty.get(tty, ""), names)
        target = by_name.get(matched) if matched else None
        # One console per session: a duplicate title must not steal the pairing.
        if target is not None and target.session_id not in claimed:
            claimed[target.session_id] = tty
        else:
            unmatched.append(pid)
    global _LOGGED_JOINS
    signature = frozenset(claimed.items())
    if signature != _LOGGED_JOINS:
        for session_id, tty in sorted(claimed.items()):
            log.info("console %s shows session %s", tty, session_id[:8])
        _LOGGED_JOINS = signature
    return claimed, unmatched


def snapshot(
    previous: Iterable[Slot] = (),
    capacity: int = slots.DEFAULT_CAPACITY,
    *,
    sessions_provider: Callable[[], Iterable[Session]] = registry.read_all,
    probe_provider: Callable[[], Mapping[int, str] | None] = liveness.probe_proc_starts,
    tty_provider: Callable[[], Mapping[int, str]] = processes.claude_ttys,
    journal_provider: Callable[[], Iterable[int]] = attach_journal_pids,
    cwd_provider: Callable[[Iterable[int]], Mapping[int, str]] = processes.cwds_for,
    title_provider: Callable[[], Mapping[str, str]] = _default_titles,
    owner_provider: Callable[[], Mapping[str, str | None]] = processes.claude_terminal_owners,
    pinned: Mapping[str, int] | None = None,
) -> tuple[Slot, ...]:
    """Build the current slot assignment.

    Order matters: filter to live sessions first, then attribute terminals,
    then discover the attach consoles the registry never mentions.
    """
    sessions = tuple(sessions_provider())

    probe = probe_provider()
    if probe is None:
        # A failed probe means "I don't know", not "everything died". Keep the
        # sessions and let the strike counter decide over successive rounds.
        log.debug("liveness probe unavailable; trusting the registry this round")
        live = sessions
    else:
        live = tuple(s for s in sessions if liveness.is_alive(s.pid, s.proc_start, probe))

    # Attribute each tty to the terminal showing it, and drop the ones no
    # window owns. Daemon-hosted workers get a real pseudo-terminal that nothing
    # displays; treating those as reachable would aim keystrokes at nowhere.
    owners = owner_provider()
    tty_by_pid = {
        pid: tty for pid, tty in tty_provider().items()
        if processes.has_window(tty, owners)
    }
    enriched = processes.with_ttys(live, tty_by_pid, owners)

    attach_pids = processes.attach_client_pids(
        tty_by_pid,
        registry_pids={s.pid for s in sessions},
        journal_pids=journal_provider(),
    )

    # An attach console and the worker it displays are separate processes; the
    # terminal title is what ties them together. A joined session keeps its real
    # state AND gains a terminal, so it becomes both readable and controllable.
    claimed, unmatched = _join_consoles(
        live, tty_by_pid, attach_pids, title_provider() if attach_pids else {})

    enriched = tuple(
        s.with_(tty=claimed[s.session_id], kind=ConsoleKind.ATTACH,
                terminal=owners.get(claimed[s.session_id], "iterm2"))
        if s.session_id in claimed else s
        for s in enriched
    )
    attached = processes.synthesise_attach_sessions(
        tty_by_pid, unmatched, cwd_provider(unmatched), owners)

    return slots.assign(previous, enriched + attached, capacity, pinned=pinned)
