"""Slot assignment: stable positions, honest filtering."""
from claude_herder import slots as S
from claude_herder.models import ConsoleKind, SessionState

CAP = 6


class TestFiltering:
    def test_vscode_sessions_are_excluded(self, make_session):
        # No tty, cannot be keystroked, and they carry no status field either.
        s = make_session(1, entrypoint="claude-vscode")
        assert S.herdable([s]) == ()

    def test_daemon_spares_are_excluded(self, make_session):
        assert S.herdable([make_session(2, kind=ConsoleKind.BGJOB, spare=True)]) == ()

    def test_plain_interactive_session_is_kept(self, make_session):
        s = make_session(3, tty="/dev/ttys015")
        assert S.herdable([s]) == (s,)

    def test_background_job_with_a_job_id_is_kept_for_monitoring(self, make_session):
        s = make_session(4, kind=ConsoleKind.BGJOB, job_id="abc123")
        assert S.herdable([s]) == (s,)

    def test_background_worker_without_a_job_id_is_dropped(self, make_session):
        assert S.herdable([make_session(5, kind=ConsoleKind.BGJOB, job_id=None)]) == ()


class TestStickiness:
    def test_positions_are_stable_across_updates(self, make_session):
        a, b = make_session(1, tty="/dev/ttys1"), make_session(2, tty="/dev/ttys2")
        first = S.assign((), [a, b], CAP)
        again = S.assign(first, [b, a], CAP)  # order reversed on the wire
        assert S.index_of(again, a.session_id) == S.index_of(first, a.session_id)
        assert S.index_of(again, b.session_id) == S.index_of(first, b.session_id)

    def test_a_dead_session_frees_its_slot_without_moving_the_others(self, make_session):
        a, b, c = (make_session(i, tty=f"/dev/ttys{i}") for i in (1, 2, 3))
        first = S.assign((), [a, b, c], CAP)
        assert [S.index_of(first, x.session_id) for x in (a, b, c)] == [0, 1, 2]

        after = S.assign(first, [a, c], CAP)   # b vanished
        assert S.index_of(after, a.session_id) == 0
        assert S.index_of(after, c.session_id) == 2
        assert S.index_of(after, b.session_id) is None

        d = make_session(4, tty="/dev/ttys4")
        refilled = S.assign(after, [a, c, d], CAP)
        assert S.index_of(refilled, d.session_id) == 1, "new session takes the lowest free slot"
        assert S.index_of(refilled, a.session_id) == 0
        assert S.index_of(refilled, c.session_id) == 2

    def test_capacity_is_respected_and_overflow_is_dropped(self, make_session):
        many = [make_session(i, tty=f"/dev/ttys{i}") for i in range(1, 10)]
        result = S.assign((), many, CAP)
        assert len(result) == CAP
        assert sum(1 for s in result if s.occupied) == CAP

    def test_assignment_is_deterministic_for_the_same_input(self, make_session):
        many = [make_session(i, tty=f"/dev/ttys{i}") for i in range(1, 5)]
        assert S.assign((), many, CAP) == S.assign((), list(reversed(many)), CAP)

    def test_returns_exactly_capacity_slots_even_when_empty(self):
        result = S.assign((), [], CAP)
        assert len(result) == CAP and not any(s.occupied for s in result)

    def test_result_is_immutable(self, make_session):
        result = S.assign((), [make_session(1, tty="/dev/ttys1")], CAP)
        assert isinstance(result, tuple)
