"""Liveness: a PID alone is not proof a session is alive."""
from claude_herder import liveness


def test_pid_reuse_is_caught_by_proc_start_mismatch():
    # The registry records procStart precisely so a recycled PID belonging to
    # some unrelated process cannot masquerade as a live session.
    probe = {4242: "Mon Aug 24 00:27:05 2026"}
    assert liveness.is_alive(4242, "Mon Aug 24 00:27:05 2026", probe) is True
    assert liveness.is_alive(4242, "Tue Aug 25 09:00:00 2026", probe) is False


def test_absent_pid_is_dead():
    assert liveness.is_alive(999999, "whenever", {}) is False


def test_session_without_recorded_proc_start_falls_back_to_pid_presence():
    assert liveness.is_alive(7, "", {7: "Mon Aug 24 00:27:05 2026"}) is True


class TestStrikes:
    def test_one_absence_is_not_enough_to_evict(self):
        counter = liveness.Strikes()
        counter, evicted = counter.observe(present={"a"}, tracked={"a", "b"})
        assert evicted == frozenset()

    def test_two_consecutive_absences_evict(self):
        counter = liveness.Strikes()
        counter, _ = counter.observe(present={"a"}, tracked={"a", "b"})
        counter, evicted = counter.observe(present={"a"}, tracked={"a", "b"})
        assert evicted == frozenset({"b"})

    def test_reappearing_resets_the_count(self):
        counter = liveness.Strikes()
        counter, _ = counter.observe(present={"a"}, tracked={"a", "b"})
        counter, _ = counter.observe(present={"a", "b"}, tracked={"a", "b"})
        counter, evicted = counter.observe(present={"a"}, tracked={"a", "b"})
        assert evicted == frozenset()

    def test_a_failed_probe_evicts_nothing(self):
        # If we cannot tell who is alive, forgetting every session would blank
        # the whole pad. Absence of evidence is not evidence of death.
        counter = liveness.Strikes()
        counter, evicted = counter.observe(present=None, tracked={"a", "b"})
        counter, evicted = counter.observe(present=None, tracked={"a", "b"})
        assert evicted == frozenset()

    def test_strikes_are_immutable_between_observations(self):
        first = liveness.Strikes()
        second, _ = first.observe(present=set(), tracked={"a"})
        assert first.counts == {}
        assert second.counts != first.counts


class TestTimezoneMismatch:
    """Regression: the registry writes procStart in UTC, `ps` reports lstart in
    local time. Comparing them as strings marks every live session dead."""

    UTC_STAMP = "Mon Aug 24 00:27:05 2026"      # what the registry records
    LOCAL_STAMP = "Sun Aug 23 19:27:05 2026"    # what `ps` reports, CDT (UTC-5)

    def test_utc_registry_stamp_matches_local_ps_stamp(self):
        assert liveness.is_alive(1, self.UTC_STAMP, {1: self.LOCAL_STAMP}) is True

    def test_identical_stamps_still_match(self):
        assert liveness.is_alive(1, self.LOCAL_STAMP, {1: self.LOCAL_STAMP}) is True

    def test_genuinely_different_start_times_are_still_dead(self):
        assert liveness.is_alive(1, self.UTC_STAMP, {1: "Fri Aug 21 03:14:00 2026"}) is False

    def test_a_few_seconds_of_drift_is_tolerated(self):
        assert liveness.is_alive(1, "Mon Aug 24 00:27:06 2026", {1: self.LOCAL_STAMP}) is True

    def test_unparseable_observed_stamp_is_treated_as_alive(self):
        # A format change upstream must not blank the pad.
        assert liveness.is_alive(1, self.UTC_STAMP, {1: "not a date"}) is True

    def test_single_digit_day_padding_is_handled(self):
        assert liveness.is_alive(1, "Tue Aug  4 00:27:05 2026",
                                 {1: "Mon Aug  3 19:27:05 2026"}) is True
