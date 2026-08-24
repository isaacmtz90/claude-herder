"""Composing registry + process table into what the pad should show."""
import json
import pytest
from claude_herder import fleet
from claude_herder.models import ConsoleKind, SessionState


def registry_doc(pid, **over):
    doc = {"pid": pid, "sessionId": f"sid-{pid}", "cwd": "/Users/x/Repo",
           "procStart": "Mon Aug 24 00:27:05 2026", "kind": "interactive",
           "entrypoint": "cli", "name": f"job-{pid}", "status": "idle"}
    doc.update(over)
    return doc


@pytest.fixture
def world(tmp_path):
    """A fake machine: registry dir plus injected process-table answers."""
    def _build(docs, *, ttys=None, journal=(), probe=None, cwds=None):
        for doc in docs:
            (tmp_path / f"{doc['pid']}.json").write_text(json.dumps(doc))
        pids = [d["pid"] for d in docs] + list((ttys or {}).keys())
        return dict(
            sessions_dir=tmp_path,
            probe=probe if probe is not None
                  else {p: "Sun Aug 23 19:27:05 2026" for p in pids},
            ttys=ttys or {},
            journal_pids=frozenset(journal),
            cwds=cwds or {},
        )
    return _build


def snap(w, previous=(), capacity=6):
    return fleet.snapshot(
        previous, capacity,
        sessions_provider=lambda: fleet.registry.read_all(w["sessions_dir"]),
        probe_provider=lambda: w["probe"],
        tty_provider=lambda: w["ttys"],
        journal_provider=lambda: w["journal_pids"],
        cwd_provider=lambda pids: w["cwds"],
    )


def test_interactive_session_with_a_tty_is_controllable(world):
    w = world([registry_doc(100)], ttys={100: "/dev/ttys1"})
    slots = snap(w)
    session = slots[0].session
    assert session.tty == "/dev/ttys1" and session.controllable


def test_attach_console_appears_alongside_registry_sessions(world):
    # 200 owns a tty but is absent from the registry: a fleet attach client.
    w = world([registry_doc(100, kind="bg", jobId="abc")],
              ttys={200: "/dev/ttys2"}, journal=[200], cwds={200: "/Users/x/Project"})
    slots = snap(w)
    kinds = {s.session.kind for s in slots if s.occupied}
    assert ConsoleKind.ATTACH in kinds and ConsoleKind.BGJOB in kinds


def test_attach_console_state_is_unknown_never_invented(world):
    w = world([], ttys={200: "/dev/ttys2"}, journal=[200])
    attach = next(s.session for s in snap(w) if s.occupied)
    assert attach.state is SessionState.UNKNOWN


def test_dead_session_is_dropped_by_the_liveness_probe(world):
    w = world([registry_doc(100)], ttys={}, probe={})  # pid not running
    assert not any(s.occupied for s in snap(w))


def test_failed_probe_keeps_showing_what_we_last_knew(world):
    w = world([registry_doc(100)], ttys={100: "/dev/ttys1"}, probe=None)
    w["probe"] = None
    slots = snap(w)
    assert any(s.occupied for s in slots), "a broken ps must not blank the pad"


def test_positions_survive_a_refresh(world):
    w = world([registry_doc(100), registry_doc(101)], ttys={100: "/dev/ttys1"})
    first = snap(w)
    second = snap(w, previous=first)
    assert [s.session.session_id if s.occupied else None for s in first] == \
           [s.session.session_id if s.occupied else None for s in second]


def test_snapshot_always_returns_capacity_slots(world):
    assert len(snap(world([]), capacity=6)) == 6


class TestTitleJoin:
    """A fleet attach console and the worker it displays are different
    processes. Pairing them by terminal title is what lets an attach console
    show real state instead of "unknown"."""

    def _snap(self, w, titles):
        return fleet.snapshot(
            (), 6,
            sessions_provider=lambda: fleet.registry.read_all(w["sessions_dir"]),
            probe_provider=lambda: w["probe"],
            tty_provider=lambda: w["ttys"],
            journal_provider=lambda: w["journal_pids"],
            cwd_provider=lambda pids: w["cwds"],
            title_provider=lambda: titles,
        )

    def test_matching_title_gives_the_worker_its_terminal(self, world):
        # pid 900 is a tty-less worker that is WAITING; pid 901 is the attach
        # client displaying it. The pad must show "waiting" and allow control.
        w = world(
            [registry_doc(900, kind="bg", jobId="j1", name="fix the parser",
                          status="waiting", waitingFor="permission prompt")],
            ttys={901: "/dev/ttys9"}, journal=[901])
        slots = self._snap(w, {"/dev/ttys9": "◐ fix the parser (claude)"})
        occupied = [s.session for s in slots if s.occupied]
        assert len(occupied) == 1, "the worker and its console must not both appear"
        session = occupied[0]
        assert session.state is SessionState.WAITING
        assert session.waiting_for == "permission prompt"
        assert session.tty == "/dev/ttys9"
        assert session.controllable is True

    def test_unmatched_console_still_appears_but_state_is_unknown(self, world):
        w = world([registry_doc(900, kind="bg", jobId="j1", name="fix the parser")],
                  ttys={901: "/dev/ttys9"}, journal=[901])
        slots = self._snap(w, {"/dev/ttys9": "something else entirely (claude)"})
        states = {s.session.state for s in slots if s.occupied}
        assert SessionState.UNKNOWN in states

    def test_a_shell_tab_title_never_captures_a_session(self, world):
        # Regression: "..ments-backend" must not be joined to "payments-backend-77".
        w = world([registry_doc(900, kind="bg", jobId="j1", name="payments-backend-77")],
                  ttys={901: "/dev/ttys9"}, journal=[901])
        slots = self._snap(w, {"/dev/ttys9": "..ments-backend (-zsh)"})
        joined = [s.session for s in slots if s.occupied and s.session.tty]
        assert all(s.name != "payments-backend-77" for s in joined)

    def test_two_consoles_cannot_claim_the_same_worker(self, world):
        w = world([registry_doc(900, kind="bg", jobId="j1", name="fix the parser")],
                  ttys={901: "/dev/ttys9", 902: "/dev/ttys8"}, journal=[901, 902])
        title = "fix the parser (claude)"
        slots = self._snap(w, {"/dev/ttys9": title, "/dev/ttys8": title})
        ttys = [s.session.tty for s in slots if s.occupied and s.session.tty]
        assert len(ttys) == len(set(ttys)), "each console maps to at most one session"
