"""Claude Herder pad firmware: a renderer, and nothing more.

All state lives on the host. This program paints what it is told, reports what
is pressed, and makes exactly one decision on its own: if the host goes quiet,
say so, because stale state must never be mistaken for live state.

Layout, on a 128x64 panel with the 6x12 built-in font (21 columns, 5 rows):

    +-----------------------------+  inverted title bar: selected session
    | detect adafruit keyboard in |
    | Wrangling  1m12s        (*) |  state, elapsed, spinner
    | > 1. Yes                    |  detail: summary, or the live options
    |   2. No                     |
    | approved                    |  transient toast
    +-----------------------------+

All twelve keys are sessions; there is no action row. Everything you *do* goes
through the encoder: turning it moves the cursor over the option list, pressing
it picks the highlighted one.
"""

import json
import time

import displayio
import terminalio
import usb_cdc
from adafruit_display_shapes.rect import Rect
from adafruit_display_text import label
from adafruit_macropad import MacroPad

FIRMWARE_VERSION = "2.0.0"

# --- tunables -----------------------------------------------------------------
HEARTBEAT_TIMEOUT = 8.0      # host silence before we flag it; > any host stall
FRAME_INTERVAL = 0.04        # LED animation step (~25fps)
BLINK_PERIOD = 0.5           # "needs you" urgency
BREATHE_PERIOD = 2.4         # "thinking" calm
SPINNER_PERIOD = 0.12        # OLED throbber step
MENU_LINGER = 6.0            # seconds the action menu stays up after a turn
MAX_READ = 512
MAX_BUFFER = 4096

SLOT_KEYS = 12               # every key is a session; input is via the encoder

# --- display geometry ---------------------------------------------------------
FONT_H = 12                  # measured: terminalio.FONT bounding box is 6x12
TITLE_H = 12
BODY_ROWS = 4
COLS = 21                    # 128 / 6

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
    "error": "blink",
    "unknown": "solid",
    "empty": "off",
}
HOST_LOST_COLOUR = (90, 0, 120)
MONITOR_ONLY_SCALE = 0.35    # background jobs are dimmer: visible, not actionable
UNSELECTED_SCALE = 0.55

# Claude's own throbber shapes, cycled while a session is working.
SPINNER = ("*", "+", ".", "+")
# Shown while a session is blocked, so "needs you" reads as urgent even on the
# monochrome panel.
WAITING_MARKER = ("!", " ")


def scaled(colour, factor):
    return (int(colour[0] * factor), int(colour[1] * factor), int(colour[2] * factor))


def clip(text):
    return text[:COLS]


