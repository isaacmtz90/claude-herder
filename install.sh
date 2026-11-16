#!/usr/bin/env bash
# Install Claude Herder: firmware on the pad, daemon under launchd.
#
# Safe to re-run. Everything host-side lives under $HERDER_HOME, so uninstalling
# is a directory removal plus one launchctl call.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HERDER_HOME="$CONFIG_DIR/herder"
VENV="$HERDER_HOME/venv"
LABEL="io.claudeherder.daemon"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

pad_attached() {
  # ioreg, not system_profiler: the latter intermittently reports nothing for
  # this device on this machine, which silently skipped both the firmware
  # backup and the flash.
  ioreg -p IOUSB -w0 -l 2>/dev/null | grep -q '"idProduct" = 33032' && return 0
  # Fall back to the CDC interfaces actually appearing.
  ls /dev/cu.usbmodem* >/dev/null 2>&1
}

# --- 1. preflight -------------------------------------------------------------
info "checking prerequisites"
[[ "$(uname -s)" == "Darwin" ]] || die "macOS only: the terminal adapters are AppleScript."

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || die "python3 not found."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || die "Python 3.11+ required (found $("$PY" -V 2>&1))."
info "python: $("$PY" -V 2>&1)"

if pad_attached; then
  info "MacroPad RP2040 detected"
else
  warn "no MacroPad RP2040 on USB. Continuing, but the firmware step is skipped."
  warn "Plug the pad in and re-run this script to finish."
fi

# --- 2. host-side install -----------------------------------------------------
info "installing into $HERDER_HOME"
mkdir -p "$HERDER_HOME"
[[ -d "$VENV" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$REPO"

# --- 3. config ----------------------------------------------------------------
if [[ ! -f "$HERDER_HOME/config.toml" ]]; then
  info "writing default config to $HERDER_HOME/config.toml"
  cp "$REPO/config.example.toml" "$HERDER_HOME/config.toml"
elif grep -q '^\[\[canned\]\]' "$HERDER_HOME/config.toml"; then
  # The old [[canned]] table predates the dial. Left alone it would replace the
  # whole action menu with three text entries -- losing Focus and Interrupt --
  # and keep slash commands unsubmitted, which is the "typed but never ran"
  # behaviour. Migrate, keeping a backup of whatever was there.
  BACKUP="$HERDER_HOME/config.toml.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$HERDER_HOME/config.toml" "$BACKUP"
  cp "$REPO/config.example.toml" "$HERDER_HOME/config.toml"
  warn "migrated config.toml to the new [[action]] menu format"
  warn "your previous file is at $BACKUP"
else
  info "keeping your existing config.toml"
fi

# --- 4. firmware --------------------------------------------------------------
if pad_attached; then
  BACKUP="$HERDER_HOME/firmware-backup-$(date +%Y%m%d-%H%M%S)"
  if [[ -d /Volumes/CIRCUITPY ]]; then
    info "backing up the pad's current firmware to $BACKUP"
    mkdir -p "$BACKUP"
    cp /Volumes/CIRCUITPY/*.py "$BACKUP/" 2>/dev/null || true
  else
    warn "CIRCUITPY is not mounted, so existing firmware cannot be backed up"
    warn "automatically. If this pad runs a keymap you care about, stop now"
    warn "and save it first: mount CIRCUITPY and copy its .py files somewhere safe."
  fi
  info "flashing herder firmware"
  "$VENV/bin/python" "$REPO/firmware/flash.py" --source "$REPO/firmware"
else
  warn "skipping firmware flash"
fi

# --- 4b. Accessibility helper -------------------------------------------------
# A TCC grant attaches to a binary, so granting Accessibility to the daemon's
# interpreter would hand it to every Python program on the machine. The helper
# app exists so that grant can be scoped to this tool alone.
info "building the Accessibility helper app"
if command -v osacompile >/dev/null; then
  rm -rf "$HERDER_HOME/ClaudeHerderHelper.app"
  osacompile -o "$HERDER_HOME/ClaudeHerderHelper.app" \
    "$REPO/helper/ClaudeHerderHelper.applescript"
  info "helper installed at $HERDER_HOME/ClaudeHerderHelper.app"
else
  warn "osacompile not found; Ghostty support will need Accessibility granted"
  warn "to the interpreter instead, which is a much broader permission."
fi

# --- 5. launchd ---------------------------------------------------------------
info "installing the launchd agent"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>-m</string>
    <string>claude_herder</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$HERDER_HOME/herder.log</string>
  <key>StandardErrorPath</key><string>$HERDER_HOME/herder.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string>
  </dict>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST_EOF

# Idempotent (re-)load. bootstrap fails with a generic I/O error if the label is
# still registered, so wait for the old one to actually go away, and fall back to
# kickstart if it is somehow still there.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
  info "daemon loaded. Log: $HERDER_HOME/herder.log"
elif launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null; then
  info "daemon restarted in place. Log: $HERDER_HOME/herder.log"
else
  warn "could not load the launchd agent. Load it by hand with:"
  warn "  launchctl bootstrap gui/$(id -u) $PLIST"
fi

# --- 5b. trigger the Automation prompt while a human is present ---------------
# A launchd agent often cannot raise a TCC prompt at all, so the grant has to be
# requested from an interactive session. TCC keys on the binary, so approving it
# here is what lets the background daemon read terminal titles later.
info "checking iTerm2 automation access (approve the prompt if one appears)"
if "$VENV/bin/python" - <<'PROBE' 2>/dev/null
import sys
sys.path.insert(0, "")
from claude_herder.adapters.iterm2 import ITerm2Adapter
sys.exit(0 if ITerm2Adapter().session_titles() else 1)
PROBE
then
  info "iTerm2 automation works: fleet-attached consoles will show real state"
else
  warn "could not read iTerm2 titles yet."
  warn "Run this once from a terminal and approve the macOS prompt:"
  warn "  $VENV/bin/python -m claude_herder --once"
  warn "Until then, fleet-attached consoles show 'unknown' instead of a state."
fi

# --- 6. the parts that cannot be automated ------------------------------------
echo
echo "------------------------------------------------------------------"
echo "Installed. Three things need you, because macOS will not let a"
echo "script grant them:"
echo
echo "1. iTerm2 automation consent"
echo "   macOS asks '... wants to control iTerm2' the first time. Approve it."
echo "   A launchd agent often cannot show that prompt itself, so if the check"
echo "   above failed, run this from a terminal once and approve:"
echo "     $VENV/bin/python -m claude_herder --once"
echo "   Registry-backed state LEDs work regardless; what needs this grant is"
echo "   typing into sessions and reading titles (which gives fleet-attached"
echo "   consoles their state instead of 'unknown')."
echo
echo "2. Ghostty needs Accessibility (iTerm2 does not)"
echo "   Ghostty has no scripting interface, so reaching a tab needs"
echo "   System Settings > Privacy & Security > Accessibility. Add ONLY:"
echo "     $HERDER_HOME/ClaudeHerderHelper.app"
echo "   That bundle exists so the grant stays scoped to this tool rather"
echo "   than going to your Python interpreter. Prefer iTerm2 regardless:"
echo "   it needs no Accessibility at all."
echo
echo "3. Ghostty also needs Claude Code to stop retitling tabs:"
echo '     "env": { "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1" }'
echo "   TRADE-OFF: that title is what pairs a fleet-attached console with"
echo "   its session, so setting it costs those consoles their state LEDs."
echo "   Only set it if you actually use Ghostty."
echo
echo "Check it:  $VENV/bin/python -m claude_herder --once"
echo "------------------------------------------------------------------"
