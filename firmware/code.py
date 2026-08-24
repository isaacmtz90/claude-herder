"""Claude Herder pad firmware: a renderer, and nothing more.

All state lives on the host. This program paints what it is told, reports what
is pressed, and makes exactly one decision on its own: if the host goes quiet,
say so, because stale state must never be mistaken for live state.
"""

import json
import time

import displayio
import terminalio
import usb_cdc
from adafruit_display_shapes.rect import Rect
from adafruit_display_text import label
from adafruit_macropad import MacroPad

FIRMWARE_VERSION = "1.0.0"

# --- tunables -----------------------------------------------------------------
HEARTBEAT_TIMEOUT = 3.0      # seconds of host silence before we flag it
FRAME_INTERVAL = 0.04        # LED animation step (~25fps)
BLINK_PERIOD = 0.5           # "needs you" urgency
BREATHE_PERIOD = 2.4         # "thinking" calm
MAX_READ = 512

SLOT_KEYS = 6                # keys 0-5 mirror sessions
ACTION_KEYS = (6, 7, 8, 9, 10, 11)

# --- palette ------------------------------------------------------------------
# Colour AND pattern differ per state, so the pad stays readable without
# relying on hue discrimination.
STATE_COLOUR = {
    "waiting": (255, 0, 0),
    "busy": (255, 130, 0),
    "idle": (0, 170, 60),
    "shell": (0, 70, 255),
    "error": (255, 0, 0),
    "unknown": (70, 70, 70),
    "empty": (0, 0, 0),
}
STATE_PATTERN = {
    "waiting": "blink",
    "busy": "breathe",
    "idle": "solid",
    "shell": "solid",
    "error": "solid",
    "unknown": "solid",
    "empty": "off",
}
ACTION_COLOUR = {
    6: (0, 160, 40),     # approve
    7: (200, 0, 0),      # deny
    8: (200, 140, 0),    # interrupt
    9: (40, 60, 200),    # canned 1
    10: (40, 60, 200),   # canned 2
    11: (40, 60, 200),   # canned 3
}
HOST_LOST_COLOUR = (90, 0, 120)
MONITOR_ONLY_SCALE = 0.35    # background jobs are dimmer: visible, not actionable
SELECTED_BOOST = 1.0
UNSELECTED_SCALE = 0.55


def scaled(colour, factor):
    return (int(colour[0] * factor), int(colour[1] * factor), int(colour[2] * factor))


