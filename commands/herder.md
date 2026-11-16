---
description: Inspect and control the Claude Herder MacroPad daemon
---

# Claude Herder

Report the current state of the herder and its pad.

Run these and summarise the result for the user:

1. **Is the daemon running?**
   `pgrep -fl "claude_herder" || echo "daemon not running"`

2. **What would the pad show right now?**
   `~/.claude/herder/venv/bin/python -m claude_herder --once`

3. **Is the pad connected?**
   `ls /dev/cu.usbmodem* 2>/dev/null; system_profiler SPUSBDataType | grep -A2 -i macropad`

4. **Recent daemon log:**
   `tail -30 ~/.claude/herder/herder.log 2>/dev/null || echo "no log"`

If the user asked to start or stop it:
- start: `launchctl kickstart -k gui/$(id -u)/io.claudeherder.daemon`
- stop:  `launchctl bootout gui/$(id -u)/io.claudeherder.daemon`

Explain what each slot means: all twelve keys (0-11) are sessions (red blink =
waiting on you, amber breathe = working, green = idle, blue = in a shell, dim =
view-only background job). There is no action row: tapping a key selects that
session, and the encoder scrolls its choices or the configured action menu, with
press to select. A slow purple pulse across all keys means the daemon is not
talking to the pad.
