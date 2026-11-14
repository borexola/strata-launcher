#!/bin/sh
# Builds the Linux release download: out/strata-launcher-<version>-linux-x86_64.tar.gz
# It holds the ready-made program (no Python needed), install.sh, uninstall.sh and the README.
#
#   packaging/linux/build.sh [python]          (OUT_DIR=folder puts the result somewhere else than out/)
#
# Needs Python 3.10 or newer with this project and PyInstaller installed:  pip install . pyinstaller
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY=${1:-python3}
OUT="${OUT_DIR:-$ROOT/out}"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/strata_launcher/__init__.py")
NAME="strata-launcher-$VERSION-linux-$(uname -m)"

"$PY" -m PyInstaller "$ROOT/packaging/strata-launcher.spec" --noconfirm --distpath "$OUT/dist" --workpath "$OUT/build"
rm -rf "$OUT/$NAME"
mkdir -p "$OUT/$NAME"
cp -a "$OUT/dist/strata-launcher" "$OUT/$NAME/strata-launcher"
cp "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/README.md" "$OUT/$NAME/"
[ -f "$ROOT/LICENSE" ] && cp "$ROOT/LICENSE" "$OUT/$NAME/"
tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" "$NAME"
echo "Made $OUT/$NAME.tar.gz"
