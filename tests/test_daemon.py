"""The daemon loop: selection handling, repaint decisions, toasts."""
import pytest
from claude_herder import __main__ as daemon
from claude_herder.config import Config
from claude_herder.models import ConsoleKind, SessionState, Slot


class FakePad:
    def __init__(self):
        self.sent = []
        self.incoming = []
        self.closed = False

    def send(self, frame):
        self.sent.append(frame); return True

    def poll(self):
        frames, self.incoming = tuple(self.incoming), []
        return frames

    def close(self):
        self.closed = True

    def frames_of(self, kind):
        return [f for f in self.sent if f.get("t") == kind]


@pytest.fixture
def herder(monkeypatch, make_session):
    def _make(sessions=()):
        slots = tuple(
            Slot(index=i, session=sessions[i] if i < len(sessions) else None)
            for i in range(6))
        monkeypatch.setattr(daemon.fleet, "snapshot", lambda *a, **k: slots)
        pad = FakePad()
        h = daemon.Herder(Config(), pad, dry_run=True)
        return h, pad
    return _make


class TestRendering:
    def test_first_refresh_pushes_a_slots_frame(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh()
        assert len(pad.frames_of("slots")) == 1

    def test_an_unchanged_fleet_is_not_resent(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.refresh(); h.refresh()
        assert len(pad.frames_of("slots")) == 1, "repainting an identical frame is waste"

    def test_selecting_forces_a_repaint(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh()
        h.on_key(0)
        h.refresh()
        assert len(pad.frames_of("slots")) == 2

    def test_pad_reconnect_forces_a_full_repaint(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh()
        h.on_frame({"t": "hello", "fw": "1.0.0"})
        h.refresh()
        assert len(pad.frames_of("slots")) == 2


class TestSelection:
    def test_tapping_a_slot_key_selects_it(self, herder, make_session):
        h, _ = herder([make_session(1, tty="/dev/ttys1"), make_session(2, tty="/dev/ttys2")])
        h.refresh()
        h.on_key(1)
        assert h.selected == 1
        assert h.selected_session().pid == 2

    def test_selecting_an_empty_slot_yields_no_session(self, herder):
        h, _ = herder([])
        h.refresh()
        h.on_key(3)
        assert h.selected == 3 and h.selected_session() is None

    def test_selection_is_dropped_when_its_session_disappears(
            self, herder, monkeypatch, make_session):
        h, _ = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        assert h.selected == 0
        # The fleet empties out from under us.
        monkeypatch.setattr(daemon.fleet, "snapshot",
                            lambda *a, **k: tuple(Slot(index=i) for i in range(6)))
        h.refresh()
        assert h.selected is None, "a stale selection would target nothing"


class TestEncoder:
    """The pad owns the option cursor so the dial feels instant; the host only
    hears about the final choice."""

    def test_encoder_motion_is_not_acted_on_by_the_host(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        before = len(pad.sent)
        h.on_frame({"t": "enc", "d": -2})
        assert len(pad.sent) == before, "rotation is informational only"

    def test_a_pick_is_routed(self, herder, make_session):
        h, _ = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        seen = []
        h.router.perform = (lambda s, index, label, live, current=None:
                            seen.append((index, label, live)) or "ok")
        h.on_frame({"t": "pick", "i": 1, "l": "No"})
        assert seen == [(1, "No", False)]

    def test_a_pick_on_a_live_prompt_is_marked_live(self, herder, make_session):
        from claude_herder.models import SessionState
        h, _ = herder([make_session(1, tty="/dev/ttys1", state=SessionState.WAITING)])
        h.options_provider = lambda s: ["Yes", "No"]
        h.refresh(); h.on_key(0)
        h.refresh()
        seen = []
        h.router.perform = (lambda s, index, label, live, current=None:
                            seen.append(live) or "ok")
        h.on_frame({"t": "pick", "i": 0, "l": "Yes"})
        assert seen == [True]

    def test_a_pick_with_nothing_selected_warns(self, herder):
        h, pad = herder([])
        h.refresh()
        h.on_frame({"t": "pick", "i": 0, "l": "Yes"})
        assert any("no session" in f.get("m", "").lower() for f in pad.frames_of("toast"))

    def test_unknown_frames_are_ignored_quietly(self, herder):
        h, _ = herder([])
        h.on_frame({"t": "nonsense"})
        h.on_frame({})


class TestContextFrames:
    def test_a_context_frame_follows_selection(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0); h.refresh()
        assert pad.frames_of("ctx"), "the detail area needs a frame"

    def test_an_unchanged_context_is_not_resent(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        h.refresh(); h.refresh(); h.refresh()
        assert len(pad.frames_of("ctx")) == 1

    def test_no_context_is_sent_for_an_empty_slot(self, herder):
        h, pad = herder([])
        h.refresh()
        assert pad.frames_of("ctx") == []


class TestShutdown:
    def test_stop_closes_the_pad(self, herder):
        h, pad = herder([])
        h.stop()
        h.run()
        assert pad.closed


class TestTapFocusesTheTerminal:
    """Requirement 5: pressing a session key should also bring its terminal
    forward, so the pad and the screen agree on what you are looking at."""

    def test_tapping_a_windowed_session_focuses_it(self, herder, make_session):
        h, _ = herder([make_session(1, tty="/dev/ttys005")])
        h.refresh()
        focused = []
        h.router.focus = lambda s: focused.append(s.tty) or True
        h.on_key(0)
        assert focused == ["/dev/ttys005"]

    def test_tapping_a_windowless_session_does_not_try_to_focus(
            self, herder, make_session):
        from claude_herder.models import ConsoleKind
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        h, _ = herder([bg])
        h.refresh()
        focused = []
        h.router.focus = lambda s: focused.append(s) or True
        h.on_key(0)
        assert focused == [], "there is no window to raise"

    def test_tapping_an_empty_slot_focuses_nothing(self, herder):
        h, _ = herder([])
        h.refresh()
        focused = []
        h.router.focus = lambda s: focused.append(s) or True
        h.on_key(3)
        assert focused == []

    def test_focus_on_tap_can_be_switched_off(self, herder, make_session):
        from claude_herder.config import Config
        h, _ = herder([make_session(1, tty="/dev/ttys005")])
        h.config = Config(focus_on_tap=False)
        h.refresh()
        focused = []
        h.router.focus = lambda s: focused.append(s) or True
        h.on_key(0)
        assert focused == []
