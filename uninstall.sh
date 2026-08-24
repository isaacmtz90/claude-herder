#!/usr/bin/env bash
# Remove Claude Herder from the host.
set -euo pipefail
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HERDER_HOME="$CONFIG_DIR/herder"
LABEL="io.claudeherder.daemon"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "==> daemon stopped and unloaded"
echo "==> config and firmware backups kept in $HERDER_HOME"
echo "    remove them yourself when you no longer need them:"
echo "      rm -rf '$HERDER_HOME'"
echo "==> the pad still runs herder firmware; copy a backup from"
echo "    $HERDER_HOME/firmware-backup-* onto /Volumes/CIRCUITPY to revert"
