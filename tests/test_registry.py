"""Parsing Claude Code's live session registry (~/.claude/sessions/<pid>.json)."""
import json
import pytest
from claude_herder import registry
from claude_herder.models import SessionState, ConsoleKind


def write(tmp_path, pid, **over):
    doc = {
        "pid": pid,
        "sessionId": f"sid-{pid}",
        "cwd": "/Users/x/Documents/Repo",
        "procStart": "Mon Aug 24 00:27:05 2026",
        "version": "2.1.241",
        "kind": "bg",
        "entrypoint": "cli",
        "name": "some work",
        "jobId": "abc123",
        "status": "busy",
        "updatedAt": 1787531990478,
    }
    doc.update(over)
    path = tmp_path / f"{pid}.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize("raw,expected", [
    ("busy", SessionState.BUSY),
    ("idle", SessionState.IDLE),
    ("waiting", SessionState.WAITING),
    ("shell", SessionState.SHELL),
])
def test_maps_every_documented_status_value(tmp_path, raw, expected):
    s = registry.parse_session_file(write(tmp_path, 1, status=raw))
    assert s.state is expected


def test_missing_status_is_unknown_never_idle(tmp_path):
    # v2.1.231 (VS Code) sessions have no status field. Rendering those as
    # idle would show a green "available" light for a session we know nothing about.
    doc = json.loads(write(tmp_path, 2).read_text())
    doc.pop("status")
    (tmp_path / "2.json").write_text(json.dumps(doc))
    s = registry.parse_session_file(tmp_path / "2.json")
    assert s.state is SessionState.UNKNOWN
    assert s.state is not SessionState.IDLE


def test_unrecognised_future_status_degrades_to_unknown(tmp_path):
    s = registry.parse_session_file(write(tmp_path, 3, status="teleporting"))
    assert s.state is SessionState.UNKNOWN


def test_waiting_for_is_carried_through_for_the_oled(tmp_path):
    s = registry.parse_session_file(
        write(tmp_path, 4, status="waiting", waitingFor="permission prompt"))
    assert s.waiting_for == "permission prompt"


def test_bg_kind_maps_to_bgjob_and_interactive_to_interactive(tmp_path):
    assert registry.parse_session_file(write(tmp_path, 5, kind="bg")).kind is ConsoleKind.BGJOB
    assert registry.parse_session_file(
        write(tmp_path, 6, kind="interactive")).kind is ConsoleKind.INTERACTIVE


def test_rejects_file_missing_required_identity_fields(tmp_path):
    (tmp_path / "7.json").write_text(json.dumps({"status": "busy"}))
    assert registry.parse_session_file(tmp_path / "7.json") is None


def test_malformed_json_returns_none_rather_than_raising(tmp_path):
    (tmp_path / "8.json").write_text("{ truncated")
    assert registry.parse_session_file(tmp_path / "8.json") is None


def test_read_all_skips_bad_files_and_returns_the_good_ones(tmp_path):
    write(tmp_path, 10)
    write(tmp_path, 11)
    (tmp_path / "12.json").write_text("garbage")
    (tmp_path / "notes.txt").write_text("ignored")
    sessions = registry.read_all(tmp_path)
    assert {s.pid for s in sessions} == {10, 11}


def test_read_all_on_missing_directory_is_empty_not_an_error(tmp_path):
    assert registry.read_all(tmp_path / "nope") == ()


def test_sessions_are_immutable(tmp_path):
    s = registry.parse_session_file(write(tmp_path, 13))
    with pytest.raises(Exception):
        s.state = SessionState.IDLE
