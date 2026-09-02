"""Reading a job's own state file, which is what the fleet view renders from.

Field precedence is taken from the CLI's row renderer: a finished job shows
`output.result`, a blocked one shows `needs`, and a working one shows `detail`
unless `detail` is one of the placeholder sentinels.
"""
import json
from claude_herder import jobstate


def write(tmp_path, short, **fields):
    job = tmp_path / "jobs" / short
    job.mkdir(parents=True)
    (job / "state.json").write_text(json.dumps(fields))
    return job


class TestPrecedence:
    def test_a_finished_job_shows_its_result(self, tmp_path, monkeypatch):
        write(tmp_path, "aaa", state="done", tempo="idle",
              detail="live status", output={"result": "shipped the fix"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("aaa") == "shipped the fix"

    def test_a_blocked_job_shows_what_it_needs(self, tmp_path, monkeypatch):
        write(tmp_path, "bbb", state="running", tempo="blocked",
              needs="approve Bash: echo hello", detail="doing things")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("bbb") == "approve Bash: echo hello"

    def test_a_working_job_shows_its_detail(self, tmp_path, monkeypatch):
        write(tmp_path, "ccc", state="running", tempo="active", detail="running tests")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("ccc") == "running tests"

    def test_result_wins_over_detail_when_done(self, tmp_path, monkeypatch):
        write(tmp_path, "ddd", state="done", tempo="idle",
              detail="not this", output={"result": "this one"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("ddd") == "this one"


class TestSentinels:
    def test_placeholder_details_are_treated_as_no_summary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        for i, sentinel in enumerate(jobstate.SENTINELS):
            write(tmp_path, f"s{i}", state="running", tempo="active", detail=sentinel)
            assert jobstate.summary_for(f"s{i}") == "", sentinel

    def test_the_start_prompt_is_not_a_real_question(self, tmp_path, monkeypatch):
        write(tmp_path, "eee", tempo="blocked", needs="send a prompt to start")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("eee") == ""


class TestSanitising:
    def test_reminder_blocks_are_stripped(self, tmp_path, monkeypatch):
        write(tmp_path, "fff", tempo="active",
              detail="before <system-reminder>noise here</system-reminder> after")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("fff") == "before after"

    def test_stray_tags_are_removed(self, tmp_path, monkeypatch):
        write(tmp_path, "ggg", tempo="active", detail="a <b>bold</b> claim")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("ggg") == "a bold claim"

    def test_whitespace_is_collapsed(self, tmp_path, monkeypatch):
        write(tmp_path, "hhh", tempo="active", detail="lots\n\n  of   space")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("hhh") == "lots of space"


class TestQuestions:
    def test_pending_question_options_are_returned(self, tmp_path, monkeypatch):
        write(tmp_path, "iii", tempo="blocked", needs="pick one",
              block={"questions": [{"options": [{"label": "Alpha"}, {"label": "Beta"}]}]})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.question_options("iii") == ("Alpha", "Beta")

    def test_plain_string_options_are_accepted(self, tmp_path, monkeypatch):
        write(tmp_path, "jjj", block={"questions": [{"options": ["Yes", "No"]}]})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.question_options("jjj") == ("Yes", "No")

    def test_no_pending_question_yields_nothing(self, tmp_path, monkeypatch):
        write(tmp_path, "kkk", tempo="active", detail="busy")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.question_options("kkk") == ()


class TestRobustness:
    def test_a_missing_job_is_empty_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("ghost") == ""
        assert jobstate.question_options("ghost") == ()

    def test_a_corrupt_state_file_is_survivable(self, tmp_path, monkeypatch):
        job = tmp_path / "jobs" / "lll"
        job.mkdir(parents=True)
        (job / "state.json").write_text("{ truncated")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for("lll") == ""

    def test_no_job_id_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert jobstate.summary_for(None) == ""
