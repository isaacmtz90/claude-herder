"""Working out which terminal a session lives in.

The registry tells us what every session is *doing* but not where it is. That
mapping has to come from the process table, and it is less obvious than it
looks — see `attach_client_pids` for why "interactive sessions have a tty" is
the wrong rule on a machine using remote control.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Iterable, Mapping

from .models import ConsoleKind, Session, SessionState

log = logging.getLogger(__name__)

#: Background workers report their CLI version as their command name rather
#: than "claude", so match both shapes before deciding a tty is not ours.
_VERSION_COMM = re.compile(r"^\d+\.\d+\.\d+$")

_NO_TTY = {"??", "?", "-", ""}


def _is_claude_comm(comm: str) -> bool:
    basename = comm.rsplit("/", 1)[-1]
    return basename == "claude" or bool(_VERSION_COMM.match(basename))


def parse_ps_tty(text: str) -> dict[int, str]:
    """Parse `ps -Ao pid=,tty=,comm=` into {pid: /dev/ttysNNN}.

    Only Claude processes that actually own a terminal are returned.
    """
    found: dict[int, str] = {}
    for line in text.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        pid_text, tty, comm = fields
        if tty in _NO_TTY or not _is_claude_comm(comm.strip()):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        found[pid] = tty if tty.startswith("/dev/") else f"/dev/{tty}"
    return found


def claude_ttys() -> dict[int, str]:
    """One `ps` call for the whole process table."""
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,tty=,comm="],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("cannot read process table: %s", exc)
        return {}
    return parse_ps_tty(out)


def attach_client_pids(
    tty_by_pid: Mapping[int, str],
    registry_pids: Iterable[int],
    journal_pids: Iterable[int],
) -> frozenset[int]:
    """Identify fleet attach clients.

    With remote control enabled, typing `claude` often attaches the terminal to
    a daemon-hosted background worker instead of starting a local session. The
    terminal-owning process is then an attach *client* that the registry never
    lists, while the registry entry that has the status field belongs to a
    worker with no tty at all.

    `journal_pids` (from ~/.claude/daemon/attach-journal) corroborates but is
    not required: a console we can see and type into earns a key regardless.
    """
    known = set(registry_pids)
    journalled = set(journal_pids)
    candidates = {pid for pid in tty_by_pid if pid not in known}
    for pid in candidates - journalled:
        log.debug("pid %s owns a tty, is not in the registry, and has no journal "
                  "entry; treating as an attach client anyway", pid)
    return frozenset(candidates)


def with_ttys(
    sessions: Iterable[Session],
    tty_by_pid: Mapping[int, str],
    owners: Mapping[str, str | None] | None = None,
) -> tuple[Session, ...]:
    """Attach each session's terminal, and which app displays it.

    tty and terminal are set together on purpose: a tty alone does not mean a
    human can see the session, so letting them drift apart is what allowed a
    windowless daemon pty to look controllable.
    """
    owners = owners if owners is not None else {}
    attached = []
    for session in sessions:
        tty = tty_by_pid.get(session.pid)
        if tty is None:
            attached.append(session)
            continue
        attached.append(session.with_(tty=tty, terminal=owners.get(tty, "iterm2")))
    return tuple(attached)


def cwds_for(pids: Iterable[int]) -> dict[int, str]:
    """Look up working directories for a handful of pids via lsof."""
    pids = [str(p) for p in pids]
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("cwd lookup failed: %s", exc)
        return {}

    found: dict[int, str] = {}
    current: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                current = int(line[1:])
            except ValueError:
                current = None
        elif line.startswith("n") and current is not None:
            found.setdefault(current, line[1:])
    return found


def synthesise_attach_sessions(
    tty_by_pid: Mapping[int, str],
    attach_pids: Iterable[int],
    cwd_by_pid: Mapping[int, str] | None = None,
    owners: Mapping[str, str | None] | None = None,
) -> tuple[Session, ...]:
    """Build Session records for attach consoles.

    These are controllable — the attach client forwards its tty input to the
    worker's PTY — but their state is genuinely UNKNOWN until we can pair the
    console with its worker. Reporting a state we cannot observe would be worse
    than admitting we do not know.
    """
    cwd_by_pid = cwd_by_pid or {}
    owners = owners if owners is not None else {}
    made = []
    for pid in sorted(attach_pids):
        tty = tty_by_pid.get(pid)
        if tty is None:
            continue
        cwd = cwd_by_pid.get(pid, "")
        made.append(Session(
            # Keyed on tty, not pid: stable for the life of the terminal, so a
            # reconnecting client does not jump to a different key.
            session_id=f"attach:{tty}",
            pid=pid,
            proc_start="",
            cwd=cwd,
            name="",
            state=SessionState.UNKNOWN,
            kind=ConsoleKind.ATTACH,
            tty=tty,
            terminal=owners.get(tty, "iterm2"),
        ))
    return tuple(made)


#: Terminal applications we know how to drive, matched against process names in
#: a tty's ancestry. The value is the adapter name that owns them.
TERMINAL_APPS = {
    "iterm2": "iterm2",
    "iterm": "iterm2",
    "itermserver": "iterm2",
    "ghostty": "ghostty",
}

#: How far up the process tree to look before giving up.
MAX_ANCESTRY = 12


def _parse_tree(text: str) -> tuple[dict[int, int], dict[int, str]]:
    parent: dict[int, int] = {}
    name: dict[int, str] = {}
    for line in text.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        parent[pid] = ppid
        name[pid] = fields[2].strip().lstrip("-").rsplit("/", 1)[-1]
    return parent, name


def terminal_owners(ps_tty: str, ps_tree: str) -> dict[str, str | None]:
    """Attribute each Claude-owned tty to the terminal app displaying it.

    Positive attribution, deliberately. A daemon-hosted worker has a real
    pseudo-terminal that no window shows, so "not iTerm2, therefore Ghostty"
    would aim keystrokes at a window that does not exist. A tty whose ancestry
    contains no terminal we recognise maps to None.
    """
    parent, name = _parse_tree(ps_tree)
    owners: dict[str, str | None] = {}

    for pid, tty in parse_ps_tty(ps_tty).items():
        owner = None
        current, seen = pid, set()
        for _ in range(MAX_ANCESTRY):
            if current in seen:      # defensive: a cycle would loop forever
                break
            seen.add(current)
            # Prefix match: process names carry versions ("iTermServer-3.6.6").
            proc = name.get(current, "").lower()
            candidate = None
            for prefix, adapter in TERMINAL_APPS.items():
                if proc.startswith(prefix):
                    candidate = adapter
                    break
            if candidate:
                owner = candidate
                break
            nxt = parent.get(current)
            if nxt is None or nxt == current or nxt <= 1:
                break
            current = nxt
        owners[tty] = owner
    return owners


def has_window(tty: str | None, owners: Mapping[str, str | None]) -> bool:
    """True only if some terminal application is actually showing this tty."""
    if tty is None:
        return False
    return owners.get(tty) is not None


def process_tree() -> str:
    """Raw `ps` output for ancestry walking."""
    try:
        return subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("cannot read process tree: %s", exc)
        return ""


def claude_terminal_owners() -> dict[str, str | None]:
    """Live attribution for every Claude-owned tty."""
    try:
        tty_text = subprocess.run(
            ["ps", "-Ao", "pid=,tty=,comm="],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("cannot read process table: %s", exc)
        return {}
    return terminal_owners(tty_text, process_tree())
