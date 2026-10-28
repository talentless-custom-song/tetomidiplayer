#!/usr/bin/env python3
"""Build tetoMIDI with PyInstaller (same command on Windows and Linux).

    python scripts/build.py onedir     -> dist/tetoMIDI/            (folder; used by the installers)
    python scripts/build.py onefile    -> dist/tetoMIDI-portable[.exe]

Extra arguments are passed straight to PyInstaller.
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

from tetomidi import APP_NAME  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["onedir", "onefile"])
    args, passthrough = parser.parse_known_args()

    dist, work = ROOT / "dist", ROOT / "build"
    name = APP_NAME if args.mode == "onedir" else f"{APP_NAME}-portable"
    sep = os.pathsep  # PyInstaller's --add-data separator is ';' on Windows and ':' elsewhere
    is_windows = sys.platform.startswith("win")

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        f"--{args.mode}", "--name", name,
        "--distpath", str(dist), "--workpath", str(work / args.mode), "--specpath", str(work),
        "--paths", str(ROOT),
        f"--add-data={ROOT / 'assets'}{sep}assets",
        # Pillow loads this helper dynamically; without it ImageTk fails in the frozen app:
        "--hidden-import", "PIL._tkinter_finder", "--hidden-import", "PIL.ImageTk",
        # fake keyboard sound: PortAudio is loaded through cffi at run time:
        "--hidden-import", "sounddevice",
        # CA bundle for the MIDI Hub's https connection when the system has none:
        "--collect-data", "certifi",
        # pynput picks its backend at run time, so PyInstaller cannot see it:
        "--hidden-import", "pynput.keyboard._win32" if is_windows else "pynput.keyboard._xorg",
        "--hidden-import", "pynput.mouse._win32" if is_windows else "pynput.mouse._xorg",
    ]
    if not is_windows:
        cmd += ["--hidden-import", "Xlib.support.connect", "--hidden-import", "Xlib.ext.xtest",
                "--hidden-import", "Xlib.ext.record", "--hidden-import", "Xlib.keysymdef.latin1",
                "--hidden-import", "Xlib.keysymdef.miscellany", "--hidden-import", "Xlib.keysymdef.xkb",
                "--hidden-import", "Xlib.keysymdef.xf86"]
    if is_windows:
        cmd += ["--icon", str(ROOT / "assets" / "app.ico"),
                "--collect-all", "_sounddevice_data"]   # the PortAudio DLL that ships inside the sounddevice wheel
    cmd += passthrough + [str(ROOT / "main.py")]

    print("+", " ".join(cmd), flush=True)
    shutil.rmtree(dist / name, ignore_errors=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        return result.returncode

    out = dist / (name + (".exe" if is_windows and args.mode == "onefile" else ""))
    if not out.exists():
        print(f"error: expected build output {out} was not created", file=sys.stderr)
        return 1
    print(f"built {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
