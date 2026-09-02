"""Key -> action dispatch. The safety-critical part of the daemon."""
import pytest
from claude_herder.actions import Router
from claude_herder.config import CannedKey, Config
from claude_herder.models import ConsoleKind, SessionState


class FakeAdapter:
    name = "fake"

    def __init__(self, controllable=True):
        self._controllable = controllable
        self.texts = []
        self.keys = []
        self.focused = []

    def can_control(self): return self._controllable
    def owns(self, tty): return tty is not None
    def send_text(self, tty, text, submit=False):
        self.texts.append((tty, text, submit)); return True
    def send_key(self, tty, key):
        self.keys.append((tty, key)); return True
    def focus(self, tty):
        self.focused.append(tty); return True


@pytest.fixture
def router():
    def _make(adapter=None, config=None):
        r = Router(config or Config(), dry_run=False)
        r._override = adapter or FakeAdapter()
        return r
    return _make


class TestPickingALivePromptOption:
    """Answering a prompt by moving the TUI's own cursor.

    Not by typing the option number: a whole family of dialogs renders with
    indexes hidden, where a digit is silently a no-op, and option order varies
    with feature flags so "2" does not reliably mean the same row twice.
    Arrow keys and Enter work in every variant.
    """

    def test_moving_down_to_a_later_option(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=2, label="No", live=True, current=0)
        assert [k for _t, k in adapter.keys] == ["down", "down", "enter"]

    def test_moving_up_to_an_earlier_option(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=0, label="Yes", live=True, current=2)
        assert [k for _t, k in adapter.keys] == ["up", "up", "enter"]

    def test_already_on_the_option_just_confirms(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=1, label="No", live=True, current=1)
        assert [k for _t, k in adapter.keys] == ["enter"]

    def test_nothing_is_typed(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=1, label="No", live=True, current=0)
        assert adapter.texts == [], "digits are unreliable; never type them"

    def test_an_unknown_cursor_refuses_rather_than_guessing(
            self, router, make_session):
        # If the pointer was not visible we cannot tell where the cursor is,
        # and guessing index 0 could approve something the user meant to deny.
        adapter = FakeAdapter()
        message = router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                          index=1, label="No", live=True, current=None)
        assert adapter.keys == [] and adapter.texts == []
        assert "cursor" in message.lower() or "unknown" in message.lower()

    def test_an_absurd_distance_is_refused(self, router, make_session):
        adapter = FakeAdapter()
        message = router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                          index=99, label="?", live=True, current=0)
        assert adapter.keys == [] and "range" in message.lower()


class TestPickingAMenuAction:
    """When nothing is blocking, the dial offers our own actions."""

    def test_focus_raises_the_window(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=0, label="Focus", live=False)
        assert adapter.focused == ["/dev/ttys1"]

    def test_interrupt_sends_escape(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=1, label="Interrupt", live=False)
        assert adapter.keys == [("/dev/ttys1", "escape")]

    def test_a_slash_command_is_typed_but_not_submitted(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                index=2, label="/compact", live=False)
        assert adapter.texts == [("/dev/ttys1", "/compact", False)]
        assert adapter.keys == [], "destructive-ish commands wait for a human Enter"

    def test_open_output_needs_no_terminal(self, router, make_session):
        from claude_herder.models import ConsoleKind
        adapter = FakeAdapter()
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        message = router(adapter).perform(bg, index=0, label="Open output", live=False)
        assert adapter.texts == [] and adapter.keys == []
        assert "output" in message.lower()

    def test_an_unknown_label_does_nothing(self, router, make_session):
        adapter = FakeAdapter()
        message = router(adapter).perform(make_session(1, tty="/dev/ttys1"),
                                          index=0, label="Frobnicate", live=False)
        assert adapter.texts == [] and adapter.keys == []
        assert message


class TestUnreachableSessionsAreNeverTypedInto:
    def test_a_windowless_session_refuses_every_live_option(
            self, router, make_session):
        from claude_herder.models import ConsoleKind
        adapter = FakeAdapter()
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        message = router(adapter).perform(bg, index=0, label="Yes", live=True)
        assert adapter.texts == [] and adapter.keys == []
        assert "view only" in message.lower()

    def test_a_tty_with_no_window_is_also_refused(self, router, make_session):
        adapter = FakeAdapter()
        orphan = make_session(1, tty="/dev/ttys016", terminal=None)
        router(adapter).perform(orphan, index=0, label="Yes", live=True)
        assert adapter.texts == [] and adapter.keys == []


class TestDryRun:
    def test_dry_run_reports_without_sending(self, make_session):
        from claude_herder.config import Config
        adapter = FakeAdapter()
        r = Router(Config(), dry_run=True)
        r._override = adapter
        message = r.perform(make_session(1, tty="/dev/ttys1"),
                            index=0, label="Yes", live=True, current=0)
        assert adapter.texts == [] and adapter.keys == []
        assert "dry run" in message.lower()
