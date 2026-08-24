"""Key -> action dispatch. The safety-critical part of the daemon."""
import pytest
from claude_herder.actions import Router, APPROVE_KEY, DENY_KEY, INTERRUPT_KEY, CANNED_KEYS
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


class TestPermissionKeys:
    def test_approve_sends_the_configured_digit_without_submitting(self, router, make_session):
        adapter = FakeAdapter()
        r = router(adapter, Config(approve_key="1"))
        r.act(APPROVE_KEY, make_session(1, tty="/dev/ttys1"))
        assert adapter.texts == [("/dev/ttys1", "1", False)]

    def test_deny_sends_its_own_digit(self, router, make_session):
        adapter = FakeAdapter()
        r = router(adapter, Config(deny_key="3"))
        r.act(DENY_KEY, make_session(1, tty="/dev/ttys1"))
        assert adapter.texts == [("/dev/ttys1", "3", False)]

    def test_digits_come_from_config_not_hardcoded(self, router, make_session):
        adapter = FakeAdapter()
        r = router(adapter, Config(approve_key="y"))
        r.act(APPROVE_KEY, make_session(1, tty="/dev/ttys1"))
        assert adapter.texts[0][1] == "y"


class TestInterruptAndNavigation:
    def test_interrupt_sends_escape_as_a_key_not_text(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).act(INTERRUPT_KEY, make_session(1, tty="/dev/ttys1"))
        assert adapter.keys == [("/dev/ttys1", "escape")]

    def test_encoder_scrolls_with_arrow_keys(self, router, make_session):
        adapter = FakeAdapter()
        r = router(adapter)
        session = make_session(1, tty="/dev/ttys1")
        r.scroll(session, -1)
        r.scroll(session, 2)
        assert [k for _, k in adapter.keys] == ["up", "down", "down"]

    def test_zero_delta_does_nothing(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).scroll(make_session(1, tty="/dev/ttys1"), 0)
        assert adapter.keys == []

    def test_encoder_press_selects_with_enter(self, router, make_session):
        adapter = FakeAdapter()
        router(adapter).select_option(make_session(1, tty="/dev/ttys1"))
        assert adapter.keys == [("/dev/ttys1", "enter")]


class TestCannedKeys:
    def test_submitting_sends_text_then_enter_separately(self, router, make_session):
        # Deliberate: Claude Code's TUI reads an Enter arriving in the same input
        # batch as a newline rather than a submit, so text and Enter are sent as
        # two operations with a gap between them.
        adapter = FakeAdapter()
        config = Config(canned=(CannedKey("compact", "/compact", submit=True),),
                        submit_delay=0.0)
        router(adapter, config).act(CANNED_KEYS[0], make_session(1, tty="/dev/ttys1"))
        assert adapter.texts == [("/dev/ttys1", "/compact", False)]
        assert adapter.keys == [("/dev/ttys1", "enter")]

    def test_a_non_submitting_key_never_presses_enter(self, router, make_session):
        adapter = FakeAdapter()
        config = Config(canned=(CannedKey("plan", "\x1b[Z", submit=False),))
        router(adapter, config).act(CANNED_KEYS[0], make_session(1, tty="/dev/ttys1"))
        assert adapter.keys == []

    def test_an_unconfigured_canned_key_does_nothing(self, router, make_session):
        adapter = FakeAdapter()
        config = Config(canned=(CannedKey("only one", "x"),))
        router(adapter, config).act(CANNED_KEYS[2], make_session(1, tty="/dev/ttys1"))
        assert adapter.texts == []

    def test_focus_is_available_as_a_builtin_binding(self, router, make_session):
        adapter = FakeAdapter()
        config = Config(canned=(CannedKey("focus", "@focus"),))
        router(adapter, config).act(CANNED_KEYS[0], make_session(1, tty="/dev/ttys1"))
        assert adapter.focused == ["/dev/ttys1"]
        assert adapter.texts == []


class TestBackgroundJobsAreNeverTypedInto:
    """A background session has no terminal. Every keystroke path must refuse
    rather than send input somewhere unintended."""

    def bg(self, make_session):
        return make_session(1, kind=ConsoleKind.BGJOB, job_id="abc", tty=None)

    @pytest.mark.parametrize("key", [DENY_KEY, INTERRUPT_KEY] + list(CANNED_KEYS))
    def test_control_keys_send_nothing_and_say_why(self, router, make_session, key):
        adapter = FakeAdapter()
        message = router(adapter).act(key, self.bg(make_session))
        assert adapter.texts == [] and adapter.keys == []
        assert message and "view only" in message.lower()

    def test_the_approve_key_becomes_open_output_for_a_background_job(
            self, router, make_session):
        # Approving is meaningless without a terminal, so the leftmost action key
        # is repurposed to the one thing that is useful: showing the job output.
        adapter = FakeAdapter()
        message = router(adapter).act(APPROVE_KEY, self.bg(make_session))
        assert adapter.texts == [] and adapter.keys == []
        assert "output" in message.lower()

    def test_encoder_sends_nothing(self, router, make_session):
        adapter = FakeAdapter()
        r = router(adapter)
        r.scroll(self.bg(make_session), 1)
        r.select_option(self.bg(make_session))
        assert adapter.keys == []

    def test_a_session_with_no_tty_is_never_typed_into(self, router, make_session):
        adapter = FakeAdapter()
        orphan = make_session(1, kind=ConsoleKind.INTERACTIVE, tty=None)
        router(adapter).act(APPROVE_KEY, orphan)
        assert adapter.texts == [] and adapter.keys == []


class TestDryRun:
    def test_dry_run_reports_without_sending(self, make_session):
        adapter = FakeAdapter()
        r = Router(Config(), dry_run=True)
        r._override = adapter
        message = r.act(APPROVE_KEY, make_session(1, tty="/dev/ttys1"))
        assert adapter.texts == [] and adapter.keys == []
        assert "dry run" in message.lower()
