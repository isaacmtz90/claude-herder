"""Parsing and error handling around the shell commands we depend on."""
import subprocess
import pytest
from claude_herder import liveness, processes
from claude_herder.adapters import base, bgjob, ghostty, iterm2


def fake_run(stdout="", returncode=0, raises=None):
    def _run(*a, **k):
        if raises:
            raise raises
        return subprocess.CompletedProcess(a[0] if a else [], returncode, stdout, "")
    return _run


class TestLivenessProbe:
    def test_parses_ps_output_into_pid_start_times(self, monkeypatch):
        monkeypatch.setattr(liveness.subprocess, "run", fake_run(
            "  501 Sun Aug 23 19:27:05 2026\n 1234 Mon Aug 24 08:00:00 2026\n"))
        probe = liveness.probe_proc_starts()
        assert probe[501] == "Sun Aug 23 19:27:05 2026"
        assert probe[1234].startswith("Mon Aug 24")

    def test_a_failed_probe_returns_none_not_an_empty_dict(self, monkeypatch):
        # None means "unknown"; {} would mean "nothing is alive" and blank the pad.
        monkeypatch.setattr(liveness.subprocess, "run",
                            fake_run(raises=OSError("no ps")))
        assert liveness.probe_proc_starts() is None

    def test_timeout_is_treated_as_unknown(self, monkeypatch):
        monkeypatch.setattr(liveness.subprocess, "run", fake_run(
            raises=subprocess.TimeoutExpired("ps", 5)))
        assert liveness.probe_proc_starts() is None

    def test_junk_lines_are_skipped(self, monkeypatch):
        monkeypatch.setattr(liveness.subprocess, "run",
                            fake_run("garbage\n\n  7 Sun Aug 23 19:27:05 2026\n"))
        assert list(liveness.probe_proc_starts()) == [7]


class TestClaudeTtys:
    def test_reads_the_process_table(self, monkeypatch):
        monkeypatch.setattr(processes.subprocess, "run",
                            fake_run("61864 ttys005 claude\n 460 ?? UserEventAgent\n"))
        assert processes.claude_ttys() == {61864: "/dev/ttys005"}

    def test_a_failed_ps_yields_no_ttys_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(processes.subprocess, "run",
                            fake_run(raises=OSError("boom")))
        assert processes.claude_ttys() == {}


