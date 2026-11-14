#!/bin/sh
# Installs Strata Launcher for you only (no sudo). Run it again to update: your settings are kept.
#
#   ./install.sh
#
# From a release download (a folder with the ready-made program beside this file) it copies that program.
# From the source code it builds a small Python environment of its own instead (needs Python 3.10 or newer).
#
# What it puts where:
#   ~/.local/share/strata-launcher/                          the program
#   ~/.local/bin/strata-launcher                             the command
#   ~/.local/share/applications/strata-launcher.desktop      the entry in your app menu
#   ~/.local/share/icons/hicolor/256x256/apps/strata-launcher.png
# Your settings live in ~/.config/strata-launcher and are never touched by this script.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/strata-launcher"
BIN="$HOME/.local/bin"
DESKTOP="$DATA/applications/strata-launcher.desktop"
ICON_DIR="$DATA/icons/hicolor/256x256/apps"

say() { printf '%s\n' "$*"; }
die() { printf 'Not installed: %s\n' "$*" >&2; exit 1; }

mkdir -p "$APP" "$BIN" "$ICON_DIR" "$(dirname "$DESKTOP")"

if [ -x "$HERE/strata-launcher/strata-launcher" ]; then
    # A release download: the program is ready-made.
    say "Copying the program to $APP/app ..."
    rm -rf "$APP/app.new"
    cp -a "$HERE/strata-launcher" "$APP/app.new"
    rm -rf "$APP/app"
    mv "$APP/app.new" "$APP/app"
    rm -rf "$APP/venv"
    RUN="$APP/app/strata-launcher"
    ICON_SRC=$(find "$APP/app" -name icon.png -path '*assets*' | head -n 1)
elif [ -f "$HERE/pyproject.toml" ]; then
    # The source code: make a Python environment for the launcher alone.
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PY=$candidate
            break
        fi
    done
    say "Building the launcher's own Python environment in $APP/venv (this downloads Qt, about 100 MB) ..."
    if command -v uv >/dev/null 2>&1; then
        uv venv --quiet --allow-existing "$APP/venv" || die "uv could not make the environment."
        VIRTUAL_ENV="$APP/venv" uv pip install --quiet --upgrade "$HERE" || die "the launcher's packages could not be installed."
    else
        [ -n "$PY" ] || die "Python 3.10 or newer was not found. Install it (Ubuntu: sudo apt install python3 python3-venv), or use the release download instead."
        "$PY" -m venv "$APP/venv" 2>/dev/null || die "$PY cannot make an environment. On Ubuntu or Debian: sudo apt install python3-venv"
        "$APP/venv/bin/python" -m pip install --quiet --upgrade pip
        "$APP/venv/bin/python" -m pip install --quiet --upgrade "$HERE" || die "the launcher's packages could not be installed."
    fi
    rm -rf "$APP/app"
    RUN="$APP/venv/bin/strata-launcher"
    ICON_SRC="$HERE/src/strata_launcher/assets/icon.png"
else
    die "run this script from the unpacked release folder or from the source folder."
fi

# The icon. The menu entry names the file itself, so it shows at once. Touching the icon folder tells a running
# desktop to look again; without that a new icon stays invisible until the next sign-in.
ICON=strata-launcher
if [ -n "${ICON_SRC:-}" ] && [ -f "$ICON_SRC" ] && cp "$ICON_SRC" "$ICON_DIR/strata-launcher.png"; then
    ICON="$ICON_DIR/strata-launcher.png"
    touch "$DATA/icons/hicolor" 2>/dev/null || true
fi

cat > "$BIN/strata-launcher" <<EOF
#!/bin/sh
exec "$RUN" "\$@"
EOF
chmod +x "$BIN/strata-launcher"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Strata Launcher
GenericName=Local AI server
Comment=Set up, start, stop and watch a Strata server
Exec="$RUN"
Icon=$ICON
Terminal=false
StartupNotify=true
StartupWMClass=strata-launcher
Categories=Utility;
Keywords=AI;LLM;Strata;
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true

# If "open when I sign in" was switched on, point that entry at the program just installed.
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/strata-launcher.desktop"
if [ -f "$AUTOSTART" ]; then
    sed -i -e "s|^Exec=.*|Exec=\"$RUN\" --at-login|" -e "s|^Icon=.*|Icon=$ICON|" "$AUTOSTART"
fi

say ""
say "Installed. Open \"Strata Launcher\" from your app menu, or run: strata-launcher"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say "($BIN is not on your PATH, so in a terminal use the full name: $BIN/strata-launcher)" ;;
esac
