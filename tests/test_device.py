"""Finding the right serial port, which is subtler than it looks."""
from dataclasses import dataclass
from claude_herder import device


@dataclass
class FakePort:
    device: str
    vid: int | None
    pid: int | None


PAD = (device.MACROPAD_VID, device.MACROPAD_PID)
OTHER = (0x1234, 0x5678)


def ports(*specs):
    return [FakePort(name, vid, pid) for name, (vid, pid) in specs]


def test_picks_the_data_interface_not_the_repl_console():
    # Enabling a second CDC channel makes the pad enumerate TWICE. The lower
    # interface is the REPL console; connecting there looks like a dead daemon.
    found = device.select_data_port(ports(
        ("/dev/cu.usbmodem1401", PAD),
        ("/dev/cu.usbmodem1403", PAD),
    ))
    assert found == "/dev/cu.usbmodem1403"


def test_ignores_devices_that_are_not_the_macropad():
    found = device.select_data_port(ports(
        ("/dev/cu.Bluetooth-Incoming-Port", (None, None)),
        ("/dev/cu.usbserial-1", OTHER),
        ("/dev/cu.usbmodem1403", PAD),
    ))
    assert found == "/dev/cu.usbmodem1403"


def test_returns_none_when_the_pad_is_unplugged():
    assert device.select_data_port(ports(("/dev/cu.usbserial-1", OTHER))) is None


def test_single_interface_pad_still_returns_something():
    # Firmware without the data channel: better to connect and fail loudly
    # than to report "no pad" when one is plainly attached.
    found = device.select_data_port(ports(("/dev/cu.usbmodem1401", PAD)))
    assert found == "/dev/cu.usbmodem1401"


def test_ordering_of_the_input_list_does_not_matter():
    a = device.select_data_port(ports(("/dev/cu.usbmodem1401", PAD),
                                      ("/dev/cu.usbmodem1403", PAD)))
    b = device.select_data_port(ports(("/dev/cu.usbmodem1403", PAD),
                                      ("/dev/cu.usbmodem1401", PAD)))
    assert a == b


def test_handles_names_without_trailing_digits():
    found = device.select_data_port(ports(("/dev/cu.macropad", PAD)))
    assert found == "/dev/cu.macropad"


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("CLAUDE_HERDER_PORT", "/dev/cu.forced")
    assert device.select_data_port(ports(("/dev/cu.usbmodem1403", PAD))) == "/dev/cu.forced"
