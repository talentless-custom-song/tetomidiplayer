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

winpy() { timeout 1800 xvfb-run -a wine "$WINPY" "$@"; }   # a Wine crash must not hang the terminal forever

echo "==> 1/7 system packages (wine + 32-bit wine, nsis, xvfb)"
# The Python installer for Windows starts as a 32-bit program, so Wine needs its 32-bit half (wine32) too.
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install -y wine wine64 wine32:i386 xvfb nsis curl ca-certificates

echo "==> 2/7 Wine prefix"
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  rm -rf "$WINEPREFIX"          # a prefix made before wine32 was installed is broken: start clean
  xvfb-run -a wineboot --init
  wineserver -w
  [ -f "$WINEPREFIX/drive_c/windows/syswow64/rundll32.exe" ] || { echo "Wine prefix has no 32-bit part (wine32 missing?)"; exit 1; }
fi

echo "==> 3/7 Python $PYVER for Windows (inside Wine)"
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  curl -fsSL -o /tmp/python-win.exe "https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe"
  xvfb-run -a wine /tmp/python-win.exe /quiet InstallAllUsers=0 'TargetDir=C:\Python312' \
      Include_launcher=0 Include_test=0 PrependPath=0 Shortcuts=0 || true
  wineserver -w
fi
[ -f "$WINEPREFIX/drive_c/Python312/python.exe" ] || { echo "Python did not install under Wine"; exit 1; }

# a crashing program should end, not wait for Wine's debugger
xvfb-run -a wine reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f >/dev/null 2>&1 || true
xvfb-run -a wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Debugger /t REG_SZ /d false /f >/dev/null 2>&1 || true

echo "==> 4/7 Python packages"
winpy -m pip install --upgrade pip
# numpy 2.x calls a C-runtime function (crealf) that Wine does not implement yet: use numpy 1.26 for this build
winpy -m pip install "numpy<2" -r requirements-build.txt

echo "==> 5/7 PyInstaller: portable exe and app folder"
winpy scripts/build.py onefile
# dist/tetoMIDI may still hold the Linux app folder from an earlier build; Wine cannot delete those files
# (PermissionError), so remove them from Linux first
rm -rf dist/tetoMIDI build/onedir
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
