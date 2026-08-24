"""Slot layout must survive a restart, or muscle memory is worthless."""
import json
from claude_herder import store, slots as S


def test_saving_and_loading_round_trips(tmp_path):
    path = tmp_path / "slots.json"
    store.save_layout({"sid-a": 0, "sid-b": 3}, path)
    assert store.load_layout(path) == {"sid-a": 0, "sid-b": 3}


def test_missing_file_is_an_empty_layout(tmp_path):
    assert store.load_layout(tmp_path / "absent.json") == {}


def test_corrupt_file_does_not_crash_startup(tmp_path):
    path = tmp_path / "slots.json"
    path.write_text("{ truncated")
    assert store.load_layout(path) == {}


def test_non_integer_indices_are_discarded(tmp_path):
    path = tmp_path / "slots.json"
    path.write_text(json.dumps({"sid-a": "zero", "sid-b": 2}))
    assert store.load_layout(path) == {"sid-b": 2}


def test_unwritable_path_is_not_fatal(tmp_path):
    # Failing to persist is a nuisance, not a reason to stop herding.
    store.save_layout({"a": 1}, tmp_path / "no" / "such" / "dir" / "slots.json")


def test_layout_of_extracts_the_mapping_from_slots(make_session):
    assigned = S.assign((), [make_session(1, tty="/dev/t1"),
                             make_session(2, tty="/dev/t2")], 6)
    layout = store.layout_of(assigned)
    assert set(layout.values()) == {0, 1}
    assert len(layout) == 2


class TestPinning:
    def test_a_remembered_session_returns_to_its_own_key(self, make_session):
        a = make_session(1, sid="sid-a", tty="/dev/t1")
        # Fresh daemon, no in-memory history, but a saved layout putting a on key 4.
        assigned = S.assign((), [a], 6, pinned={"sid-a": 4})
        assert S.index_of(assigned, "sid-a") == 4

    def test_a_pin_beyond_capacity_is_ignored(self, make_session):
        a = make_session(1, sid="sid-a", tty="/dev/t1")
        assigned = S.assign((), [a], 6, pinned={"sid-a": 99})
        assert S.index_of(assigned, "sid-a") == 0

    def test_live_placement_wins_over_a_stale_pin(self, make_session):
        a = make_session(1, sid="sid-a", tty="/dev/t1")
        first = S.assign((), [a], 6)                      # lands on key 0
        again = S.assign(first, [a], 6, pinned={"sid-a": 5})
        assert S.index_of(again, "sid-a") == 0, "don't move a session already on the pad"

    def test_two_pins_cannot_collide(self, make_session):
        a = make_session(1, sid="sid-a", tty="/dev/t1")
        b = make_session(2, sid="sid-b", tty="/dev/t2")
        assigned = S.assign((), [a, b], 6, pinned={"sid-a": 2, "sid-b": 2})
        indices = [S.index_of(assigned, s) for s in ("sid-a", "sid-b")]
        assert len(set(indices)) == 2 and None not in indices
