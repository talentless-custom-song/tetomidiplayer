# Changes vs nanoMIDIPlayer-main

**Rewritten / new (package `tetomidi/`, replaces the old app code):** UI (`ui/*`, Pillow-drawn widgets), `engine/transport.py`
(single clock shared by player and piano roll), `engine/dispatchers.py` (piano/drums macros ported with identical semantics,
new guitar), `rollmodel.py`, `midi_data.py` (friendly errors), `keysim.py`, `hotkeys.py`, `config.py`, `defaults*.py`,
`mappings.py`, `paths.py`, `main.py`.
**Added:** `assets/` (icons, fonts, app icon), `scripts/build.py`, `scripts/package_linux.py`, `packaging/windows/installer.nsi`,
`.github/workflows/build-desktop.yml`, `tests/` (60 tests), `README.md`, `NOTICE.md`, `CHANGES.md`, `.gitignore`,
`requirements*.txt`.
**Kept:** `LICENSE` (GPL-3.0).
**Deleted / not carried over:** customtkinter UI and themes, MIDI→QWERTY, MIDI Hub, MIDI-output mode, macOS support,
update checker, telemetry, `bootstrapper.exe`, dependencies customtkinter / keyboard / python-rtmidi / requests / emoji /
markdown / tkinterweb / pygments.

**Since v1.2:** intro screen, animated sidebar, liquid-glass buttons and switches (animated), smaller piano/drum/guitar roll,
"Note lengths" option (off = every note short and equal, in the roll and in the key presses), window opacity setting,
mini player (`ui/mini_player.py`).
**Mini player (update):** round transport buttons (previous, stop, big play / pause, next; previous and next use the
MIDI player list), a collapsible **CONSOLE** that shows the key presses of the shown instrument (Piano / Drum / Guitar,
each with its own history, Clear button, `press: p+shift` style lines). `engine`/`keysim`: key events are labelled with
their source (`ScopedKeys`, `KeySim.add_listener`), `console.py` holds the history. The mini player's setting is
`mini.console`. The MIDI player window has a **Mini player** button. The **Open** button (was "Select file") uses a folder
icon. Icons are drawn as vector shapes in `ui/gfx.py` (`control_icon`, `round_button_image`).
**Roll background picture:** SETTING → Piano roll → *Background image* (JPG, PNG or GIF; a GIF uses its first frame).
The picture is copied to `backgrounds/roll_background.<ext>` in the config folder and shown behind the falling notes of
the Piano, Drum and Guitar rolls. *Background darkness* (0-90%) darkens it. Config: `roll.background`, `roll.backgroundDim`.
Keyboards and drum/guitar pads stay opaque. Code: `gfx.background_image`, `RollView._background`.
**MIDI Player window:** `ui/midi_player_window.py` (playlist, prev/next, seek, repeat, shuffle, speed, instrument choice),
`engine/playlist.py` (play order logic), "MIDI player" button on the instrument pages, `player.*` settings.

**v1.3:**
- *Lighter UI, same look:* converted Tk images are cached (`gfx.photo_cached`, used by every widget in `kit.py`), the roll
  skips frames while the position does not change (paused / stopped), and the main loop slows down to 50 ms while no
  window is visible.
- *Platform code in one place:* `tetomidi/platform_tweaks.py` (DPI + 1 ms timer + taskbar identity on Windows, frame removal
  on Windows / X11, session detection). Tk class name is now `TetoMIDI` (`StartupWMClass` in the Linux `.desktop` file).
  Arch Linux: `packaging/arch/PKGBUILD`.
- *Mini player:* no title bar any more (drag by any empty area, **×** goes back to the main window). Windows: borderless
  window with a taskbar button; Linux: `_MOTIF_WM_HINTS`, so always-on-top keeps working. If the frame cannot be removed,
  the normal title bar stays. The window has rounded corners (`platform_tweaks.round_corners`: Windows 11 DWM, older
  Windows region, X11 Shape extension).
- *Console scrollbar:* dark, slim, self-drawn (`kit.DarkScrollbar`) instead of the white Windows scrollbar.
- *Removed:* the **MIDI player** button of the instrument pages (the window code is still in `ui/midi_player_window.py`).
- *Piano roll:* text on top of the roll (`Piano roll · song name`, `Drum pads · ...`, `Guitar strings · ...`).
- *Intro:* all fade steps are rendered before the clock starts, elements glide up while fading in, smoother easing,
  frame pacing that does not drift.
- *Key letters:* the piano roll prints the mapped computer key on all 61 / 88 keys (`RollView._piano_key_labels`, config
  `roll.keyLabels`, Settings toggle *Key letters (piano)*); CTRL-layer keys are blue.
- *Build:* `scripts/codespace_build_linux.sh` and `scripts/codespace_build_windows.sh` (Wine + NSIS) for GitHub Codespaces.
