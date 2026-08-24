#!/usr/bin/env python3
"""Install pad firmware, by file copy when possible and over the REPL when not.

Drag-and-drop is the normal path, but a pad whose CIRCUITPY volume is not
mounted cannot be installed that way -- and that is the state this project's
own pad was found in. So we try, in order:

  1. copy to a mounted CIRCUITPY volume
  2. hard-reset the board over the REPL and look again (a soft reboot does not
     re-enumerate USB, so an ejected volume stays missing)
  3. write the files over the REPL, which is slow but always available

CircuitPython can only write to its own filesystem while the host has *not*
mounted CIRCUITPY, so path 3 works precisely when path 1 does not.
"""

from __future__ import annotations

import argparse
import base64
import glob
import os
import shutil
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

VID, PID = 0x239A, 0x8108
FILES = ("boot.py", "code.py")
CHUNK = 384          # base64 chars per REPL line; well under the line limit
PROMPT = b">>>"


def circuitpy_volume() -> Path | None:
    for candidate in glob.glob("/Volumes/*"):
        path = Path(candidate)
        if (path / "boot_out.txt").exists():
            return path
    return None


def pad_ports() -> list[str]:
    found = [p.device for p in list_ports.comports() if p.vid == VID and p.pid == PID]
    return sorted(found)


def console_port() -> str | None:
    """The REPL is the lowest-numbered interface."""
    ports = pad_ports()
    return ports[0] if ports else None


class Repl:
    def __init__(self, port: str):
        self.serial = serial.Serial(port, 115200, timeout=0.2, write_timeout=5)
        time.sleep(0.3)

    def close(self):
        try:
            self.serial.close()
        except Exception:
            pass

    def read_until_prompt(self, timeout: float = 5.0) -> bytes:
        deadline = time.time() + timeout
        buf = b""
        while time.time() < deadline:
            buf += self.serial.read(4096)
            if buf.rstrip().endswith(PROMPT):
                break
        return buf

    def interrupt(self) -> bool:
        """Stop the running program and get a prompt."""
        self.serial.write(b"\x03")          # Ctrl-C
        time.sleep(0.3)
        self.serial.read(4096)
        self.serial.write(b"\r")            # any key -> REPL
        out = self.read_until_prompt()
        return out.rstrip().endswith(PROMPT)

    def run(self, line: str, timeout: float = 5.0) -> bytes:
        self.serial.write(line.encode("utf-8") + b"\r")
        return self.read_until_prompt(timeout)

    def put(self, name: str, data: bytes) -> None:
        encoded = base64.b64encode(data).decode("ascii")
        self.run("import binascii")
        self.run(f"_f = open('/{name}', 'wb')")
        for offset in range(0, len(encoded), CHUNK):
            piece = encoded[offset:offset + CHUNK]
            out = self.run(f"_f.write(binascii.a2b_base64('{piece}'))", timeout=8)
            if b"Traceback" in out or b"Error" in out:
                raise RuntimeError(f"write failed for {name}: {out[-200:]!r}")
        self.run("_f.close()")
        # Verify rather than assume: a truncated code.py bricks the pad quietly.
        out = self.run(f"import os; print('SIZE', os.stat('/{name}')[6])")
        expected = str(len(data)).encode()
        if b"SIZE " + expected not in out.replace(b"\r", b""):
            raise RuntimeError(
                f"{name} verify failed; expected {len(data)} bytes, got {out[-120:]!r}")
        print(f"    wrote and verified /{name} ({len(data)} bytes)")


def copy_to_volume(source: Path, volume: Path) -> None:
    for name in FILES:
        shutil.copy2(source / name, volume / name)
        print(f"    copied {name} -> {volume}")


def install_over_repl(source: Path, port: str, *, reset: bool = True) -> None:
    repl = Repl(port)
    try:
        if not repl.interrupt():
            raise RuntimeError(
                "could not get a REPL prompt; close any serial monitor and retry")
        # Stop auto-reload thrashing the board between the two file writes.
        repl.run("import supervisor; supervisor.runtime.autoreload = False")
        # CircuitPython holds its filesystem read-only whenever USB mass storage
        # is enabled -- which stays true even when the host has ejected the
        # volume, leaving neither side able to write. Remounting hands write
        # access back to the board for the duration of the install.
        out = repl.run("import storage; storage.remount('/', readonly=False)")
        if b"Error" in out or b"Traceback" in out:
            out = repl.run("storage.remount('/', readonly=False, "
                           "disable_concurrent_write_protection=True)")
            if b"Traceback" in out:
                raise RuntimeError(f"cannot make the pad filesystem writable: {out[-200:]!r}")
        for name in FILES:
            repl.put(name, (source / name).read_bytes())
        if reset:
            print("    hard-resetting the board to apply boot.py")
            repl.serial.write(b"import microcontroller; microcontroller.reset()\r")
            time.sleep(1.0)
    finally:
        repl.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).parent),
                        help="directory holding boot.py and code.py")
    parser.add_argument("--repl-only", action="store_true",
                        help="skip the volume path even if CIRCUITPY is mounted")
    args = parser.parse_args()
    source = Path(args.source)

    for name in FILES:
        if not (source / name).exists():
            print(f"error: {source/name} not found", file=sys.stderr)
            return 2

    if not pad_ports():
        print("error: no Adafruit MacroPad RP2040 found (VID:PID 239A:8108).",
              file=sys.stderr)
        print("       Is it plugged in?", file=sys.stderr)
        return 1

    volume = None if args.repl_only else circuitpy_volume()
    if volume:
        print(f"[1/1] CIRCUITPY mounted at {volume}")
        copy_to_volume(source, volume)
        print("done. The pad will reload automatically.")
        return 0

    port = console_port()
    print(f"[1/2] no CIRCUITPY volume; using the REPL on {port}")
    install_over_repl(source, port)

    print("[2/2] re-checking for a CIRCUITPY volume after reset")
    for _ in range(10):
        time.sleep(1.0)
        if (volume := circuitpy_volume()):
            print(f"    CIRCUITPY now mounted at {volume}; "
                  "future updates can be a plain file copy")
            break
    else:
        print("    still not mounted -- fine, but future updates will use the REPL")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
