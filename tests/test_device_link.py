"""The serial link's failure behaviour: an unplugged pad must never raise."""
import pytest
import serial as pyserial
from claude_herder import device, protocol


class FakeSerial:
    def __init__(self, *a, **k):
        self.is_open = True
        self.written = b""
        self.to_read = b""
        self.fail_on_write = False
        self.fail_on_read = False
        self.closed = False

    @property
    def in_waiting(self):
        if self.fail_on_read:
            raise OSError("device went away")
        return len(self.to_read)

    def write(self, data):
        if self.fail_on_write:
            raise pyserial.SerialException("write failed")
        self.written += data
        return len(data)

    def read(self, n):
        chunk, self.to_read = self.to_read[:n], self.to_read[n:]
        return chunk

    def close(self):
        self.closed = True
        self.is_open = False


@pytest.fixture
def pad(monkeypatch):
    made = []

    def factory(*a, **k):
        fake = FakeSerial()
        made.append(fake)
        return fake

    monkeypatch.setattr(device.serial, "Serial", factory)
    monkeypatch.setattr(device, "select_data_port", lambda: "/dev/fake")
    p = device.Pad()
    return p, made


class TestConnect:
    def test_send_connects_lazily(self, pad):
        p, made = pad
        assert p.send({"t": "hb"}) is True
        assert made and made[0].written.endswith(b"\n")

    def test_missing_pad_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr(device, "select_data_port", lambda: None)
        p = device.Pad()
        assert p.connect() is False
        assert p.send({"t": "hb"}) is False
        assert p.poll() == ()

    def test_open_failure_is_swallowed(self, monkeypatch):
        def boom(*a, **k):
            raise pyserial.SerialException("in use")
        monkeypatch.setattr(device.serial, "Serial", boom)
        monkeypatch.setattr(device, "select_data_port", lambda: "/dev/fake")
        assert device.Pad().connect() is False

    def test_reconnect_attempts_are_rate_limited(self, monkeypatch):
        calls = []
        monkeypatch.setattr(device, "select_data_port",
                            lambda: calls.append(1) or None)
        p = device.Pad()
        for _ in range(5):
            p.connect()
        assert len(calls) == 1, "a missing pad must not be probed in a tight loop"


class TestFraming:
    def test_poll_returns_complete_frames_only(self, pad):
        p, made = pad
        p.connect()
        made[0].to_read = protocol.encode({"t": "key", "k": 2}) + b'{"t":"enc"'
        assert p.poll() == ({"t": "key", "k": 2},)

    def test_a_split_frame_is_reassembled_across_polls(self, pad):
        p, made = pad
        p.connect()
        whole = protocol.encode({"t": "key", "k": 5})
        made[0].to_read = whole[:6]
        assert p.poll() == ()
        made[0].to_read = whole[6:]
        assert p.poll() == ({"t": "key", "k": 5},)

    def test_nothing_waiting_returns_empty(self, pad):
        p, _ = pad
        p.connect()
        assert p.poll() == ()


class TestUnplug:
    def test_write_failure_drops_the_link(self, pad):
        p, made = pad
        p.connect()
        made[0].fail_on_write = True
        assert p.send({"t": "hb"}) is False
        assert p.connected is False

    def test_read_failure_drops_the_link(self, pad):
        p, made = pad
        p.connect()
        made[0].fail_on_read = True
        assert p.poll() == ()
        assert p.connected is False

    def test_close_is_idempotent(self, pad):
        p, _ = pad
        p.connect()
        p.close(); p.close()
        assert p.connected is False
