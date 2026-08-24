"""Attributing ttys to sessions, and spotting fleet attach clients."""
from claude_herder import processes
from claude_herder.models import ConsoleKind, SessionState

PS = """\
61864 ttys005 claude
55011 ttys015 claude
 5947 ttys017 claude
61769 ??      2.1.241
  460 ??      /usr/libexec/UserEventAgent
71234 ttys009 /opt/homebrew/bin/claude
80001 ttys003 vim
"""


class TestPsParsing:
    def test_keeps_only_claude_processes_that_own_a_tty(self):
        got = processes.parse_ps_tty(PS)
        assert set(got) == {61864, 55011, 5947, 71234}

    def test_normalises_tty_to_a_device_path(self):
        assert processes.parse_ps_tty(PS)[61864] == "/dev/ttys005"

    def test_ignores_tty_less_processes(self):
        assert 61769 not in processes.parse_ps_tty(PS)

    def test_ignores_unrelated_programs_on_a_tty(self):
        assert 80001 not in processes.parse_ps_tty(PS)

    def test_garbage_lines_do_not_raise(self):
        assert processes.parse_ps_tty("nonsense\n\n   \n") == {}


class TestAttachClients:
    def test_a_tty_claude_absent_from_the_registry_is_an_attach_client(self):
        found = processes.attach_client_pids(
            tty_by_pid={61864: "/dev/ttys005", 55011: "/dev/ttys015"},
            registry_pids={55011},
            journal_pids={61864},
        )
        assert found == frozenset({61864})

    def test_registry_sessions_are_never_attach_clients(self):
        found = processes.attach_client_pids(
            tty_by_pid={55011: "/dev/ttys015"}, registry_pids={55011}, journal_pids={55011})
        assert found == frozenset()

    def test_unjournalled_tty_claude_is_still_treated_as_an_attach_client(self):
        # The attach journal is best-effort; a console we can see and reach is
        # worth a key even if the daemon did not write a journal entry for it.
        found = processes.attach_client_pids(
            tty_by_pid={99: "/dev/ttys099"}, registry_pids=set(), journal_pids=set())
        assert found == frozenset({99})


class TestEnrichment:
    def test_registry_session_gains_its_tty_and_becomes_controllable(self, make_session):
        s = make_session(55011, tty=None)
        assert s.controllable is False
        out = processes.with_ttys([s], {55011: "/dev/ttys015"})
        assert out[0].tty == "/dev/ttys015"
        assert out[0].controllable is True

    def test_enrichment_does_not_mutate_the_original(self, make_session):
        s = make_session(55011, tty=None)
        processes.with_ttys([s], {55011: "/dev/ttys015"})
        assert s.tty is None

    def test_session_without_a_tty_is_left_alone(self, make_session):
        s = make_session(61769, kind=ConsoleKind.BGJOB, job_id="x")
        assert processes.with_ttys([s], {})[0].tty is None


class TestSynthesisedAttachSessions:
    def test_attach_console_is_controllable_but_state_is_unknown(self):
        # The registry entry with the status field belongs to the tty-less
        # worker; we cannot yet pair the two, so claiming a state would be a lie.
        made = processes.synthesise_attach_sessions(
            {61864: "/dev/ttys005"}, frozenset({61864}), cwd_by_pid={61864: "/Users/x/Repo"})
        assert len(made) == 1
        s = made[0]
        assert s.kind is ConsoleKind.ATTACH
        assert s.state is SessionState.UNKNOWN
        assert s.controllable is True
        assert s.tty == "/dev/ttys005"

    def test_label_falls_back_to_the_cwd_basename(self):
        made = processes.synthesise_attach_sessions(
            {61864: "/dev/ttys005"}, frozenset({61864}), cwd_by_pid={61864: "/Users/x/Project"})
        assert made[0].label == "Project"

    def test_ids_are_stable_across_calls_so_slots_do_not_reshuffle(self):
        a = processes.synthesise_attach_sessions({7: "/dev/ttys7"}, frozenset({7}), {})
        b = processes.synthesise_attach_sessions({7: "/dev/ttys7"}, frozenset({7}), {})
        assert a[0].session_id == b[0].session_id