class Display:
    """An inverted title bar plus four body lines."""

    def __init__(self, macropad):
        self.macropad = macropad
        group = displayio.Group()
        group.append(Rect(0, 0, macropad.display.width, TITLE_H, fill=0xFFFFFF))
        self.title = label.Label(
            terminalio.FONT, text="Claude Herder", color=0x000000,
            anchored_position=(macropad.display.width // 2, 0), anchor_point=(0.5, 0.0))
        group.append(self.title)
        self.lines = []
        for i in range(BODY_ROWS):
            line = label.Label(
                terminalio.FONT, text="", color=0xFFFFFF,
                anchored_position=(1, TITLE_H + 1 + i * FONT_H), anchor_point=(0.0, 0.0))
            self.lines.append(line)
            group.append(line)
        macropad.display.root_group = group
        # Assigning label.text triggers a bitmap rebuild, so at 25fps an
        # unconditional write would spend the whole loop redrawing unchanged
        # glyphs. Track what is on screen and only write real changes.
        self._current = [None] * (BODY_ROWS + 1)

    def show(self, title, rows):
        wanted = [clip(title)] + [
            clip(rows[i]) if i < len(rows) else "" for i in range(BODY_ROWS)]
        if wanted[0] != self._current[0]:
            self.title.text = wanted[0]
            self._current[0] = wanted[0]
        for i, line in enumerate(self.lines):
            if wanted[i + 1] != self._current[i + 1]:
                line.text = wanted[i + 1]
                self._current[i + 1] = wanted[i + 1]


class Herder:
    def __init__(self):
        self.macropad = MacroPad()
        self.macropad.pixels.auto_write = False
        self.macropad.pixels.brightness = 0.45
        self.display = Display(self.macropad)

        self.slots = [{"i": i, "st": "empty"} for i in range(SLOT_KEYS)]
        self.selected = None
        self.context = {}          # detail for the selected session
        self.cursor = 0            # highlighted option
        self.menu_until = 0.0      # while > now, show options instead of summary
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
        if len(self.buffer) > MAX_BUFFER:
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
        # ANY frame proves the host is alive, so a slots update doubles as a
        # heartbeat and a quiet host is unambiguous.
        self.last_heartbeat = time.monotonic()
        if kind == "hb":
            return                      # liveness only; nothing to render
        if kind == "slots":
            incoming = frame.get("v")
            if isinstance(incoming, list):
                self.slots = incoming[:SLOT_KEYS]
                # A plain loop, not next(gen, default): CircuitPython's next()
                # accepts no default argument and raises TypeError if given one.
                for entry in self.slots:
                    if entry.get("sel"):
                        self.selected = entry.get("i")
                        break
                self._redraw()
        elif kind == "ctx":
            self.context = frame
            self.cursor = 0
            # A live prompt is urgent: show its options straight away rather
            # than waiting for the user to discover them with the encoder.
            if frame.get("live"):
                self.menu_until = time.monotonic() + 3600.0
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

    def _options(self):
        options = self.context.get("o")
        return options if isinstance(options, list) else []

    def _detail_rows(self, now):
        """Two lines: either the summary, or a window onto the option list."""
        options = self._options()
        showing_menu = options and now < self.menu_until
        if not showing_menu:
            summary = str(self.context.get("sum", ""))
            if not summary:
                return ["", ""]
            # Wrap onto the two available rows rather than truncating.
            return [summary[:COLS], summary[COLS:COLS * 2]]

        # Scroll a two-line window so the cursor is always visible.
        top = 0 if self.cursor < 2 else self.cursor - 1
        rows = []
        for offset in range(2):
            index = top + offset
            if index >= len(options):
                rows.append("")
                continue
            marker = ">" if index == self.cursor else " "
            rows.append("%s %s" % (marker, str(options[index])[:COLS - 2]))
        return rows

    def _status_row(self, entry, now):
        state = entry.get("st", "empty")
        text = str(self.context.get("label") or state)
        elapsed = str(self.context.get("age") or "")

        if state == "busy":
            spin = SPINNER[int(now / SPINNER_PERIOD) % len(SPINNER)]
        elif state == "waiting":
            spin = WAITING_MARKER[int(now / BLINK_PERIOD) % len(WAITING_MARKER)]
        else:
            spin = ""

        left = text if not elapsed else "%s %s" % (text, elapsed)
        pad = COLS - len(left) - len(spin)
        return left + (" " * pad if pad > 0 else " ") + spin

    def _redraw(self):
        now = time.monotonic()
        if (now - self.last_heartbeat) > HEARTBEAT_TIMEOUT:
            self.display.show("herder offline",
                              ["host not responding", "state may be stale", "", ""])
            return

        entry = self._entry(self.selected) if self.selected is not None else None
        if entry is None or entry.get("st") == "empty":
            toast = self.toast if now < self.toast_until else ""
            self.display.show("Claude Herder",
                              ["pick a session", "", "", toast])
            return

        title = str(entry.get("nm", "?"))
        status = self._status_row(entry, now)
        detail = self._detail_rows(now)
        toast = self.toast if now < self.toast_until else ""
        self.display.show(title, [status, detail[0], detail[1], toast])

    def _paint(self, now):
        pixels = self.macropad.pixels
        if (now - self.last_heartbeat) > HEARTBEAT_TIMEOUT:
            # Pad-generated: the one thing the firmware decides for itself.
            phase = 0.35 + 0.35 * self._triangle(now, 2.0)
            for key in range(SLOT_KEYS):
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
                if key != self.selected:
                    factor *= UNSELECTED_SCALE
                if entry.get("ctl") is False:
                    factor *= MONITOR_ONLY_SCALE
            pixels[key] = scaled(colour, factor)
        pixels.show()

    @staticmethod
    def _triangle(now, period):
        """0..1..0 ramp; cheaper than sin() and smooth enough for LEDs."""
        position = (now % period) / period
        return position * 2.0 if position < 0.5 else (1.0 - position) * 2.0

    # --- input ----------------------------------------------------------------
    def _inputs(self, now):
        event = self.macropad.keys.events.get()
        if event and event.pressed:
            key = event.key_number
            if key < SLOT_KEYS:
                self.selected = key
                self.context = {}
                self.cursor = 0
                self.menu_until = 0.0
                self._redraw()
            self._say({"t": "key", "k": key, "e": "press"})

        position = self.macropad.encoder
        if position != self._encoder_last:
            delta = position - self._encoder_last
            self._encoder_last = position
            options = self._options()
            if options:
                # Local cursor movement, so the dial feels instant instead of
                # waiting on a USB round trip.
                self.cursor = max(0, min(len(options) - 1, self.cursor + delta))
                self.menu_until = now + MENU_LINGER
                self._redraw()
            self._say({"t": "enc", "d": delta})

        self.macropad.encoder_switch_debounced.update()
        if self.macropad.encoder_switch_debounced.pressed:
            options = self._options()
            if not options:
                self._say({"t": "encbtn", "e": "press"})
            elif now < self.menu_until:
                # Menu is up and a choice is highlighted: commit it.
                self._say({"t": "pick", "i": self.cursor,
                           "l": str(options[self.cursor])})
                self.menu_until = 0.0
            else:
                # Summary is showing: first press reveals the choices, so a
                # stray click can never fire an action you could not see.
                self.menu_until = now + MENU_LINGER
                self._redraw()

    def run(self):
        self._encoder_last = self.macropad.encoder
        self._redraw()
        next_frame = 0.0
        while True:
            now = time.monotonic()
            self._read()
            self._inputs(now)
            if now >= next_frame:
                self._paint(now)
                # Animated rows (spinner, blink) need repainting even when no
                # frame has arrived.
                self._redraw()
                next_frame = now + FRAME_INTERVAL


Herder().run()