class TestCwdLookup:
    def test_parses_lsof_field_output(self, monkeypatch):
        monkeypatch.setattr(processes.subprocess, "run",
                            fake_run("p61864\nfcwd\nn/Users/x/Repo\n"
                                     "p5947\nfcwd\nn/Users/x/Other\n"))
        assert processes.cwds_for([61864, 5947]) == {
            61864: "/Users/x/Repo", 5947: "/Users/x/Other"}

    def test_no_pids_skips_the_subprocess_entirely(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("should not shell out for an empty pid list")
        monkeypatch.setattr(processes.subprocess, "run", explode)
        assert processes.cwds_for([]) == {}

    def test_lsof_failure_is_not_fatal(self, monkeypatch):
        monkeypatch.setattr(processes.subprocess, "run",
                            fake_run(raises=OSError("no lsof")))
        assert processes.cwds_for([1]) == {}


class TestOsascriptWrapper:
    def test_success_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(base.subprocess, "run", fake_run("ok\n"))
        assert base.run_osascript("x") == (True, "ok")

    def test_nonzero_exit_is_reported_as_failure(self, monkeypatch):
        monkeypatch.setattr(base.subprocess, "run", fake_run("", returncode=1))
        ok, _ = base.run_osascript("x")
        assert ok is False

    def test_timeout_is_flagged_distinctly(self, monkeypatch):
        # A timeout usually means a macOS permission dialog is waiting.
        monkeypatch.setattr(base.subprocess, "run", fake_run(
            raises=subprocess.TimeoutExpired("osascript", 10)))
        assert base.run_osascript("x") == (False, "timeout")

    def test_missing_binary_is_handled(self, monkeypatch):
        monkeypatch.setattr(base.subprocess, "run", fake_run(raises=OSError("gone")))
        ok, _ = base.run_osascript("x")
        assert ok is False


class TestITerm2Adapter:
    def test_session_titles_are_parsed_from_tab_separated_output(self, monkeypatch):
        monkeypatch.setattr(iterm2, "run_osascript", lambda *a, **k: (
            True, "/dev/ttys005\t◐ my session (claude)\nnoise\n"))
        a = iterm2.ITerm2Adapter()
        assert a.session_titles() == {"/dev/ttys005": "◐ my session (claude)"}

    def test_discovery_failure_yields_no_sessions(self, monkeypatch):
        monkeypatch.setattr(iterm2, "run_osascript", lambda *a, **k: (False, "nope"))
        assert iterm2.ITerm2Adapter().session_titles() == {}

    def test_ownership_follows_discovery(self, monkeypatch):
        monkeypatch.setattr(iterm2, "run_osascript",
                            lambda *a, **k: (True, "/dev/ttys1\ttitle\n"))
        a = iterm2.ITerm2Adapter()
        assert a.owns("/dev/ttys1") is True
        assert a.owns("/dev/ttys9") is False
        assert a.owns(None) is False

    def test_writing_to_an_absent_tty_is_a_failure_not_a_silent_success(
            self, monkeypatch):
        monkeypatch.setattr(iterm2, "run_osascript", lambda *a, **k: (True, "notfound"))
        assert iterm2.ITerm2Adapter().send_text("/dev/ttys9", "x") is False

    def test_send_key_rejects_an_unknown_key_name(self):
        assert iterm2.ITerm2Adapter().send_key("/dev/ttys1", "hyperspace") is False

    def test_send_key_passes_code_points(self, monkeypatch):
        seen = {}
        def spy(script, args=()):
            seen["args"] = list(args); return True, "ok"
        monkeypatch.setattr(iterm2, "run_osascript", spy)
        assert iterm2.ITerm2Adapter().send_key("/dev/ttys1", "down") is True
        assert seen["args"] == ["/dev/ttys1", 27, 91, 66]

    def test_focus_reports_whether_the_session_was_found(self, monkeypatch):
        monkeypatch.setattr(iterm2, "run_osascript", lambda *a, **k: (True, "ok"))
        assert iterm2.ITerm2Adapter().focus("/dev/ttys1") is True


class TestBgJobAdapter:
    def test_it_never_claims_to_be_controllable(self):
        a = bgjob.BgJobAdapter()
        assert a.can_control() is False
        assert a.send_text("/dev/ttys1", "x") is False
        assert a.send_key("/dev/ttys1", "enter") is False
        assert a.focus("/dev/ttys1") is False
        assert a.owns(None) is True

    def test_open_output_needs_a_job_id(self):
        assert bgjob.BgJobAdapter().open_output(None) is False

    def test_open_output_reports_a_missing_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bgjob, "jobs_dir", lambda: tmp_path)
        assert bgjob.BgJobAdapter().open_output("nope") is False

    def test_open_output_launches_the_finder_for_a_real_job(self, monkeypatch, tmp_path):
        (tmp_path / "abc").mkdir()
        monkeypatch.setattr(bgjob, "jobs_dir", lambda: tmp_path)
        opened = []
        monkeypatch.setattr(bgjob.subprocess, "run",
                            lambda cmd, **k: opened.append(cmd) or None)
        assert bgjob.BgJobAdapter().open_output("abc") is True
        assert opened and opened[0][0] == "open"


class TestGhosttyAdapter:
    def test_a_tty_iterm2_owns_is_not_ghostty(self):
        a = ghostty.GhosttyAdapter(iterm_ttys=frozenset({"/dev/ttys1"}))
        assert a.owns("/dev/ttys1") is False
        assert a.owns("/dev/ttys2") is True

    def test_tagging_an_unwritable_tty_fails_cleanly(self, monkeypatch):
        a = ghostty.GhosttyAdapter()
        monkeypatch.setattr(ghostty, "TITLE_SETTLE", 0)
        assert a.focus("/dev/definitely-not-a-tty") is False

    def test_focus_reports_a_missing_tab(self, monkeypatch, tmp_path):
        target = tmp_path / "faketty"
        target.write_text("")
        monkeypatch.setattr(ghostty, "TITLE_SETTLE", 0)
        monkeypatch.setattr(ghostty, "run_osascript", lambda *a, **k: (True, "notfound"))
        assert ghostty.GhosttyAdapter().focus(str(target)) is False

    def test_send_key_rejects_unknown_keys_before_stealing_focus(self):
        assert ghostty.GhosttyAdapter().send_key("/dev/ttys2", "hyperspace") is False
