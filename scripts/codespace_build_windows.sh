#!/usr/bin/env bash
# Build the Windows portable .exe and Setup.exe inside a GitHub Codespace (Ubuntu) with Wine + NSIS.
# Usage:  ./scripts/codespace_build_windows.sh        (first run: 15-25 min, later runs are much faster)
# Wine is not a real Windows: if a step fails, build on a Windows PC instead (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PYVER="3.12.8"
export WINEPREFIX="$HOME/.wine-tetomidi" WINEARCH=win64 WINEDEBUG=-all
WINPY='C:\Python312\python.exe'

winpy() { xvfb-run -a wine "$WINPY" "$@"; }

echo "==> 1/7 system packages (wine, nsis, xvfb)"
sudo apt-get update
sudo apt-get install -y wine wine64 xvfb nsis curl ca-certificates

echo "==> 2/7 Wine prefix"
if [ ! -d "$WINEPREFIX" ]; then
  xvfb-run -a wineboot --init
  wineserver -w
fi

echo "==> 3/7 Python $PYVER for Windows (inside Wine)"
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  curl -fsSL -o /tmp/python-win.exe "https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe"
  xvfb-run -a wine /tmp/python-win.exe /quiet InstallAllUsers=0 'TargetDir=C:\Python312' \
      Include_launcher=0 Include_test=0 PrependPath=0 Shortcuts=0 || true
  wineserver -w
fi
[ -f "$WINEPREFIX/drive_c/Python312/python.exe" ] || { echo "Python did not install under Wine"; exit 1; }

echo "==> 4/7 Python packages"
winpy -m pip install --upgrade pip
winpy -m pip install -r requirements-build.txt

echo "==> 5/7 PyInstaller: portable exe and app folder"
winpy scripts/build.py onefile
winpy scripts/build.py onedir

echo "==> 6/7 Setup.exe with NSIS"
VERSION="$(python3 -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"', open('tetomidi/__init__.py').read()).group(1))")"
mkdir -p build
# NSIS on Linux treats '*.*' as 'files that contain a dot'; '*' takes everything
sed 's/\*\.\*/*/' packaging/windows/installer.nsi > build/installer-linux.nsi
makensis "-DVERSION=$VERSION" "-DSRC_DIR=$ROOT/dist/tetoMIDI" "-DOUT_FILE=$ROOT/dist/tetoMIDI-$VERSION-Setup.exe" \
         "-DICON_FILE=$ROOT/assets/app.ico" "-DLICENSE_FILE=$ROOT/LICENSE" build/installer-linux.nsi
mv -f dist/tetoMIDI-portable.exe "dist/tetoMIDI-$VERSION-portable.exe"

echo "==> 7/7 done. Download these from the Explorer (right click -> Download):"
ls -lh dist/*.exe
