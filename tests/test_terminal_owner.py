"""Which terminal application, if any, owns a tty.

Daemon-hosted background workers get a pseudo-terminal of their own with no
window attached to it. "Has a tty" therefore does not mean "a human can see
it", and claiming a tty by exclusion ("not iTerm2, so it must be Ghostty")
sends keystrokes at a window that does not exist.
"""
from claude_herder import processes

# pid ppid comm  -- a real capture, trimmed: iTerm2 shells, a Ghostty shell,
# and a daemon-hosted worker whose parent chain never reaches a terminal.
PS_TREE = """\
1 0 launchd
1294 876 iTermServer-3.6.6
60879 1294 login
60880 60879 -zsh
61864 60880 claude
2001 1 ghostty
2002 2001 login
2003 2002 -zsh
2004 2003 claude
55947 1 claude
7001 55947 claude
7002 7001 2.1.241
"""

PS_TTY = """\
61864 ttys005 claude
2004 ttys020 claude
7002 ttys016 2.1.241
"""


def owners():
    return processes.terminal_owners(PS_TTY, PS_TREE)


class TestAttribution:
    def test_an_iterm2_shell_is_attributed_to_iterm2(self):
        assert owners()["/dev/ttys005"] == "iterm2"

    def test_a_ghostty_shell_is_attributed_to_ghostty(self):
        assert owners()["/dev/ttys020"] == "ghostty"

    def test_a_daemon_pty_has_no_terminal_owner(self):
        # The whole point: this tty exists, but no window shows it.
        assert owners()["/dev/ttys016"] is None

    def test_every_claude_tty_is_reported(self):
        assert set(owners()) == {"/dev/ttys005", "/dev/ttys020", "/dev/ttys016"}


class TestHelpers:
    def test_has_a_window_is_false_without_an_owner(self):
        assert processes.has_window("/dev/ttys016", owners()) is False

    def test_has_a_window_is_true_for_a_real_terminal(self):
        assert processes.has_window("/dev/ttys005", owners()) is True

    def test_a_tty_of_none_never_has_a_window(self):
        assert processes.has_window(None, owners()) is False

    def test_an_unknown_tty_is_treated_as_windowless(self):
        assert processes.has_window("/dev/ttys999", owners()) is False


class TestRobustness:
    def test_a_cycle_in_the_process_tree_terminates(self):
        tree = "10 11 a\n11 10 b\n"
        assert processes.terminal_owners("10 ttys1 claude\n", tree) == {"/dev/ttys1": None}

    def test_garbage_input_yields_no_owners(self):
        assert processes.terminal_owners("nonsense\n", "rubbish\n") == {}

    def test_unknown_terminal_apps_are_reported_as_unknown_not_guessed(self):
        tree = "5000 1 WeirdTerm\n5001 5000 claude\n"
        # Not a terminal we know how to drive, so it must not be claimed by an
        # adapter. Either None or a distinct marker is acceptable; what matters
        # is that it is not silently attributed to ghostty.
        assert processes.terminal_owners("5001 ttys3 claude\n", tree)["/dev/ttys3"] != "ghostty"


class TestFleetUsesAttribution:
    """A tty with no window must not make a session look reachable."""

    def test_a_windowless_pty_does_not_become_controllable(self, world):
        from claude_herder import fleet
        from claude_herder.models import SessionState
        import json
        docs = [{"pid": 700, "sessionId": "sid-700", "cwd": "/Users/x/Repo",
                 "procStart": "Mon Aug 24 00:27:05 2026", "kind": "bg",
                 "entrypoint": "cli", "name": "worker", "jobId": "j1",
                 "status": "busy"}]
        w = world(docs, ttys={700: "/dev/ttys016"})
        slots = fleet.snapshot(
            (), 12,
            sessions_provider=lambda: fleet.registry.read_all(w["sessions_dir"]),
            probe_provider=lambda: w["probe"],
            tty_provider=lambda: w["ttys"],
            journal_provider=lambda: frozenset(),
            cwd_provider=lambda pids: {},
            title_provider=lambda: {},
            owner_provider=lambda: {"/dev/ttys016": None},   # daemon pty
        )
        session = next(s.session for s in slots if s.occupied)
        assert session.tty is None, "a windowless pty must not be attached"
        assert session.controllable is False

    def test_a_windowed_tty_is_attached_normally(self, world):
        from claude_herder import fleet
        docs = [{"pid": 701, "sessionId": "sid-701", "cwd": "/Users/x/Repo",
                 "procStart": "Mon Aug 24 00:27:05 2026", "kind": "interactive",
                 "entrypoint": "cli", "name": "console", "status": "idle"}]
        w = world(docs, ttys={701: "/dev/ttys005"})
        slots = fleet.snapshot(
            (), 12,
            sessions_provider=lambda: fleet.registry.read_all(w["sessions_dir"]),
            probe_provider=lambda: w["probe"],
            tty_provider=lambda: w["ttys"],
            journal_provider=lambda: frozenset(),
            cwd_provider=lambda pids: {},
            title_provider=lambda: {},
            owner_provider=lambda: {"/dev/ttys005": "iterm2"},
        )
        session = next(s.session for s in slots if s.occupied)
        assert session.tty == "/dev/ttys005"
        assert session.controllable is True


class TestRouterPicksAdapterByAttribution:
    """Adapter choice must follow positive attribution, never exclusion."""

    def _router(self):
        from claude_herder.actions import Router
        from claude_herder.config import Config
        return Router(Config())

    def test_an_iterm2_session_uses_the_iterm2_adapter(self, make_session):
        s = make_session(1, tty="/dev/ttys005").with_(terminal="iterm2")
        assert self._router().adapter_for(s).name == "iterm2"

    def test_a_ghostty_session_uses_the_ghostty_adapter(self, make_session):
        s = make_session(1, tty="/dev/ttys020").with_(terminal="ghostty")
        assert self._router().adapter_for(s).name == "ghostty"

    def test_an_unattributed_tty_is_never_claimed_by_ghostty(self, make_session):
        # This is the bug: a daemon pseudo-terminal is not an iTerm2 tty, and
        # "not iTerm2 therefore Ghostty" would tag it and click at a window
        # that does not exist.
        s = make_session(1, tty="/dev/ttys016").with_(terminal=None)
        adapter = self._router().adapter_for(s)
        assert adapter.name != "ghostty"
        assert adapter.can_control() is False

    def test_a_session_with_no_tty_is_monitor_only(self, make_session):
        from claude_herder.models import ConsoleKind
        s = make_session(1, kind=ConsoleKind.BGJOB, job_id="j", tty=None)
        assert self._router().adapter_for(s).can_control() is False
