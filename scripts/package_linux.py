#!/usr/bin/env python3
"""Turn dist/tetoMIDI (PyInstaller onedir) into an AppImage and a .deb.

    python scripts/package_linux.py --appimagetool /path/to/appimagetool
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tetomidi import __version__  # noqa: E402

DESKTOP = """[Desktop Entry]
Type=Application
Name=tetoMIDI
Comment=MIDI macro player with piano roll (piano, drums, guitar)
Exec={exec}
Icon=tetomidi
Terminal=false
Categories=AudioVideo;Audio;Music;
StartupWMClass=TetoMIDI
"""


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=True)


def build_appimage(onedir: Path, out_dir: Path, tool: str) -> Path:
    appdir = out_dir / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    copy_tree(onedir, appdir / "usr" / "lib" / "tetoMIDI")
    run = appdir / "AppRun"
    run.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/lib/tetoMIDI/tetoMIDI" "$@"\n')
    run.chmod(0o755)
    (appdir / "tetomidi.desktop").write_text(DESKTOP.format(exec="tetoMIDI"))
    shutil.copy(ROOT / "assets" / "app-256.png", appdir / "tetomidi.png")
    shutil.copy(ROOT / "assets" / "app-256.png", appdir / ".DirIcon")
    out = out_dir / f"tetoMIDI-{__version__}-x86_64.AppImage"
    env = dict(os.environ, ARCH="x86_64")
    # --appimage-extract-and-run: CI runners have no FUSE
    subprocess.run([tool, "--appimage-extract-and-run", str(appdir), str(out)], check=True, env=env)
    out.chmod(0o755)
    return out


def build_deb(onedir: Path, out_dir: Path) -> Path:
    pkg = out_dir / "deb"
    shutil.rmtree(pkg, ignore_errors=True)
    copy_tree(onedir, pkg / "opt" / "tetoMIDI")
    (pkg / "usr" / "bin").mkdir(parents=True)
    os.symlink("/opt/tetoMIDI/tetoMIDI", pkg / "usr" / "bin" / "tetomidi")
    apps = pkg / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "tetomidi.desktop").write_text(DESKTOP.format(exec="/opt/tetoMIDI/tetoMIDI"))
    icons = pkg / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)
    shutil.copy(ROOT / "assets" / "app-256.png", icons / "tetomidi.png")
    size_kb = sum(f.stat().st_size for f in (pkg / "opt").rglob("*") if f.is_file()) // 1024
    (pkg / "DEBIAN").mkdir()
    (pkg / "DEBIAN" / "control").write_text(
        f"Package: tetomidi\nVersion: {__version__}\nSection: sound\nPriority: optional\nArchitecture: amd64\n"
        f"Depends: libc6, libx11-6, libportaudio2\nInstalled-Size: {size_kb}\nMaintainer: tetoMIDI <noreply@tetomidi.invalid>\n"
        "Description: tetoMIDI - MIDI macro player with falling-note piano roll\n"
        " Plays MIDI files as keyboard macros for piano, drums and guitar.\n"
        " Derived from nanoMIDIPlayer (GPL-3.0). Needs an X11 session.\n")
    for d in [p for p in pkg.rglob("*") if p.is_dir()] + [pkg]:
        d.chmod(0o755)
    out = out_dir / f"tetomidi_{__version__}_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(pkg), str(out)], check=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--appimagetool", required=True)
    ap.add_argument("--onedir", default=str(ROOT / "dist" / "tetoMIDI"))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    onedir, out = Path(a.onedir), Path(a.out)
    if not (onedir / "tetoMIDI").is_file():
        print(f"error: {onedir}/tetoMIDI not found - run scripts/build.py onedir first", file=sys.stderr)
        return 1
    print("built", build_appimage(onedir, out, a.appimagetool))
    print("built", build_deb(onedir, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
