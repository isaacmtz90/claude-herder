# Claude Herder

An Adafruit MacroPad RP2040 as a control surface for a fleet of Claude Code
sessions. Six keys mirror six sessions and show what each one is doing; the
bottom two rows and the encoder drive whichever session is selected.

Glance at the pad: a red blinking key means a session is blocked waiting on you.
Tap it, then approve from the pad without hunting through terminal tabs.

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

When nothing is blocking, those two middle rows show what the session last
said instead, and the dial offers **Focus / Interrupt / /compact / /clear**
(or just **Open output** for a background job that has no window).

## LED states

The pad speaks Claude's language rather than a scheduler's: a working session
reads *Simmering*, *Noodling*, *Wrangling*; a blocked one reads **Needs you**.

| Appearance | Screen says | Meaning |
|---|---|---|
| Red, fast blink | Needs you | **Waiting on you** — permission prompt, question, dialog |
| Amber, slow breathe | a rotating gerund | Working |
| Green, dim solid | Ready | Idle, waiting for input |
| Blue, solid | In shell | Dropped to a shell |
| Dim white | Unsure | State unknown — *not* the same as idle |
| Dim (any colour) | Background job: watchable, not typeable |
| Off | — | Empty slot |
| All keys pulsing purple | The daemon is not talking to the pad; **state on screen is stale** |

That last one matters. The pad generates it itself, on its own timer, because
stale state that looks live is worse than an obvious failure.

## Install

```sh
git clone <this repo> && cd claude-herder
./install.sh
```

The installer creates a venv under `~/.claude/herder`, flashes the pad, and
loads a launchd agent. It prints the three things macOS will not let a script
do for you (iTerm2 automation consent, and — only if you use Ghostty —
Accessibility and a settings change).

Also installable as a Claude Code plugin, which adds a `/herder` command for
checking status:

```sh
claude plugin marketplace add /path/to/claude-herder
claude plugin install claude-herder
```

Uninstall with `./uninstall.sh`. Your pad's previous firmware is backed up to
`~/.claude/herder/firmware-backup-*` at install time; copy it back onto
`/Volumes/CIRCUITPY` to revert.

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
declares keybinding actions for exactly this use case — `strip:next`,
`chat:attentionDown` — but [17 of its 137 declared actions have no
implementation][macropad], and those are among them. They match your keypress
and silently do nothing.

For iTerm2, the daemon finds a session by `tty` and uses AppleScript
`write text` to push characters into that session's PTY. This needs only per-app
**Automation** consent — no Accessibility, no focus stealing, no clipboard
clobbering — and it can address session four of six without raising a window.
Control bytes survive it, verified on iTerm2 3.6.6, so Escape and the arrow
sequences work here too.

## Fleet-attached consoles

With `remoteControlAtStartup` enabled, typing `claude` often *attaches* your
terminal to a daemon-hosted background worker rather than starting a local
session. That splits the problem in two: the registry entry that has the state
belongs to a worker with no terminal, and the terminal belongs to an attach
client the registry never lists.

The daemon pairs them by **terminal title**. Claude Code sets the tab title to
the session name (which is why `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` exists) and
iTerm2 publishes each tab's title, so matching one against the other joins the
halves using only supported surfaces.

Matching is deliberately conservative — an ambiguous title is refused rather
than guessed, and elision is treated as directional, because a wrong join would
send an Approve to the wrong session. Consoles that cannot be identified still
appear and are still controllable; they just show `unknown` rather than a state
invented for them.

## Answering permission prompts

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

**Approve sends `1`** (no Enter). "Yes" is always the first option, so this is
safe, and it was verified end to end: the dialog closed and the command ran.

**Deny sends Escape, not a digit.** This matters more than it looks. Some tools
show the two options above; others show three, with `2` being *"Yes, and don't
ask again"*. A fixed deny digit would therefore **permanently approve** the
exact thing you meant to refuse, on some prompts and not others. Escape cancels
every shape of the dialog, and the dialog itself documents it. Also verified end
to end: dialog dismissed, command not run.

Both are configurable. A leading `@` in `approve`/`deny` means a key name rather
than literal text, so `deny = "@escape"` is the shipped default and
`deny = "2"` is available if you want to opt into digit-based denial.

## Known limits

- **VS Code-hosted sessions are excluded.** They run on stdio with no
  controlling terminal, cannot receive a keystroke, and publish no `status`
  field. A key for them could neither report nor act.
- **Background jobs are watchable, not typeable.** A daemon-hosted worker owns a
  pseudo-terminal that no window displays, so there is nowhere to send a
  keystroke. Terminal attribution is positive — walked up the process tree —
  precisely so a windowless pty is never mistaken for a Ghostty tab. Their keys
  are dimmer and the dial offers only "Open output".
- **A blocked session's options are read off the screen.** The registry reports
  *that* a session is waiting, never what the choices are, so the option list is
  parsed from the terminal pane. That is scraping: it degrades to the safe
  action menu rather than guessing, because a mis-parsed list would put the
  wrong answer under your thumb.
- **Ghostty works up to the Accessibility grant, which only you can give.**
  Verified against Ghostty 1.3.1: the adapter correctly claims a Ghostty tty and
  declines an iTerm2 one, OSC title tagging succeeds (that needs no permission),
  and `focus()` fails cleanly with an actionable message rather than crashing.
  The remaining step — clicking the tagged tab in Ghostty's Window menu — needs
  **Accessibility**, which macOS will not let a script grant. Grant it to
  `~/.claude/herder/ClaudeHerderHelper.app` and nothing else: that bundle exists
  so the permission is scoped to this tool instead of to your Python
  interpreter, which would otherwise inherit it for every script you ever run.
  iTerm2 needs no Accessibility at all; prefer it where you have the choice.
- **Setting `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` for Ghostty costs
  fleet-attached consoles their state**, because that title is the join key.
- **Permission answers are version-coupled**, and one of them is a trap. See
  "Answering permission prompts" below.
- **The registry is an internal file format.** Everything that reads it degrades
  to `unknown` rather than raising, but a future Claude Code could change it.

## Development

```sh
python3 -m venv .venv && ./.venv/bin/pip install -e . pytest
./.venv/bin/python -m pytest            # no hardware needed
./.venv/bin/python -m claude_herder --once      # print a snapshot and exit
./.venv/bin/python -m claude_herder -v --dry-run  # log actions, send nothing
./.venv/bin/python firmware/flash.py    # reflash the pad
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
CircuitPython board — the channel their hardware lacked is the whole feature.

[onikan]: https://github.com/onikan27/claude-code-monitor
[macropad]: https://github.com/juaninamilliom/claude-code-macropad
[bruce]: https://github.com/bruceyxli/claude-code-monitor
[ccbot]: https://github.com/six-ddc/ccbot
[ccmon]: https://github.com/beyrulibryam/cc-monitor
[squad]: https://github.com/smtg-ai/claude-squad
[rosehill]: https://github.com/danielrosehill/Claude-Macropad-V2

## Licence

MIT.
