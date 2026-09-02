"""A one-line "what did it last say" for an idle session."""
import json
from claude_herder import summaries


def transcript(tmp_path, entries, sid="sid-1"):
    project = tmp_path / "projects" / "-Users-x-Repo"
    project.mkdir(parents=True)
    path = project / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def text(value):
    return {"type": "text", "text": value}


class TestExtraction:
    def test_returns_the_last_assistant_text(self, tmp_path):
        path = transcript(tmp_path, [
            assistant(text("older thing")),
            assistant(text("the latest thing")),
        ])
        assert summaries.last_assistant_text(path) == "the latest thing"

    def test_skips_thinking_and_tool_use_blocks(self, tmp_path):
        path = transcript(tmp_path, [
            assistant(text("real answer")),
            assistant({"type": "thinking", "thinking": "hmm"}),
            assistant({"type": "tool_use", "name": "Bash", "input": {}}),
        ])
        assert summaries.last_assistant_text(path) == "real answer"

    def test_only_the_first_line_is_used(self, tmp_path):
        path = transcript(tmp_path, [assistant(text("headline\nand detail\nmore"))])
        assert summaries.last_assistant_text(path) == "headline"

    def test_markdown_emphasis_is_stripped_for_a_tiny_screen(self, tmp_path):
        path = transcript(tmp_path, [assistant(text("**Done** with `the thing`"))])
        assert summaries.last_assistant_text(path) == "Done with the thing"

    def test_long_text_is_truncated(self, tmp_path):
        path = transcript(tmp_path, [assistant(text("x" * 500))])
        assert len(summaries.last_assistant_text(path)) <= summaries.MAX_SUMMARY

    def test_no_assistant_text_yields_empty(self, tmp_path):
        path = transcript(tmp_path, [{"type": "user", "message": {"content": "hi"}}])
        assert summaries.last_assistant_text(path) == ""

    def test_a_missing_file_yields_empty(self, tmp_path):
        assert summaries.last_assistant_text(tmp_path / "nope.jsonl") == ""

    def test_corrupt_lines_are_skipped(self, tmp_path):
        path = transcript(tmp_path, [assistant(text("good"))])
        path.write_text("{ broken\n" + path.read_text())
        assert summaries.last_assistant_text(path) == "good"

    def test_string_content_is_handled(self, tmp_path):
        path = transcript(tmp_path, [
            {"type": "assistant", "message": {"content": "plain string form"}}])
        assert summaries.last_assistant_text(path) == "plain string form"


class TestLookup:
    def test_finds_a_transcript_by_session_id(self, tmp_path, monkeypatch):
        transcript(tmp_path, [assistant(text("hello"))], sid="abc-123")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert summaries.transcript_for("abc-123") is not None

    def test_an_unknown_session_has_no_transcript(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert summaries.transcript_for("nope") is None

    def test_summary_for_a_session_is_end_to_end(self, tmp_path, monkeypatch, make_session):
        transcript(tmp_path, [assistant(text("all tests passing"))], sid="sid-9")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        s = make_session(1, sid="sid-9")
        assert summaries.summarise(s) == "all tests passing"

    def test_a_session_with_no_transcript_summarises_to_empty(
            self, tmp_path, monkeypatch, make_session):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert summaries.summarise(make_session(1, sid="ghost")) == ""


class TestPerformance:
    def test_only_the_tail_of_a_large_transcript_is_read(self, tmp_path):
        # Transcripts run to thousands of lines; reading them whole on every
        # poll would be wasteful.
        filler = [assistant(text(f"line {i}")) for i in range(5000)]
        path = transcript(tmp_path, filler + [assistant(text("the last word"))])
        assert path.stat().st_size > summaries.TAIL_BYTES // 2
        assert summaries.last_assistant_text(path) == "the last word"
