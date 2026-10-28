# tetoMIDI v1.3

MIDI macro player with a falling-note piano roll. It reads a `.mid` file and presses keyboard keys for you
(piano, drums, guitar). No audio, no SoundFont. Python 3 + Tkinter.

Based on [nanoMIDIPlayer](https://github.com/NotHammer043/nanoMIDIPlayer) (GPL-3.0). See `NOTICE.md`.
Discord: https://discord.gg/peaqm6uqgB · Website: https://tetomidi-pianits.vercel.app/

## Hotkeys (global, rebindable in SETTING)
play `F2` · pause `F3` · stop `F4` · speed up `F5` · slow down `F6`

## Modes
- **MIDI PLAYER**: piano macro (61/88 keys, Shift symbols, Ctrl layer, velocity via Alt, sustain via Space).
- **DRUM PLAYER**: drum macro (channel 10 if present, otherwise all channels).
- **GUITAR PLAYER**: QWERTY macro with an editable keymap (default notes 40-75, octave folding).
- **SETTING**: hotkeys, per-instrument playback options, keymap editors, roll display, always-on-top, reset.

## MIDI Player window
The MIDI player window (v1.2; its button was removed from the instrument pages in v1.3) has a playlist: add files or a
whole folder, double-click a song, PREV / PLAY / STOP / NEXT, seek bar, repeat (off / all / one), shuffle and speed.
It plays through the key-press player of the instrument you pick (Piano / Drum / Guitar) at the top of that window, so
there is still no audio. While the window is open the global hotkeys control it. The playlist is saved between runs.

## Mini player
Click **Mini player** (on the instrument pages or in the MIDI Player window) for a small always-on-top window with the
transport, the song position and a **CONSOLE** listing the key presses that the instrument sends. Previous / next go
through the MIDI Player list. **Console** hides or shows the console; **Clear** empties it. **Expand** returns to the
main window.
The mini player has no title bar and rounded corners: drag it by any empty part, and use **×** (or **Expand**) to go back to the main window.

## Key letters on the piano
The piano roll prints the computer key on every piano key (all 61 or 88 keys; black keys show the shifted symbol or capital
that is really pressed). Keys outside the 36-96 range are played with CTRL and have a blue letter.
SETTING → *Piano roll* → **Key letters (piano)** turns it off.

## Custom roll background
SETTING → *Piano roll* → **Choose image** (JPG, PNG or GIF) puts a picture behind the falling notes of the Piano,
Drum and Guitar pages. Use **Background darkness** to make it darker, or **Remove** to go back to the plain colour.

## Run from source
```
python -m pip install -r requirements.txt
python main.py
```
Linux: needs `python3-tk` and an **X11** session (key injection and global hotkeys do not work on Wayland-only sessions).
Windows: if the target game runs as administrator, run tetoMIDI as administrator too.

## Build locally
```
python -m pip install -r requirements-build.txt
python scripts/build.py onedir      # dist/tetoMIDI/
python scripts/build.py onefile     # dist/tetoMIDI-portable(.exe)
# Windows installer: install NSIS, then see the header of packaging/windows/installer.nsi
# Linux AppImage + .deb (after onedir):
python scripts/package_linux.py --appimagetool /path/to/appimagetool
```
Tests: `python -m unittest discover -s tests -t .` (GUI tests need a display; use `xvfb-run -a` on headless Linux).

## GitHub Actions (works from a phone)
1. Push this folder to a GitHub repo (branch `main`). The workflow `.github/workflows/build-desktop.yml` runs automatically.
2. Manual run: repo → **Actions** → *Build desktop apps* → **Run workflow**.
3. Download: open the finished run → scroll to **Artifacts** → `tetoMIDI-windows` (Setup .exe + portable .exe) and
   `tetoMIDI-linux` (.AppImage + .deb). Artifacts are zip files; extract them first.
4. Release: create a tag `v1.3.0` (repo → Releases → *Draft a new release* → new tag, or `git tag v1.3.0 && git push --tags`).
   The workflow then attaches all files to a GitHub Release.

Linux: `chmod +x tetoMIDI-*.AppImage && ./tetoMIDI-*.AppImage` or `sudo apt install ./tetomidi_*_amd64.deb`.
Windows: run the Setup .exe (SmartScreen may warn because the file is unsigned), or just run the portable .exe.

## Config
`%APPDATA%\tetoMIDI\config.json` (Windows) or `~/.config/tetoMIDI/config.json` (Linux). Override with env `TETOMIDI_CONFIG_DIR`.

## Building in a GitHub Codespace (when GitHub Actions minutes are used up)
Open the repository -> **Code -> Codespaces -> Create codespace**, then in its terminal:

    ./scripts/codespace_build_linux.sh     # AppImage + .deb  -> dist/
    ./scripts/codespace_build_windows.sh   # portable .exe + Setup.exe (Wine + NSIS, slower, less reliable) -> dist/

Download the files from `dist/` (right click -> Download). A real Windows PC is the safest way to build the `.exe`:
`py -m pip install -r requirements-build.txt` then `py scripts\build.py onefile`.
