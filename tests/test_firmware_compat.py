"""Static checks on the pad firmware.

The firmware runs under CircuitPython, which cannot be imported here, so none of
it is exercised by the rest of the suite. These checks catch the class of bug
that actually bit: valid CPython that CircuitPython does not implement, which
fails at runtime on the device with only a line number to go on.
"""
import ast
from pathlib import Path
import pytest

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware"
SOURCES = sorted(FIRMWARE.glob("*.py"))

#: Modules CircuitPython does not ship.
UNAVAILABLE_MODULES = {
    "dataclasses", "typing", "functools", "enum", "abc", "logging",
    "subprocess", "pathlib", "argparse", "threading", "asyncio", "re",
}


def trees():
    for path in SOURCES:
        if path.name == "flash.py":
            continue  # host-side tool, runs on CPython
        yield path, ast.parse(path.read_text())


def test_there_is_firmware_to_check():
    assert [p.name for p in SOURCES]


def test_all_firmware_parses():
    for path, _ in trees():
        assert path


def test_next_is_never_called_with_a_default():
    """CircuitPython's next() takes exactly one argument.

    `next(gen, fallback)` is idiomatic CPython and raises
    "function takes 1 positional arguments but 2 were given" on the device.
    """
    for path, tree in trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "next" and len(node.args) > 1):
                pytest.fail(f"{path.name}:{node.lineno}: next() with a default "
                            f"argument is unsupported on CircuitPython")


def test_min_and_max_are_never_called_with_default_kwarg():
    """`min(x, default=0)` is likewise unavailable."""
    for path, tree in trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"min", "max"}):
                for kw in node.keywords:
                    if kw.arg == "default":
                        pytest.fail(f"{path.name}:{node.lineno}: "
                                    f"{node.func.id}(default=...) is unsupported")


def test_no_imports_that_circuitpython_lacks():
    for path, tree in trees():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in UNAVAILABLE_MODULES, (
                    f"{path.name}:{node.lineno}: CircuitPython has no {name!r}")


def test_no_fstring_equals_specifier():
    """The f"{x=}" debug form is a CPython 3.8+ nicety CircuitPython lacks."""
    for path, _ in trees():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert "=}" not in line, f"{path.name}:{number}: f-string '=' specifier"


def test_the_protocol_frame_keys_match_the_daemon():
    """The pad and daemon must agree on frame tags, and nothing type-checks
    across that boundary."""
    from claude_herder import protocol
    source = (FIRMWARE / "code.py").read_text()
    for frame in (protocol.slots_frame([]), protocol.sel_frame(0),
                  protocol.toast_frame("x"), protocol.heartbeat_frame(),
                  protocol.context_frame("idle", "Ready"),
                  protocol.cursor_frame(0), protocol.ping_frame()):
        tag = frame["t"]
        assert f'"{tag}"' in source, f"firmware does not handle frame tag {tag!r}"


def test_slot_count_agrees_across_the_usb_boundary():
    """The pad and the daemon must agree on how many keys are sessions.

    Nothing type-checks across the serial link, so a mismatch would show up as
    silently missing slots rather than as an error.
    """
    from claude_herder.slots import DEFAULT_CAPACITY
    source = (FIRMWARE / "code.py").read_text()
    for line in source.splitlines():
        if line.startswith("SLOT_KEYS"):
            firmware_slots = int(line.split("=")[1].split("#")[0].strip())
            break
    else:
        raise AssertionError("firmware does not define SLOT_KEYS")
    assert firmware_slots == DEFAULT_CAPACITY == 12


def test_firmware_has_no_action_key_block():
    """Requirement: the action row is gone; every key is a session."""
    source = (FIRMWARE / "code.py").read_text()
    assert "ACTION_KEYS" not in source, "action keys should no longer exist"
