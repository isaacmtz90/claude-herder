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

    def test_acting_with_nothing_selected_warns_instead_of_guessing(self, herder):
        h, pad = herder([])
        h.refresh()
        h.on_key(daemon.__dict__.get("APPROVE", 6))
        toasts = [f["m"] for f in pad.frames_of("toast")]
        assert any("no session" in t.lower() for t in toasts)


class TestEncoder:
    def test_rotation_is_forwarded_to_the_router(self, herder, make_session, monkeypatch):
        h, _ = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        seen = []
        monkeypatch.setattr(h.router, "scroll", lambda s, d: seen.append(d) or "")
        h.on_frame({"t": "enc", "d": -2})
        assert seen == [-2]

    def test_press_is_forwarded_as_select(self, herder, make_session, monkeypatch):
        h, _ = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        seen = []
        monkeypatch.setattr(h.router, "select_option", lambda s: seen.append(True) or "")
        h.on_frame({"t": "encbtn", "e": "press"})
        assert seen == [True]

    def test_unknown_frames_are_ignored_quietly(self, herder):
        h, _ = herder([])
        h.on_frame({"t": "nonsense"})
        h.on_frame({})
        # no exception is the assertion


class TestActionsReachTheRouter:
    def test_a_control_key_produces_a_toast(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        h.on_key(6)
        assert pad.frames_of("toast"), "the user should see what the key did"

    def test_dry_run_never_claims_to_have_sent(self, herder, make_session):
        h, pad = herder([make_session(1, tty="/dev/ttys1")])
        h.refresh(); h.on_key(0)
        h.on_key(6)
        assert any("dry run" in f["m"].lower() for f in pad.frames_of("toast"))


class TestShutdown:
    def test_stop_closes_the_pad(self, herder):
        h, pad = herder([])
        h.stop()
        h.run()
        assert pad.closed
