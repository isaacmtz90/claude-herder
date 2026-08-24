"""Ghostty: no scripting dictionary, so this path is genuinely worse.

Ghostty exposes no AppleScript interface for addressing a tab, so there is no
equivalent of iTerm2's `write text`. The only known technique is:

  1. tag the tab by writing an OSC title sequence straight to its tty
     (allowed -- we own the device, and it sets the title without injecting
     input),
  2. find that tag in Ghostty's Window menu and click it via System Events,
  3. type into whatever is now focused.

That means **Accessibility permission** and a focus change, and it can lose a
race if the user moves focus mid-send. It is implemented here because Ghostty
was asked for as a first-class terminal, but iTerm2 is strictly better and is
preferred whenever a session lives there.

Prerequisite: `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, or Claude Code overwrites
our tag. The installer prints this; without it, targeting silently degrades to
"whatever is focused".
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from .base import KEY_CODES, run_osascript

log = logging.getLogger(__name__)

TAG_PREFIX = "herder:"
#: Time for a retitle to propagate into Ghostty's Window menu.
TITLE_SETTLE = 0.25
#: Time for a newly raised window to actually come forward before we type.
FOCUS_SETTLE = 0.12

# Two clicks are needed: the first selects the tab, the second raises its
# window. AXRaise is then required because `activate` can raise the wrong one.
_RAISE_TAGGED_TAB = '''
on run argv
  set wanted to item 1 of argv
  tell application "Ghostty" to activate
  delay 0.1
  tell application "System Events"
    tell process "Ghostty"
      set windowMenu to menu "Window" of menu bar 1
      set hits to (every menu item of windowMenu whose name contains wanted)
      if (count of hits) is 0 then return "notfound"
      click item 1 of hits
      delay 0.05
      click item 1 of hits
      delay 0.05
      try
        perform action "AXRaise" of window 1
      end try
    end tell
  end tell
  return "ok"
end run
'''

_TYPE_FOCUSED = '''
on run argv
  set payload to item 1 of argv
  set wantNewline to (item 2 of argv is "yes")
  tell application "System Events"
    keystroke payload
    if wantNewline then
      delay 0.5
      key code 36
    end if
  end tell
  return "ok"
end run
'''

_KEYSTROKE_CODES = '''
on run argv
  tell application "System Events"
    repeat with i from 1 to (count of argv)
      set c to (item i of argv as integer)
      if c is 27 then
        key code 53
      else if c is 13 then
        key code 36
      else if c is 91 then
        -- part of a CSI sequence; the arrow itself is sent as a key code
      else if c is 65 then
        key code 126
      else if c is 66 then
        key code 125
      else if c is 67 then
        key code 124
      else if c is 68 then
        key code 123
      else if c is 90 then
        key code 48 using shift down
      else if c is 3 then
        keystroke "c" using control down
      end if
    end repeat
  end tell
  return "ok"
end run
'''


#: Where the compiled helper may live: alongside an installed copy, or in the
#: repo during development.
HELPER_NAME = "ClaudeHerderHelper.app"


def helper_app() -> Path | None:
    """Locate the helper app that owns the Accessibility grant.

    A TCC grant attaches to a binary, so granting Accessibility to the daemon's
    interpreter would hand it to every Python program the user runs. The helper
    exists so the grant can be scoped to this tool alone.
    """
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    candidates = (
        Path(config) / "herder" / HELPER_NAME,
        Path(__file__).resolve().parents[3] / "helper" / HELPER_NAME,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def frontmost_app() -> str:
    """Which app is in front. Needs Automation consent, not Accessibility."""
    ok, out = run_osascript(
        'tell application "System Events" to get name of first process '
        'whose frontmost is true')
    return out if ok else ""


def ghostty_is_frontmost() -> bool:
    """Case-insensitive on purpose: System Events reports the process as
    "ghostty" (lowercase) even though the bundle is Ghostty.app, so an exact
    comparison silently never matches."""
    return frontmost_app().strip().lower() == "ghostty"


class GhosttyAdapter:
    """Focus-then-type. Correct where it works, fragile by construction."""

    name = "ghostty"

    def __init__(self, iterm_ttys: frozenset[str] = frozenset()):
        #: Ghostty cannot enumerate its tabs, so ownership is inferred: a
        #: Claude tty that iTerm2 does not claim is assumed to be Ghostty's.
        self._not_ours = iterm_ttys

    def can_control(self) -> bool:
        return True

    def owns(self, tty: str | None) -> bool:
        return tty is not None and tty not in self._not_ours

    def _tag(self, tty: str) -> str | None:
        """Write an OSC title sequence to the tty so the tab is findable."""
        tag = TAG_PREFIX + tty.rsplit("/", 1)[-1]
        try:
            with open(tty, "w") as handle:
                handle.write(f"\x1b]0;{tag}\x07")
        except OSError as exc:
            log.warning("cannot tag %s: %s", tty, exc)
            return None
        time.sleep(TITLE_SETTLE)
        return tag

    def focus(self, tty: str, payload: str = "") -> bool:
        """Raise the tab owning `tty`, optionally typing `payload` into it.

        Prefers the helper app so the Accessibility grant stays scoped to this
        tool. Falls back to inline AppleScript, which works only if the grant was
        given to the interpreter instead.
        """
        tag = self._tag(tty)
        if tag is None:
            return False

        helper = helper_app()
        if helper is not None:
            args = ["open", "-a", str(helper), "--args", tag]
            if payload:
                args.append(payload)
            try:
                subprocess.run(args, timeout=10, check=False,
                               capture_output=True)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("helper app failed to launch: %s", exc)
            else:
                # The helper cannot return a value (it is launched, not called),
                # so success is inferred from Ghostty coming to the front.
                time.sleep(FOCUS_SETTLE + 0.3)
                if ghostty_is_frontmost():
                    return True
                log.warning(
                    "helper ran but Ghostty did not come forward. Grant "
                    "Accessibility to %s, and check "
                    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 is set.", helper)
                return False

        ok, out = run_osascript(_RAISE_TAGGED_TAB, [tag])
        if not ok:
            log.error("Ghostty focus failed; Accessibility permission is required. "
                      "Install the helper app to scope that grant.")
            return False
        if out == "notfound":
            log.warning("no Ghostty tab titled %r; is "
                        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 set?", tag)
            return False
        time.sleep(FOCUS_SETTLE)
        return True

    def send_text(self, tty: str, text: str, submit: bool = False) -> bool:
        # When the helper is in play it can raise and type in one step, which
        # closes the window where focus could move between the two.
        if helper_app() is not None:
            if not self.focus(tty, payload=text):
                return False
            if submit:
                time.sleep(0.5)
                return self.send_key(tty, "enter")
            return True
        if not self.focus(tty):
            return False
        ok, _ = run_osascript(_TYPE_FOCUSED, [text, "yes" if submit else "no"])
        return ok

    def send_key(self, tty: str, key: str) -> bool:
        codes = KEY_CODES.get(key)
        if codes is None:
            return False
        if not self.focus(tty):
            return False
        ok, _ = run_osascript(_KEYSTROKE_CODES, [*codes])
        return ok
