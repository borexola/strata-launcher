#!/bin/sh
# Removes Strata Launcher from your account. Your settings are kept unless you add --settings.
#
#   ./uninstall.sh              remove the program, the menu entry and the start-at-sign-in entry
#   ./uninstall.sh --settings   also remove your settings and the launcher's log
#
# Strata itself, its models and a running Strata server are not touched.
set -eu
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}"

rm -rf "$DATA/strata-launcher"
rm -f "$HOME/.local/bin/strata-launcher" "$DATA/applications/strata-launcher.desktop" \
      "$DATA/icons/hicolor/256x256/apps/strata-launcher.png" "$CONFIG/autostart/strata-launcher.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
echo "Strata Launcher is removed."
if [ "${1:-}" = "--settings" ]; then
    rm -rf "$CONFIG/strata-launcher" "$STATE/strata-launcher"
    echo "Your settings and the launcher's log are removed too."
else
    echo "Your settings are still in $CONFIG/strata-launcher (remove them with: ./uninstall.sh --settings)."
fi
