"""The detail area: what the selected session is doing and what you can do."""
import pytest
from claude_herder import context
from claude_herder.models import ConsoleKind, SessionState


class TestVocabulary:
    def test_every_state_has_a_label(self):
        for state in SessionState:
            assert context.label_for(state, seed=0), f"no label for {state}"

    def test_labels_avoid_the_raw_enum_words(self):
        # Requirement 6: use Claude's vocabulary, not busy/idle.
        assert context.label_for(SessionState.BUSY, seed=0).lower() != "busy"
        assert context.label_for(SessionState.IDLE, seed=0).lower() != "idle"

    def test_working_labels_vary_so_the_screen_feels_alive(self):
        seen = {context.label_for(SessionState.BUSY, seed=s) for s in range(12)}
        assert len(seen) > 1, "a working session should not always read the same"

    def test_a_given_seed_is_stable(self):
        assert (context.label_for(SessionState.BUSY, seed=3)
                == context.label_for(SessionState.BUSY, seed=3))

    def test_settled_states_do_not_churn(self):
        # Only "working" rotates; a blocked or idle session must read the same
        # every frame or it looks like something is happening when nothing is.
        for state in (SessionState.IDLE, SessionState.WAITING, SessionState.ERROR):
            seen = {context.label_for(state, seed=s) for s in range(8)}
            assert len(seen) == 1, f"{state} should have one stable label"

    def test_labels_fit_the_panel(self):
        for state in SessionState:
            for seed in range(20):
                assert len(context.label_for(state, seed=seed)) <= 12


class TestAge:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0s"), (9, "9s"), (59, "59s"), (60, "1m"), (61, "1m"),
        (3599, "59m"), (3600, "1h"), (7260, "2h1m"), (86400, "1d"),
    ])
    def test_formats_compactly(self, seconds, expected):
        assert context.format_age(seconds) == expected

    def test_negative_clock_skew_is_clamped(self):
        assert context.format_age(-5) == "0s"

    def test_unknown_age_is_blank(self):
        assert context.format_age(None) == ""


class TestBuild:
    def test_a_blocked_session_offers_the_real_prompt_options(self, make_session):
        s = make_session(1, tty="/dev/ttys1", state=SessionState.WAITING,
                         waiting_for="permission prompt")
        frame = context.build(s, options_provider=lambda _s: ["Yes", "No"], now_ms=0)
        assert frame["o"] == ["Yes", "No"]
        assert frame["live"] is True

    def test_a_blocked_session_with_no_readable_options_still_offers_actions(
            self, make_session):
        s = make_session(1, tty="/dev/ttys1", state=SessionState.WAITING)
        frame = context.build(s, options_provider=lambda _s: [], now_ms=0)
        assert frame["o"], "the dial must always have something to do"
        assert frame["live"] is False

    def test_an_idle_session_shows_a_summary_and_an_action_menu(self, make_session):
        s = make_session(1, tty="/dev/ttys1", state=SessionState.IDLE)
        frame = context.build(s, options_provider=lambda _s: [],
                              summary_provider=lambda _s: "wrote 3 files", now_ms=0)
        assert frame["sum"] == "wrote 3 files"
        assert frame["live"] is False
        assert "Focus" in frame["o"]

    def test_a_monitor_only_session_is_not_offered_typing_actions(self, make_session):
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        frame = context.build(bg, options_provider=lambda _s: [], now_ms=0)
        assert "Interrupt" not in frame["o"]
        assert any("output" in o.lower() for o in frame["o"])

    def test_age_comes_from_the_status_change_not_the_last_write(self, make_session):
        s = make_session(1, tty="/dev/ttys1", state=SessionState.BUSY)
        s = s.with_(status_updated_at=1_000_000, updated_at=9_999_999)
        frame = context.build(s, options_provider=lambda _s: [], now_ms=1_060_000)
        assert frame["age"] == "1m"

    def test_a_broken_options_provider_does_not_break_the_frame(self, make_session):
        def boom(_s):
            raise RuntimeError("scrape failed")
        s = make_session(1, tty="/dev/ttys1", state=SessionState.WAITING)
        frame = context.build(s, options_provider=boom, now_ms=0)
        assert frame["o"], "a failed scrape must fall back, not crash the daemon"

    def test_frame_fits_one_protocol_line(self, make_session):
        from claude_herder import protocol
        s = make_session(1, tty="/dev/ttys1", state=SessionState.WAITING,
                         name="a rather long session name here")
        frame = context.build(
            s, options_provider=lambda _s: ["Yes", "Yes, and don't ask again", "No"],
            summary_provider=lambda _s: "x" * 300, now_ms=0)
        assert len(protocol.encode(frame)) < protocol.MAX_LINE_BYTES