class Display:
    """Four lines of text on the 128x64 OLED, with an inverted title bar."""

    def __init__(self, macropad):
        self.macropad = macropad
        group = displayio.Group()
        group.append(Rect(0, 0, macropad.display.width, 13, fill=0xFFFFFF))
        self.title = label.Label(
            terminalio.FONT, text="Claude Herder", color=0x000000,
            anchored_position=(macropad.display.width // 2, 0), anchor_point=(0.5, 0.0))
        group.append(self.title)
        self.lines = []
        for i in range(3):
            line = label.Label(
                terminalio.FONT, text="", color=0xFFFFFF,
                anchored_position=(2, 16 + i * 14), anchor_point=(0.0, 0.0))
            self.lines.append(line)
            group.append(line)
        macropad.display.root_group = group

    def show(self, title, rows):
        self.title.text = title[:21]
        for i, line in enumerate(self.lines):
            line.text = rows[i][:21] if i < len(rows) else ""


class Herder:
    def __init__(self):
        self.macropad = MacroPad()
        self.macropad.pixels.auto_write = False
        self.macropad.pixels.brightness = 0.45
        self.display = Display(self.macropad)
        self.slots = [{"i": i, "st": "empty"} for i in range(SLOT_KEYS)]
        self.selected = None
        self.toast = ""
        self.toast_until = 0.0
        self.last_heartbeat = time.monotonic()
        self.buffer = b""
        self.data = usb_cdc.data
        if self.data is not None:
            self.data.timeout = 0
        self._say({"t": "hello", "fw": FIRMWARE_VERSION, "slots": SLOT_KEYS})

    # --- transport ------------------------------------------------------------
    def _say(self, frame):
        if self.data is None:
            return
        try:
            self.data.write(json.dumps(frame).encode("utf-8") + b"\n")
        except OSError:
            pass  # host went away mid-write; the heartbeat check will notice

    def _read(self):
        if self.data is None or not self.data.in_waiting:
            return
        try:
            self.buffer += self.data.read(min(self.data.in_waiting, MAX_READ)) or b""
        except OSError:
            return
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            self._handle(line)
        if len(self.buffer) > 4096:
            self.buffer = b""  # wedged host: do not grow without bound

    def _handle(self, line):
        line = line.strip()
        if not line:
            return
        try:
            frame = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeError):
            return
        if not isinstance(frame, dict):
            return

        kind = frame.get("t")
        self.last_heartbeat = time.monotonic()
        if kind == "slots":
            incoming = frame.get("v")
            if isinstance(incoming, list):
                self.slots = incoming[:SLOT_KEYS]
                self.selected = next(
                    (e.get("i") for e in self.slots if e.get("sel")), self.selected)
                self._redraw()
        elif kind == "sel":
            self.selected = frame.get("i")
            self._redraw()
        elif kind == "toast":
            self.toast = str(frame.get("m", ""))
            self.toast_until = time.monotonic() + 2.0
            self._redraw()

    # --- rendering ------------------------------------------------------------
    def _entry(self, index):
        for entry in self.slots:
            if entry.get("i") == index:
                return entry
        return {"i": index, "st": "empty"}

    def _redraw(self):
        host_lost = (time.monotonic() - self.last_heartbeat) > HEARTBEAT_TIMEOUT
        if host_lost:
            self.display.show("herder offline", ["host not responding", "state may be stale", ""])
            return

        chosen = self._entry(self.selected) if self.selected is not None else None
        if chosen and chosen.get("st") != "empty":
            title = chosen.get("nm", "?")
            second = chosen.get("st", "")
            if chosen.get("wf"):
                second += ": " + chosen["wf"]
            if not chosen.get("ctl", True):
                second += " (view only)"
        else:
            title = "Claude Herder"
            second = "no session selected"

        strip = " ".join(
            self._glyph(self._entry(i).get("st", "empty")) for i in range(SLOT_KEYS))
        toast = self.toast if time.monotonic() < self.toast_until else ""
        self.display.show(title, [second, strip, toast])

    @staticmethod
    def _glyph(state):
        return {"waiting": "!", "busy": "*", "idle": "o", "shell": "$",
                "error": "X", "unknown": "?", "empty": "-"}.get(state, "?")

    def _paint(self, now):
        host_lost = (now - self.last_heartbeat) > HEARTBEAT_TIMEOUT
        pixels = self.macropad.pixels

        if host_lost:
            # Pad-generated: the one thing the firmware decides for itself.
            phase = 0.35 + 0.35 * self._triangle(now, 2.0)
            for key in range(12):
                pixels[key] = scaled(HOST_LOST_COLOUR, phase)
            pixels.show()
            return

        for key in range(SLOT_KEYS):
            entry = self._entry(key)
            state = entry.get("st", "empty")
            colour = STATE_COLOUR.get(state, STATE_COLOUR["unknown"])
            pattern = STATE_PATTERN.get(state, "solid")

            factor = 0.0 if pattern == "off" else 1.0
            if pattern == "blink":
                factor = 1.0 if (now % BLINK_PERIOD) < (BLINK_PERIOD / 2) else 0.08
            elif pattern == "breathe":
                factor = 0.25 + 0.75 * self._triangle(now, BREATHE_PERIOD)

            if state != "empty":
                factor *= SELECTED_BOOST if key == self.selected else UNSELECTED_SCALE
                if entry.get("ctl") is False:
                    factor *= MONITOR_ONLY_SCALE
            pixels[key] = scaled(colour, factor)

        for key in ACTION_KEYS:
            live = self.selected is not None and self._entry(self.selected).get("st") != "empty"
            pixels[key] = scaled(ACTION_COLOUR.get(key, (0, 0, 0)), 0.6 if live else 0.06)

        pixels.show()

    @staticmethod
    def _triangle(now, period):
        """0..1..0 ramp; cheaper than sin() and smooth enough for LEDs."""
        position = (now % period) / period
        return position * 2.0 if position < 0.5 else (1.0 - position) * 2.0

    # --- input ----------------------------------------------------------------
    def _inputs(self):
        event = self.macropad.keys.events.get()
        if event and event.pressed:
            key = event.key_number
            if key < SLOT_KEYS:
                self.selected = key
                self._redraw()
            self._say({"t": "key", "k": key, "e": "press"})

        position = self.macropad.encoder
        if position != self._encoder_last:
            delta = position - self._encoder_last
            self._encoder_last = position
            self._say({"t": "enc", "d": delta})

        self.macropad.encoder_switch_debounced.update()
        if self.macropad.encoder_switch_debounced.pressed:
            self._say({"t": "encbtn", "e": "press"})

    def run(self):
        self._encoder_last = self.macropad.encoder
        self._redraw()
        next_frame = 0.0
        while True:
            self._read()
            self._inputs()
            now = time.monotonic()
            if now >= next_frame:
                self._paint(now)
                if (now - self.last_heartbeat) > HEARTBEAT_TIMEOUT:
                    self._redraw()
                next_frame = now + FRAME_INTERVAL


Herder().run()
