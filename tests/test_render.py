"""Rendering: what the pad is told, and what it must never be told."""
from claude_herder import render
from claude_herder.models import ConsoleKind, SessionState, Slot
from claude_herder import slots as S


def test_empty_slot_renders_as_empty_not_idle(make_session):
    frame = render.slots_payload(S.assign((), [], 6), selected=None)
    assert all(entry["st"] == "empty" for entry in frame)


def test_unknown_state_is_distinct_from_idle(make_session):
    unknown = make_session(1, tty="/dev/ttys1", state=SessionState.UNKNOWN)
    idle = make_session(2, tty="/dev/ttys2", state=SessionState.IDLE)
    payload = render.slots_payload(S.assign((), [unknown, idle], 6), selected=None)
    assert payload[0]["st"] != payload[1]["st"]
    assert payload[0]["st"] == "unknown"


def test_waiting_carries_the_reason_for_the_oled(make_session):
    s = make_session(1, tty="/dev/ttys1", state=SessionState.WAITING,
                     waiting_for="permission prompt")
    payload = render.slots_payload(S.assign((), [s], 6), selected=None)
    assert payload[0]["wf"] == "permission prompt"


def test_background_jobs_are_flagged_monitor_only(make_session):
    bg = make_session(1, kind=ConsoleKind.BGJOB, job_id="abc")
    payload = render.slots_payload(S.assign((), [bg], 6), selected=None)
    assert payload[0]["k"] == "bg"
    assert payload[0]["ctl"] is False


def test_controllable_console_is_flagged_controllable(make_session):
    s = make_session(1, tty="/dev/ttys1")
    payload = render.slots_payload(S.assign((), [s], 6), selected=None)
    assert payload[0]["ctl"] is True


def test_labels_are_truncated_to_fit_the_oled(make_session):
    s = make_session(1, tty="/dev/ttys1", name="x" * 200)
    payload = render.slots_payload(S.assign((), [s], 6), selected=None)
    assert len(payload[0]["nm"]) <= render.MAX_LABEL_CHARS


def test_selection_is_marked_on_exactly_one_slot(make_session):
    a, b = make_session(1, tty="/dev/ttys1"), make_session(2, tty="/dev/ttys2")
    payload = render.slots_payload(S.assign((), [a, b], 6), selected=1)
    assert [e.get("sel", False) for e in payload].count(True) == 1
    assert payload[1]["sel"] is True


def test_payload_fits_within_one_protocol_frame(make_session):
    from claude_herder import protocol
    many = [make_session(i, tty=f"/dev/ttys{i}", name="a-fairly-long-session-name")
            for i in range(1, 7)]
    payload = render.slots_payload(S.assign((), many, 6), selected=0)
    assert len(protocol.encode(protocol.slots_frame(payload))) < protocol.MAX_LINE_BYTES
