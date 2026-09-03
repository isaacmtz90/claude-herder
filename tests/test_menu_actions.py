"""The dial's action menu, and whether picking one actually runs it."""
import pytest
from claude_herder import context
from claude_herder.config import Config, MenuAction, load
from claude_herder.actions import Router
from claude_herder.models import ConsoleKind, SessionState


class FakeAdapter:
    name = "fake"

    def __init__(self):
        self.texts, self.keys, self.focused = [], [], []

    def can_control(self): return True
    def owns(self, tty): return True
    def send_text(self, tty, text, submit=False):
        self.texts.append((tty, text, submit)); return True
    def send_key(self, tty, key):
        self.keys.append((tty, key)); return True
    def focus(self, tty):
        self.focused.append(tty); return True


@pytest.fixture
def router():
    def _make(config=None):
        r = Router(config or Config(), dry_run=False)
        r._override = FakeAdapter()
        return r, r._override
    return _make


class TestSlashCommandsRun:
    """The reported bug: the command appeared on the console but never ran."""

    def test_compact_is_submitted(self, router, make_session):
        r, adapter = router()
        r.perform(make_session(1, tty="/dev/ttys1"), index=2, label="/compact",
                  live=False)
        assert adapter.texts == [("/dev/ttys1", "/compact", False)]
        assert adapter.keys == [("/dev/ttys1", "enter")], "typing it is not running it"

    def test_clear_is_submitted(self, router, make_session):
        r, adapter = router()
        r.perform(make_session(1, tty="/dev/ttys1"), index=3, label="/clear",
                  live=False)
        assert adapter.keys == [("/dev/ttys1", "enter")]

    def test_submission_can_be_turned_off_per_action(self, router, make_session):
        config = Config(actions=(MenuAction("/risky", text="/risky", submit=False),))
        r, adapter = router(config)
        r.perform(make_session(1, tty="/dev/ttys1"), index=0, label="/risky",
                  live=False)
        assert adapter.texts == [("/dev/ttys1", "/risky", False)]
        assert adapter.keys == [], "this one was configured to wait for a human"

    def test_text_and_enter_are_separate_operations(self, router, make_session):
        # Claude's TUI reads an Enter arriving in the same input batch as a
        # newline rather than a submit, so they must not be sent together.
        r, adapter = router(Config(submit_delay=0.0))
        r.perform(make_session(1, tty="/dev/ttys1"), index=2, label="/compact",
                  live=False)
        assert adapter.texts[0][2] is False and adapter.keys


class TestBuiltins:
    def test_focus_raises_the_window(self, router, make_session):
        r, adapter = router()
        r.perform(make_session(1, tty="/dev/ttys1"), index=0, label="Focus", live=False)
        assert adapter.focused == ["/dev/ttys1"] and adapter.texts == []

    def test_interrupt_sends_escape(self, router, make_session):
        r, adapter = router()
        r.perform(make_session(1, tty="/dev/ttys1"), index=1, label="Interrupt",
                  live=False)
        assert adapter.keys == [("/dev/ttys1", "escape")] and adapter.texts == []

    def test_an_unknown_label_does_nothing(self, router, make_session):
        r, adapter = router()
        message = r.perform(make_session(1, tty="/dev/ttys1"), index=0,
                            label="Frobnicate", live=False)
        assert adapter.texts == [] and adapter.keys == [] and message


class TestMenuComesFromConfig:
    def test_the_default_menu_is_offered(self, make_session):
        s = make_session(1, tty="/dev/ttys1")
        frame = context.build(s, options_provider=lambda _s: (), now_ms=0)
        assert frame["o"] == ["Focus", "Interrupt", "/compact", "/clear"]

    def test_a_custom_menu_replaces_it(self, make_session):
        config = Config(actions=(MenuAction("Focus", builtin="focus"),
                                 MenuAction("/cost", text="/cost", submit=True)))
        s = make_session(1, tty="/dev/ttys1")
        frame = context.build(s, options_provider=lambda _s: (), now_ms=0,
                              actions=config.actions)
        assert frame["o"] == ["Focus", "/cost"]

    def test_a_windowless_session_only_gets_output(self, make_session):
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        frame = context.build(bg, options_provider=lambda _s: (), now_ms=0)
        assert frame["o"] == ["Open output"]

    def test_typing_actions_are_hidden_from_windowless_sessions(self, make_session):
        bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        frame = context.build(bg, options_provider=lambda _s: (), now_ms=0)
        assert not any(o.startswith("/") for o in frame["o"])


class TestConfigParsing:
    def test_actions_are_read_from_the_file(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text(
            '[[action]]\nlabel="Focus"\nbuiltin="focus"\n'
            '[[action]]\nlabel="/cost"\ntext="/cost"\nsubmit=true\n')
        actions = load(path).actions
        assert [a.label for a in actions] == ["Focus", "/cost"]
        assert actions[1].submit is True

    def test_submit_defaults_to_true_for_text_actions(self, tmp_path):
        # The old default was "type it and wait", which meant a picked command
        # sat on the prompt doing nothing.
        path = tmp_path / "c.toml"
        path.write_text('[[action]]\nlabel="/cost"\ntext="/cost"\n')
        assert load(path).actions[0].submit is True

    def test_the_legacy_canned_table_still_works(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[[canned]]\nlabel="/cost"\ntext="/cost"\nsubmit=true\n')
        assert [a.label for a in load(path).actions] == ["/cost"]

    def test_a_malformed_action_is_skipped(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[[action]]\nnonsense=1\n[[action]]\nlabel="/ok"\ntext="/ok"\n')
        assert [a.label for a in load(path).actions] == ["/ok"]

    def test_the_shipped_example_config_parses(self):
        from pathlib import Path
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        assert load(example).actions


class TestMenuActionsRefuseWhenADialogIsUp:
    """Observed live: a workspace-trust dialog appeared on a new directory, and
    a menu action's Enter confirmed its highlighted row ("No, exit"), quitting
    the session. Typing into a dialog is never what the user meant."""

    def test_a_dialog_blocks_a_slash_command(self, router, make_session):
        r, adapter = router()
        r.pane_reader = lambda tty: " Accessing workspace:\n ❯ No, exit\n   Yes, I trust\n"
        message = r.perform(make_session(1, tty="/dev/ttys1"), index=2,
                            label="/compact", live=False)
        assert adapter.texts == [] and adapter.keys == []
        assert "dialog" in message.lower() or "blocked" in message.lower()

    def test_a_dialog_blocks_interrupt_too(self, router, make_session):
        r, adapter = router()
        r.pane_reader = lambda tty: " Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"
        r.perform(make_session(1, tty="/dev/ttys1"), index=1, label="Interrupt",
                  live=False)
        assert adapter.keys == []

    def test_focus_is_still_allowed_during_a_dialog(self, router, make_session):
        # Raising the window sends no input, and is exactly what you want when
        # something unexpected is asking a question.
        r, adapter = router()
        r.pane_reader = lambda tty: " Accessing workspace:\n ❯ No, exit\n   Yes\n"
        r.perform(make_session(1, tty="/dev/ttys1"), index=0, label="Focus", live=False)
        assert adapter.focused == ["/dev/ttys1"]

    def test_a_clear_pane_lets_the_command_through(self, router, make_session):
        r, adapter = router()
        r.pane_reader = lambda tty: "just a normal prompt\n> \n"
        r.perform(make_session(1, tty="/dev/ttys1"), index=2, label="/compact",
                  live=False)
        assert adapter.keys == [("/dev/ttys1", "enter")]
