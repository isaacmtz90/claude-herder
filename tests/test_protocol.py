"""Wire protocol: newline-delimited JSON, both directions."""
import pytest
from claude_herder import protocol as p


def test_encode_appends_single_newline_and_is_utf8():
    out = p.encode({"t": "hb"})
    assert out.endswith(b"\n")
    assert out.count(b"\n") == 1
    assert b'"t"' in out


def test_encode_survives_non_ascii_names():
    out = p.encode({"t": "toast", "m": "café ✓"})
    frames, rest = p.decode(out)
    assert rest == b""
    assert frames[0]["m"] == "café ✓"


def test_decode_returns_complete_frames_and_keeps_partial_remainder():
    buf = p.encode({"t": "key", "k": 3}) + b'{"t":"enc","d":-1'
    frames, rest = p.decode(buf)
    assert len(frames) == 1
    assert frames[0] == {"t": "key", "k": 3}
    assert rest == b'{"t":"enc","d":-1'


def test_decode_handles_several_frames_in_one_read():
    buf = p.encode({"t": "a"}) + p.encode({"t": "b"}) + p.encode({"t": "c"})
    frames, rest = p.decode(buf)
    assert [f["t"] for f in frames] == ["a", "b", "c"]
    assert rest == b""


def test_decode_skips_malformed_json_without_losing_later_frames():
    buf = b"{not json}\n" + p.encode({"t": "ok"})
    frames, rest = p.decode(buf)
    assert [f["t"] for f in frames] == ["ok"]


def test_decode_drops_an_overlong_line_rather_than_buffering_forever():
    # A device wedged mid-frame must not grow the buffer without bound.
    buf = b"x" * (p.MAX_LINE_BYTES + 10)
    frames, rest = p.decode(buf)
    assert frames == ()
    assert len(rest) < p.MAX_LINE_BYTES


def test_decode_ignores_non_object_frames():
    frames, _ = p.decode(b"[1,2,3]\n\"str\"\n" + p.encode({"t": "ok"}))
    assert [f["t"] for f in frames] == ["ok"]


def test_decode_is_pure_and_does_not_mutate_input():
    buf = bytearray(p.encode({"t": "a"}))
    before = bytes(buf)
    p.decode(bytes(buf))
    assert bytes(buf) == before


def test_slots_frame_is_compact_and_carries_required_fields():
    f = p.slots_frame([
        {"i": 0, "st": "waiting", "wf": "permission prompt", "nm": "repo", "k": "tty"},
    ])
    frames, _ = p.decode(p.encode(f))
    assert frames[0]["t"] == "slots"
    assert frames[0]["v"][0]["st"] == "waiting"


def test_builders_produce_decodable_frames():
    for frame in (p.heartbeat_frame(), p.sel_frame(2), p.toast_frame("hi")):
        frames, rest = p.decode(p.encode(frame))
        assert rest == b"" and len(frames) == 1


class TestContextFrame:
    def test_carries_state_and_label(self):
        frames, _ = p.decode(p.encode(p.context_frame("busy", "Wrangling")))
        assert frames[0]["st"] == "busy" and frames[0]["label"] == "Wrangling"

    def test_options_are_stringified(self):
        frame = p.context_frame("waiting", "Needs you", options=["Yes", 2])
        assert frame["o"] == ["Yes", "2"]

    def test_live_marks_a_real_prompt(self):
        assert p.context_frame("waiting", "x", options=["a"], live=True)["live"] is True
        assert p.context_frame("idle", "x", options=["a"])["live"] is False

    def test_empty_extras_are_omitted_to_keep_the_frame_small(self):
        frame = p.context_frame("idle", "Ready")
        assert "sum" not in frame and "o" not in frame and "age" not in frame

    def test_a_full_context_frame_fits_one_line(self):
        frame = p.context_frame(
            "waiting", "Needs you", summary="approve Bash: echo hello",
            age="2m", options=["Yes", "Yes, and don't ask again", "No"], live=True)
        assert len(p.encode(frame)) < p.MAX_LINE_BYTES
