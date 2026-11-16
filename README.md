<h1 align="center">Claude Herder</h1>

<p align="center">
  <b>An Adafruit MacroPad RP2040 as a control surface for a fleet of Claude Code sessions.</b><br>
  Twelve keys, twelve sessions. The LED says what each one is doing; the dial answers it.
</p>

<p align="center">
  <img alt="Licence: MIT" src="https://img.shields.io/badge/licence-MIT-blue">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="Platform: macOS" src="https://img.shields.io/badge/platform-macOS-lightgrey">
  <img alt="Firmware: CircuitPython" src="https://img.shields.io/badge/firmware-CircuitPython-652F8E">
</p>

---

Glance at the pad: a red blinking key means a session is blocked waiting on you.
Tap it, turn the dial to the answer, press. No hunting through terminal tabs.

```
 ┌─────────┬─────────┬─────────┐
 │ session │ session │ session │   All twelve keys are sessions.
 ├─────────┼─────────┼─────────┤   Tap to select (and raise its window).
 │ session │ session │ session │   The LED is its state.
 ├─────────┼─────────┼─────────┤
 │ session │ session │ session │   There is no action row: everything
 ├─────────┼─────────┼─────────┤   you *do* goes through the dial.
 │ session │ session │ session │
 └─────────┴─────────┴─────────┘
   dial: turn to move through the options, press to choose
```

The screen, 21 columns by 5 rows:

```
 ┌─────────────────────────────┐
 │ detect adafruit keyboard in │  inverted bar: the selected session
 │ Simmering  18m          (*) │  what it is doing, for how long, spinner
 │ > 1. Yes                    │  the choices it is blocked on...
 │   2. No                     │
 │ approved                    │  what your last press did
 └─────────────────────────────┘
```

## Contents

