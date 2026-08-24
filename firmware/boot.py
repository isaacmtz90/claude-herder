"""Runs once at power-on, before code.py, to shape the USB device.

The second CDC channel is the whole point: protocol traffic must not share the
console channel with the REPL and auto-reload, or a stray frame can interrupt
the running program and stale state will look live.
"""

import storage
import usb_cdc

# console -> the REPL (kept, so the pad stays debuggable and installable)
# data    -> the herder protocol, a separate /dev/cu.usbmodem* device
usb_cdc.enable(console=True, data=True)

# Keep CIRCUITPY mountable so future firmware updates are a file copy rather
# than a slow write over the REPL.
storage.enable_usb_drive()

# USB HID is deliberately left enabled but unused. The daemon does all typing
# (a keyboard cannot target session 4 of 6), but keeping the interface means a
# hotkey page can be added later without reflashing the USB descriptor.
