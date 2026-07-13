#!/usr/bin/env bash
# Build the SmallTV Widget into a menu-bar .app bundle (macOS).
# Run from the client/ directory:  ./build/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install -r requirements-widget.txt pyinstaller
python3 -m PyInstaller --noconfirm build/smalltv_widget.spec

echo
echo "Done. Bundle at dist/SmallTVWidget.app"
echo "Move it to /Applications, then open it (right-click > Open the first time)."
