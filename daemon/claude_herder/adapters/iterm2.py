"""iTerm2: address a session by tty and write straight into its PTY.

This is the good path, and worth understanding why. AppleScript `write text`
pushes characters through the session's PTY, so a TUI receives them exactly as
typed. Because we find the session by `tty of s`, we can target session four of
six without raising a window. And it needs only per-app Automation consent --
no Accessibility, no focus theft, no clipboard clobbering.

Verified on iTerm2 3.6.6: `write text` carries control bytes, so Escape and the
CSI arrow sequences arrive as keys. That is what makes Interrupt and the
encoder work here without falling back to System Events.
"""

from __future__ import annotations

import logging

from .base import KEY_CODES, run_osascript

log = logging.getLogger(__name__)

_LIST_SESSIONS = '''
tell application "iTerm2"
  set out to ""
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        -- NB: `tab` inside a `tell application "iTerm2"` block resolves to
        -- iTerm2's tab CLASS, not the tab character. Use the code point.
        set out to out & (tty of s) & (ASCII character 9) & (name of s) & linefeed
      end repeat
    end repeat
  end repeat
  return out
end tell
'''

_WRITE_TEXT = '''
on run argv
  set targetTty to item 1 of argv
  set payload to item 2 of argv
  set wantNewline to (item 3 of argv is "yes")
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (tty of s) is targetTty then
            if wantNewline then
              write s text payload
            else
              write s text payload newline no
            end if
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run
'''

# Control sequences are assembled from ASCII code points inside AppleScript so
# nothing depends on argv's handling of raw control bytes.
_WRITE_CODES = '''
on run argv
  set targetTty to item 1 of argv
  set payload to ""
  repeat with i from 2 to (count of argv)
    set payload to payload & (ASCII character (item i of argv as integer))
  end repeat
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (tty of s) is targetTty then
            write s text payload newline no
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run
'''

_FOCUS = '''
on run argv
  set targetTty to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (tty of s) is targetTty then
            select s
            select t
            tell w to select
            activate
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run
'''


#: Discovery must finish well inside the pad's heartbeat window.
DISCOVERY_TIMEOUT = 2.5


class ITerm2Adapter:
    name = "iterm2"

    def __init__(self):
        self._ttys: frozenset[str] = frozenset()

    def can_control(self) -> bool:
        return True

    # --- discovery ------------------------------------------------------------
    def session_titles(self) -> dict[str, str]:
        """Map each iTerm2 session's tty to its tab title.

        The title is how we pair a fleet attach console with the Claude session
        it is displaying -- see titles.py.
        """
        # Kept short on purpose: this runs on the daemon's main loop, and a long
        # stall here starves the pad's heartbeat and makes it flash "offline".
        ok, out = run_osascript(_LIST_SESSIONS, timeout=DISCOVERY_TIMEOUT)
        if not ok:
            return {}
        found: dict[str, str] = {}
        for line in out.splitlines():
            tty, _, title = line.partition("\t")
            if tty.startswith("/dev/"):
                found[tty.strip()] = title.strip()
        self._ttys = frozenset(found)
        return found

    def owns(self, tty: str | None) -> bool:
        if tty is None:
            return False
        if not self._ttys:
            self.session_titles()
        return tty in self._ttys

    # --- actuation ------------------------------------------------------------
    def send_text(self, tty: str, text: str, submit: bool = False) -> bool:
        ok, out = run_osascript(_WRITE_TEXT, [tty, text, "yes" if submit else "no"])
        if ok and out == "notfound":
            log.warning("no iTerm2 session owns %s", tty)
            return False
        return ok

    def send_key(self, tty: str, key: str) -> bool:
        codes = KEY_CODES.get(key)
        if codes is None:
            log.error("unknown key %r", key)
            return False
        ok, out = run_osascript(_WRITE_CODES, [tty, *codes])
        return ok and out != "notfound"

    def focus(self, tty: str) -> bool:
        ok, out = run_osascript(_FOCUS, [tty])
        return ok and out != "notfound"
