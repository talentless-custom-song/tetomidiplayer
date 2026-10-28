#!/usr/bin/env bash
# Build the Linux AppImage and .deb inside a GitHub Codespace (Ubuntu).   Usage:  ./scripts/codespace_build_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/6 system packages"
sudo apt-get update
sudo apt-get install -y python3-tk python3-venv python3-pip binutils xvfb dpkg-dev libportaudio2 curl file

echo "==> 2/6 python environment (/usr/bin/python3 on purpose: it is the one that has tkinter)"
rm -rf .venv
/usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-build.txt

echo "==> 3/6 unit tests (virtual display)"
xvfb-run -a .venv/bin/python -m unittest discover -s tests -t .

echo "==> 4/6 PyInstaller app folder"
.venv/bin/python scripts/build.py onedir

echo "==> 5/6 appimagetool"
if [ ! -x appimagetool ]; then
  curl -fsSL -o appimagetool https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
  chmod +x appimagetool
fi
export APPIMAGE_EXTRACT_AND_RUN=1      # Codespaces has no FUSE

echo "==> 6/6 AppImage + .deb"
.venv/bin/python scripts/package_linux.py --appimagetool ./appimagetool

echo
echo "Done. Download these from the Explorer (right click -> Download):"
ls -lh dist/*.AppImage dist/*.deb