- [What the LEDs mean](#what-the-leds-mean)
- [Requirements](#requirements)
- [Install](#install)
- [Using it](#using-it)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Known limits](#known-limits)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Prior art](#prior-art)

## What the LEDs mean

The pad speaks Claude's language rather than a scheduler's: a working session
reads *Simmering*, *Noodling*, *Wrangling*; a blocked one reads **Needs you**.

| Key | Screen says | Meaning |
|---|---|---|
| 🔴 Red, fast blink | Needs you | **Waiting on you**: permission prompt, question, dialog |
| 🟠 Amber, slow breathe | a rotating gerund | Working |
| 🟢 Green, dim solid | Ready | Idle, waiting for input |
| 🔵 Blue, solid | In shell | Dropped to a shell |
| ⚪ Dim white | Unsure | State unknown, which is *not* the same as idle |
| Any colour, dimmer | (as above) | Background job: watchable, not typeable |
| Off | (nothing) | Empty slot |
| 🟣 All keys pulsing purple | host not responding | The daemon is not talking to the pad; **what you see is stale** |

That last one matters. The pad generates it itself, on its own timer, after
eight seconds of host silence, because stale state that looks live is worse than
an obvious failure.

## Requirements

- An [Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) running
  CircuitPython, on a USB **data** cable
- macOS (the terminal adapters are AppleScript)
- Python 3.11 or newer
- Claude Code, in iTerm2 (recommended) or Ghostty

## Install

```sh
git clone https://github.com/isaacmtz90/claude-herder.git && cd claude-herder
./install.sh
```

The installer creates a venv under `~/.claude/herder`, flashes the pad, and
loads a launchd agent. It then prints the three things macOS will not let a
script do for you: iTerm2 automation consent and, only if you use Ghostty,
an Accessibility grant and a settings change.

Your pad's previous firmware is backed up to
`~/.claude/herder/firmware-backup-*` before flashing; copy it back onto
`/Volumes/CIRCUITPY` to revert. Uninstall with `./uninstall.sh`.

Also installable as a Claude Code plugin, which adds a `/herder` command for
checking status:

```sh
claude plugin marketplace add /path/to/claude-herder
claude plugin install claude-herder
```

## Using it

1. **Tap a key** to select that session. Its terminal window is raised and the
   screen shows what it is doing.
2. **If it is blocked**, the screen lists the choices it is waiting on. Turn the
   dial to one and press.
3. **If it is not blocked**, the screen shows what the session last said, and
   the dial offers the action menu: **Focus / Interrupt / /compact / /clear**
   (or just **Open output** for a background job that has no window).

Picking a command **runs** it: the text is typed and Enter follows. The dial
already asks twice (one press to reveal the choices, one to commit), and that
is the confirmation. Set `submit = false` on an action to have it typed and
left for you instead.

Actions that type or press Enter first check that no dialog is on screen, and
refuse if one is. An Enter delivered into an open dialog answers *that dialog*.
This was observed live: a workspace-trust prompt swallowed a `/compact` and
quit the session by confirming its highlighted "No, exit". **Focus** is exempt:
it sends no input, and raising the window is exactly what you want when
something unexpected is asking.

## Configuration

Everything lives in `~/.claude/herder/config.toml`; `config.example.toml` is the
annotated default. The dial's action menu is a list you can edit:

```toml
[[action]]
label = "Focus"
builtin = "focus"          # raise this session's terminal window

[[action]]
label = "/compact"
text = "/compact"          # typed into the session, then Enter
```

Restart the daemon to pick up changes:

```sh
launchctl kickstart -k gui/$(id -u)/io.claudeherder.daemon
```

## How it works

```
~/.claude/sessions/*.json ──┐
                            ▼
       herder daemon (python, launchd)
        registry → liveness → slots → renderer → serial
                            ▲
                       key events
                            │
        actions → iTerm2 | Ghostty | background job
                            │
                   USB CDC data channel
                            ▼
                   pad firmware (renders)
```

**The firmware is deliberately dumb.** It paints what it is told and reports
what is pressed. All logic is host-side, so iterating never means reflashing and
every machine runs identical firmware. Protocol traffic rides a *second* USB CDC
channel so it cannot collide with the REPL.

**State comes from Claude Code's own session registry**, not from hooks. Claude
Code writes `~/.claude/sessions/<pid>.json` per live session and rewrites it on
every transition, including a `status` field (`busy`/`idle`/`waiting`/`shell`)
and a `waitingFor` reason. Hooks were the obvious choice and are the wrong one:
hooks load only when a session *starts*, so a hooks-based herder is blind to
every console that was already open when you installed it.

**Control goes through the terminal, not through Claude Code.** Claude Code
declares keybinding actions for exactly this use case (`strip:next`,
`chat:attentionDown`), but [17 of its 137 declared actions have no
implementation][macropad], and those are among them. They match your keypress
and silently do nothing.

For iTerm2, the daemon finds a session by `tty` and uses AppleScript
`write text` to push characters into that session's PTY. This needs only per-app
**Automation** consent (no Accessibility, no focus stealing, no clipboard
clobbering) and it can address session four of six without raising a window.
Control bytes survive it, verified on iTerm2 3.6.6, so Escape and the arrow
sequences work here too.

**Keys are sticky.** A session keeps its key for as long as it lives, and the
layout is saved so a daemon restart does not reshuffle it. The point of hardware
is muscle memory; a pad that rearranges itself is worse than no pad.

### Fleet-attached consoles

With `remoteControlAtStartup` enabled, typing `claude` often *attaches* your
terminal to a daemon-hosted background worker rather than starting a local
session. That splits the problem in two: the registry entry that has the state
belongs to a worker with no terminal, and the terminal belongs to an attach
client the registry never lists.

The daemon pairs them by **terminal title**. Claude Code sets the tab title to
the session name (which is why `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` exists) and
iTerm2 publishes each tab's title, so matching one against the other joins the
halves using only supported surfaces.

Matching is deliberately conservative. An ambiguous title is refused rather
than guessed, and elision is treated as directional, because a wrong join would
send an Approve to the wrong session. Consoles that cannot be identified still
appear and are still controllable; they just show `unknown` rather than a state
invented for them.

### Answering permission prompts

Verified by triggering a real prompt on CLI **2.1.241**:

```
 Bash command
   echo herder-probe-three
 Permission rule Bash requires confirmation for this command.
 Do you want to proceed?
 > 1. Yes
   2. No
 Esc to cancel - Tab to amend - ctrl+e to explain
```

**Answers move the dialog's own cursor with arrow keys, then press Enter. They
never type an option number.** Typing the number looks simpler and is wrong in
general: a family of dialogs (workspace trust, bypass-permissions, IDE prompts)
renders with indexes hidden, where a digit is silently ignored, and which rows
a dialog offers varies with feature flags. Some tools show the two options
above; others show three, with `2` being *"Yes, and don't ask again"*. A fixed
position is therefore not a fixed answer, and a fixed "deny digit" would
**permanently approve** the exact thing you meant to refuse on some prompts.

Three guards follow from that:

- **A pick is re-checked against a fresh read of the dialog.** Arrows are
  relative, so the pad sends the *label* you chose and the daemon locates that
  label again before moving. If the prompt changed in the meantime, it refuses
  and says so.
- **If the cursor is not visible in the pane, the pad refuses to act** rather
  than guess where it is.
- **Unnumbered dialogs are not offered on the pad at all.** Their rows cannot be
  told from the prose above them with confidence, and the family includes
  workspace trust, which is a security decision. Those fall back to the action
  menu, where Focus raises the window so you can read and answer it properly.

The `approve` and `deny` values under `[permissions]` in the config are left
over from the earlier digit-based design. They are still parsed, but nothing
acts on them today.

## Known limits

- **VS Code-hosted sessions are excluded.** They run on stdio with no
  controlling terminal, cannot receive a keystroke, and publish no `status`
  field. A key for them could neither report nor act.
- **Background jobs are watchable, not typeable.** A daemon-hosted worker owns a
  pseudo-terminal that no window displays, so there is nowhere to send a
  keystroke. Terminal attribution is positive (walked up the process tree)
  precisely so a windowless pty is never mistaken for a Ghostty tab. Their keys
  are dimmer and the dial offers only "Open output".
- **A blocked session's options are read off the screen, for now.** The
  registry reports *that* a session is waiting, never what the choices are. A
  pending question exposes its options structurally in the job state and that
  is used when available; otherwise the list is parsed from the pane. Scraping
  degrades to the safe action menu rather than guessing, because a mis-parsed
  list would put the wrong answer under your thumb.

  **The better route, not yet built:** the `PermissionRequest` hook receives
  `permission_suggestions` as data *and can answer the prompt* by writing a
  decision to stdout, with a 600 s default timeout, which is ample for a dial
  round-trip. That would remove the screen-parsing failure modes entirely. It
  is the recommended next step.
- **A single missed read frees a key.** A session whose registry file is caught
  mid-write, or that one `ps` call fails to see, leaves the pad for that poll
  and may come back on a different key. A two-strike counter exists
  (`liveness.Strikes`) but is not wired into the loop yet.
- **A failed title lookup shows a console twice.** While iTerm2 titles are
  unavailable, a fleet-attached console cannot be paired with its session, so
  it appears as its own `unknown` key next to the session's key until the next
  successful lookup (retried every 60 s).
- **Nothing leaves the pad while its process is alive.** A session that
  finished an hour ago keeps its key until it exits. A thirteenth session is
  simply not shown; there is no priority for sessions that are busy or waiting.
- **Ghostty works up to the Accessibility grant, which only you can give.**
  Verified against Ghostty 1.3.1: the adapter correctly claims a Ghostty tty and
  declines an iTerm2 one, OSC title tagging succeeds (that needs no permission),
  and `focus()` fails cleanly with an actionable message rather than crashing.
  The remaining step, clicking the tagged tab in Ghostty's Window menu, needs
  **Accessibility**, which macOS will not let a script grant. Grant it to
  `~/.claude/herder/ClaudeHerderHelper.app` and nothing else: that bundle exists
  so the permission is scoped to this tool instead of to your Python
  interpreter, which would otherwise inherit it for every script you ever run.
  iTerm2 needs no Accessibility at all; prefer it where you have the choice.
- **Setting `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` for Ghostty costs
  fleet-attached consoles their state**, because that title is the join key.
- **Prompt handling is version-coupled.** It was verified against CLI 2.1.241;
  see [Answering permission prompts](#answering-permission-prompts).
- **The registry is an internal file format.** Everything that reads it degrades
  to `unknown` rather than raising, but a future Claude Code could change it.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| All keys pulse purple; log says `no MacroPad found` | The pad is not enumerating on USB. Usually the cable. | Reseat or swap the cable (it must carry data, not just power), or press the pad's reset button. The daemon retries every 10 s, so nothing needs restarting. |
| Pad is plugged in but still not found | Port auto-detection missed it | Set `CLAUDE_HERDER_PORT` to the pad's `/dev/cu.usbmodem*` data port. The daemon runs under launchd, so add it to `EnvironmentVariables` in `~/Library/LaunchAgents/io.claudeherder.daemon.plist`; a shell `export` is not seen |
| Consoles show **Unsure** instead of a state | iTerm2 automation consent is missing | Run `~/.claude/herder/venv/bin/python -m claude_herder --once` from a terminal and approve the macOS prompt |
| Log says `another herder is already running` | Two daemons would fight over the serial port | Stop the extra one; launchd keeps exactly one alive |

Useful commands:

```sh
tail -f ~/.claude/herder/herder.log                          # what the daemon is doing
~/.claude/herder/venv/bin/python -m claude_herder --once     # what the pad should show
launchctl kickstart -k gui/$(id -u)/io.claudeherder.daemon   # restart the daemon
```

## Development

```sh
python3 -m venv .venv && ./.venv/bin/pip install -e . pytest
./.venv/bin/python -m pytest                      # no hardware needed
./.venv/bin/python -m claude_herder --once        # print a snapshot and exit
./.venv/bin/python -m claude_herder -v --dry-run  # log actions, send nothing
./.venv/bin/python firmware/flash.py              # reflash the pad
```

Everything except the adapters and the serial link runs headless: the registry,
liveness, slot assignment, rendering, protocol framing and title matching are
pure functions over fixtures.

## Prior art

This project reimplements ideas rather than copying code, and the licence column
is why.

| Project | Licence | Used for |
|---|---|---|
| [onikan27/claude-code-monitor][onikan] | MIT | tty-addressed AppleScript session lookup; the Ghostty technique |
| [juaninamilliom/claude-code-macropad][macropad] | MIT | attention-marker semantics; the macOS minefield it documents |
| [bruceyxli/claude-code-monitor][bruce] | MIT | blocking `PreToolUse` approval (not yet implemented here) |
| [six-ddc/ccbot][ccbot] | MIT | the text-then-Enter timing gap |
| [beyrulibryam/cc-monitor][ccmon] | none | ideas only: two-strike liveness, never write to stdout in a hook |
| [smtg-ai/claude-squad][squad] | AGPL-3.0 | ideas only, not vendored |
| [danielrosehill/Claude-Macropad-V2][rosehill] | none | requirements: the error state, canned-key defaults, the auto-submit question |

`claude-code-macropad` deserves particular credit: its README explains that LED
feedback was impossible on *its* pad because the vendor's config app exposes no
host-to-device channel. That constraint is the reason this project uses a
CircuitPython board: the channel their hardware lacked is the whole feature.

[onikan]: https://github.com/onikan27/claude-code-monitor
[macropad]: https://github.com/juaninamilliom/claude-code-macropad
[bruce]: https://github.com/bruceyxli/claude-code-monitor
[ccbot]: https://github.com/six-ddc/ccbot
[ccmon]: https://github.com/beyrulibryam/cc-monitor
[squad]: https://github.com/smtg-ai/claude-squad
[rosehill]: https://github.com/danielrosehill/Claude-Macropad-V2

## Licence

[MIT](LICENSE).

Not affiliated with or endorsed by Anthropic or Adafruit. "Claude" and
"MacroPad" are trademarks of their respective owners.
