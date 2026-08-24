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
  system_profiler SPUSBDataType 2>/dev/null | grep -qi "Macropad RP2040"
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
if [[ -f "$HERDER_HOME/config.toml" ]]; then
  info "keeping your existing config.toml"
else
  info "writing default config to $HERDER_HOME/config.toml"
  cp "$REPO/config.example.toml" "$HERDER_HOME/config.toml"
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
    warn "and save it first (see README: reading firmware over the REPL)."
  fi
  info "flashing herder firmware"
  "$VENV/bin/python" "$REPO/firmware/flash.py" --source "$REPO/firmware"
else
  warn "skipping firmware flash"
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

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
info "daemon loaded. Log: $HERDER_HOME/herder.log"

# --- 6. the parts that cannot be automated ------------------------------------
echo
echo "------------------------------------------------------------------"
echo "Installed. Three things need you, because macOS will not let a"
echo "script grant them:"
echo
echo "1. iTerm2 automation consent"
echo "   The first time the herder types into a session, macOS asks"
echo "   '... wants to control iTerm2'. Approve it. Until then the state"
echo "   LEDs work but the control keys do nothing."
echo
echo "2. Ghostty needs Accessibility (iTerm2 does not)"
echo "   Ghostty has no scripting interface, so reaching a tab needs"
echo "   System Settings > Privacy & Security > Accessibility, adding:"
echo "     $VENV/bin/python"
echo "   Prefer iTerm2 wherever you have the choice."
echo
echo "3. Ghostty also needs Claude Code to stop retitling tabs:"
echo '     "env": { "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1" }'
echo "   TRADE-OFF: that title is what pairs a fleet-attached console with"
echo "   its session, so setting it costs those consoles their state LEDs."
echo "   Only set it if you actually use Ghostty."
echo
echo "Check it:  $VENV/bin/python -m claude_herder --once"
echo "------------------------------------------------------------------"
