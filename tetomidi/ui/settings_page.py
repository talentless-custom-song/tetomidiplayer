"""SETTING page: only options that change behaviour (hotkeys, playback, key maps, roll display)."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image

from .. import VERSION_LABEL
from ..fakesound import DEFAULT_DEVICE_LABEL, list_output_devices
from ..mappings import DRUM_LABELS, DRUM_NOTE_TO_NAME, note_name
from ..paths import config_dir
from . import theme
from .gfx import px
from .kit import Label, PillButton, ScrollFrame, Slider, Stepper, Toggle

MODE_TITLES = ["Piano", "Drum", "Guitar"]
MODE_KEYS = ["piano", "drums", "guitar"]
PITCH_NAMES = ["Lowest", "Low", "Middle", "High", "Highest"]
PITCH_VALUES = [-24, -12, 0, 12, 24]
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif")
BACKGROUND_NAME = "roll_background"   # the copy kept in the config folder
HOTKEY_ROWS = [("play", "Play"), ("pause", "Pause"), ("stop", "Stop"), ("speedup", "Speed up"), ("slowdown", "Slow down")]


class ValueSlider(tk.Frame):
    def __init__(self, parent, vmin, vmax, value, command, step=1, fmt="{:g}", width=230):
        super().__init__(parent, bg=theme.MAIN_BG)
        self.vmin, self.vmax, self.step, self.fmt, self.command = vmin, vmax, step, fmt, command
        self.slider = Slider(self, on_drag=self._changed, on_commit=self._changed, bg=theme.MAIN_BG)
        self.slider.configure(width=px(width))
        self.slider.pack(side="left")
        self.readout = Label(self, "", size=16, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.readout.pack(side="left", padx=(px(12), 0))
        self.set(value)

    def _value(self, frac):
        v = self.vmin + (self.vmax - self.vmin) * frac
        v = round(v / self.step) * self.step
        return min(max(v, self.vmin), self.vmax)

    def _changed(self, frac):
        v = self._value(frac)
        self.readout.set(self.fmt.format(v))
        self.command(v)

    def set(self, value):
        self.slider.set((value - self.vmin) / (self.vmax - self.vmin))
        self.readout.set(self.fmt.format(value))


class SettingsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.MAIN_BG)
        self.app, self.cfg = app, app.cfg
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=(px(24), px(14)), pady=(px(14), px(10)))
        self.root_inner = self.scroll.inner
        self.mode_frames: dict[str, tk.Frame] = {}
        self._speed_sliders: dict[str, ValueSlider] = {}
        self._build()
        self.scroll.bind_wheel_recursive()

    # ------------------------------------------------------ helpers
    def _section(self, title):
        Label(self.root_inner, title, size=26, bold=True, color=theme.TEXT, bg=theme.MAIN_BG).pack(
            anchor="w", pady=(px(18), px(6)))

    def _row(self, parent, label, widget_factory, hint=""):
        row = tk.Frame(parent, bg=theme.MAIN_BG)
        row.pack(fill="x", pady=px(5))
        lab = Label(row, label, size=17, color=theme.TEXT_SOFT, bg=theme.MAIN_BG)
        lab.pack(side="left")
        row.update_idletasks()
        holder = tk.Frame(row, bg=theme.MAIN_BG)
        holder.pack(side="right")
        w = widget_factory(holder)
        w.pack()
        if hint:
            Label(parent, hint, size=14, color=theme.TEXT_FAINT, bg=theme.MAIN_BG).pack(anchor="w")
        return row

    def _toggle(self, path):
        return lambda p: Toggle(p, value=bool(self.cfg.get(path, False)), command=lambda v: self.cfg.set(path, v), bg=theme.MAIN_BG)

    def _slider(self, path, vmin, vmax, step=1, fmt="{:g}", transform=None):
        def make(p):
            return ValueSlider(p, vmin, vmax, self.cfg.get(path, vmin), lambda v: self.cfg.set(path, v), step, fmt)
        return make

    # --------------------------------------------------------- build
    def _build(self):
        Label(self.root_inner, "Settings", size=34, bold=True, color=theme.TEXT, bg=theme.MAIN_BG).pack(anchor="w")

        self._section("Hotkeys")
        self.hot_chips = {}
        for name, title in HOTKEY_ROWS:
            def make(p, n=name):
                chip = PillButton(p, self.cfg.get(f"hotkeys.{n}", ""), 96, 34, size=16, radius=10,
                                  command=lambda: self.app.rebind_hotkey(n))
                self.hot_chips[n] = chip
                return chip
            self._row(self.root_inner, title, make)
        self.cfg.on_change(self._cfg_changed)
        Label(self.root_inner, "Hotkeys work while another window (your game) has focus. "
              "Linux: needs an X11 session.", size=14, color=theme.TEXT_FAINT, bg=theme.MAIN_BG).pack(anchor="w")

        self._section("Playback")
        self._row(self.root_inner, "Speed step", self._slider("app.speedStep", 1, 25, 1, "{:g}%"))
        sel = tk.Frame(self.root_inner, bg=theme.MAIN_BG)
        sel.pack(fill="x", pady=(px(8), px(4)))
        Label(sel, "Instrument", size=17, color=theme.TEXT_SOFT, bg=theme.MAIN_BG).pack(side="left")
        self.mode_stepper = Stepper(sel, MODE_TITLES, 0, command=self._show_mode, width=120, bg=theme.MAIN_BG)
        self.mode_stepper.pack(side="right")
        self.mode_host = tk.Frame(self.root_inner, bg=theme.MAIN_BG)
        self.mode_host.pack(fill="x")
        for m in MODE_KEYS:
            f = tk.Frame(self.mode_host, bg=theme.MAIN_BG)
            self.mode_frames[m] = f
            self._build_mode(f, m)
        self._show_mode(0)

        self._build_fake_sound()

        self._section("MIDI Hub preview")
        self._row(self.root_inner, "Piano SoundFont", self._toggle("hub.useSoundfont"),
                  hint="The Hub preview plays the song with the bundled Piano.sf2. Off = the built-in synthesised piano. "
                       "Applies the next time a preview starts. Volume: the slider above.")

        self._section("Piano roll")
        self._row(self.root_inner, "Fall time", self._slider("roll.fallSeconds", 1.0, 8.0, 0.5, "{:g} s"))
        self._row(self.root_inner, "Key glow", self._toggle("roll.glow"))
        self._row(self.root_inner, "Key letters (piano)", self._toggle("roll.keyLabels"))
        self._row(self.root_inner, "3D piano roll", self._toggle("roll.piano3D"),
                  hint="Piano roll only: the lanes and the falling notes recede into the distance, far notes are darker. Off = flat roll.")
        colors = list(theme.NOTE_PALETTES)
        self._row(self.root_inner, "Note color", lambda p: Stepper(
            p, [c.title() for c in colors], colors.index(self.cfg.get("roll.noteColor", "blue")) if self.cfg.get("roll.noteColor") in colors else 0,
            command=lambda i: self.cfg.set("roll.noteColor", colors[i]), width=110, bg=theme.MAIN_BG))
        self._background_row()
        self._row(self.root_inner, "Background darkness", self._slider("roll.backgroundDim", 0, 90, 5, "{:g}%"),
                  hint="Background picture for the Piano, Drum and Guitar rolls: JPG, PNG or GIF (first frame).")

        self._section("Window")
        self._row(self.root_inner, "Window opacity", lambda p: ValueSlider(
            p, 40, 100, round(self.app.opacity() * 100), lambda v: self.app.set_opacity(v / 100), 5, "{:g}%"),
            hint="Makes the whole app see-through (the mini player too).")
        self._row(self.root_inner, "Always on top", lambda p: Toggle(
            p, value=bool(self.cfg.get("app.topmost", False)), command=self.app.set_topmost, bg=theme.MAIN_BG))

        self._section("Data")
        def buttons(p):
            f = tk.Frame(p, bg=theme.MAIN_BG)
            PillButton(f, "Open config folder", 190, 36, size=16, command=self._open_folder).pack(side="left", padx=(0, px(8)))
            PillButton(f, "Reset all", 110, 36, size=16, bg="#5a1f1b", hover="#7a2a25", command=self._reset).pack(side="left")
            return f
        self._row(self.root_inner, "Config", buttons,
                  hint="The 61/88-key piano map is stored in config.json (pianoMap) if you need to edit it.")

        self._section("About")
        Label(self.root_inner, f"tetoMIDI {VERSION_LABEL}", size=18, color=theme.TEXT_SOFT, bg=theme.MAIN_BG).pack(anchor="w")
        Label(self.root_inner, "Based on nanoMIDIPlayer by NotHammer043 · GNU GPL v3", size=15, color=theme.TEXT_MUTED,
              bg=theme.MAIN_BG).pack(anchor="w", pady=(px(2), px(30)))

    # ------------------------------------------------ fake keyboard sound
    def _build_fake_sound(self):
        self._section("Fake keyboard sound")
        self._row(self.root_inner, "Fake keyboard sound", self._toggle("fakeSound.enabled"),
                  hint="Plays a real typing click for every key the app presses, so it sounds like you type the notes by hand.")
        self._row(self.root_inner, "Volume", self._slider("fakeSound.volume", 0, 100, 5, "{:g}%"))
        self._device_names: list[str] = []

        def device_picker(p):
            f = tk.Frame(p, bg=theme.MAIN_BG)
            options = self._device_options()
            self.device_stepper = Stepper(f, [self._short(n) for n in options], self._device_index(options),
                                          command=self._device_chosen, width=250, bg=theme.MAIN_BG)
            self.device_stepper.pack(side="left")
            PillButton(f, "Rescan", 90, 34, size=16, command=self._rescan_devices).pack(side="left", padx=(px(8), 0))
            return f
        self._row(self.root_inner, "Send sound to", device_picker,
                  hint="Voice chat: pick your virtual cable here (e.g. VB-Audio \"CABLE Input\"), then choose the cable's "
                       "other end (\"CABLE Output\") as the microphone in Roblox.")
        self._row(self.root_inner, "Also play on my speakers", self._toggle("fakeSound.alsoSpeakers"),
                  hint="Plays the same clicks on the default output, so you hear what the others hear.")

        def test_button(p):
            f = tk.Frame(p, bg=theme.MAIN_BG)
            PillButton(f, "Test sound", 130, 36, size=16, command=self._test_fake_sound).pack(side="left")
            return f
        self._row(self.root_inner, "Test", test_button)
        self.fake_status = Label(self.root_inner, "", size=14, color=theme.TEXT_FAINT, bg=theme.MAIN_BG)
        self.fake_status.pack(anchor="w")
        self._update_fake_status()

    @staticmethod
    def _short(name, limit=28):
        return name if len(name) <= limit else name[:limit - 1] + "…"

    def _device_options(self) -> list[str]:
        """Names shown in the picker: the default output, every output device, and the saved one if it is unplugged."""
        names = [n for _, n in list_output_devices()]
        self._device_count = len(names)
        saved = str(self.cfg.get("fakeSound.device", "") or "")
        self._device_missing = saved if saved and saved not in names else ""
        if self._device_missing:
            names.append(saved)
        self._device_names = [""] + names   # "" = system default
        return [DEFAULT_DEVICE_LABEL] + names

    def _device_index(self, options) -> int:
        saved = str(self.cfg.get("fakeSound.device", "") or "")
        return self._device_names.index(saved) if saved in self._device_names else 0

    def _device_chosen(self, index):
        self.cfg.set("fakeSound.device", self._device_names[index])

    def _rescan_devices(self):
        options = self._device_options()
        self.device_stepper.options[:] = [self._short(n) for n in options]
        self.device_stepper.set(self._device_index(options))
        self.app.fake_sound.apply()
        self._update_fake_status()

    def _test_fake_sound(self):
        self.app.fake_sound.test()
        self.after(300, self._update_fake_status)

    def _update_fake_status(self):
        if not self.winfo_exists():   # the page was rebuilt (Reset all) before this delayed call ran
            return
        err = self.app.fake_sound.error
        if err:
            text, color = f"Audio problem: {err}", "#e5736b"
        elif not self._device_count:
            text, color = "No audio output device found (PortAudio missing?).", "#e5736b"
        elif self._device_missing:
            text, color = f"Saved device not found: {self._device_missing}", "#e5736b"
        elif self.cfg.get("fakeSound.enabled", False):
            text, color = "Fake keyboard sound is on.", theme.TEXT_FAINT
        else:
            text, color = "Fake keyboard sound is off.", theme.TEXT_FAINT
        self.fake_status.set(text, color)

    def _build_mode(self, frame, mode):
        c = f"{mode}"
        self._row(frame, "Playback speed", lambda p: self._make_speed(p, mode))
        self._row(frame, "Custom hold length", self._toggle(f"{c}.customHoldLength.enabled"))
        self._row(frame, "Hold time", self._slider(f"{c}.customHoldLength.noteLength", 0.02, 1.0, 0.02, "{:.2f} s"))
        self._row(frame, "Finger limit", lambda p: ValueSlider(
            p, 1, 11, self.cfg.get(f"{c}.fingerLimit", 11), lambda v: self.cfg.set(f"{c}.fingerLimit", int(v)), 1, "{:g}"),
            hint="Max keys held at once (11 = unlimited).")
        self._row(frame, "Release keys on pause", self._toggle(f"{c}.releaseOnPause"))
        self._row(frame, "Human error", self._toggle(f"{c}.randomFail.enabled"))
        self._row(frame, "Timing error chance", self._slider(f"{c}.randomFail.speed", 0, 50, 1, "{:g}%"))
        if mode == "piano":
            self._row(frame, "Wrong-note chance", self._slider("piano.randomFail.transpose", 0, 50, 1, "{:g}%"))
            self._row(frame, "Pitch", lambda p: Stepper(
                p, PITCH_NAMES, PITCH_VALUES.index(self.cfg.get("piano.pitchOffset", 0)) if self.cfg.get("piano.pitchOffset", 0) in PITCH_VALUES else 2,
                command=lambda i: self.cfg.set("piano.pitchOffset", PITCH_VALUES[i]), width=110, bg=theme.MAIN_BG))
            self._row(frame, "Key", lambda p: Stepper(
                p, KEY_NAMES, int(self.cfg.get("piano.transposeOffset", 0)) % 12,
                command=lambda i: self.cfg.set("piano.transposeOffset", i), width=70, bg=theme.MAIN_BG))
            self._row(frame, "No doubles", self._toggle("piano.noDoubles"), hint="Re-press a key that is still held.")
            self._row(frame, "Sustain cut-off", self._slider("piano.sustainCutoff", 0, 127, 1, "{:g}"))
            self._row(frame, "Skip drum channel", self._toggle("piano.skipPercussion"),
                      hint="Don't type MIDI channel 10 (drums) as piano keys.")
        elif mode == "guitar":
            self._row(frame, "Fold octaves", self._toggle("guitar.foldOctaves"),
                      hint="Move notes outside the key map by octaves instead of skipping them.")
            self._row(frame, "Skip drum channel", self._toggle("guitar.skipPercussion"))
            self._keymap_editor(frame, "Guitar keys (note → key)",
                                [(note_name(int(n)), f"guitar.keymap.{n}") for n in sorted(self.cfg.get("guitar.keymap"), key=int)])
        else:
            names = []
            seen = set()
            for note, name in sorted(DRUM_NOTE_TO_NAME.items(), key=lambda kv: list(DRUM_LABELS).index(kv[1])):
                if name not in seen:
                    seen.add(name)
                    names.append((DRUM_LABELS[name], f"drums.drumsMap.{name}"))
            self._keymap_editor(frame, "Drum keys (drum → key)", names)

    def _make_speed(self, parent, mode):
        s = ValueSlider(parent, 10, 500, round(self.app.speed.get(mode, 1.0) * 100),
                        lambda v: self.app.set_speed(mode, v / 100), 5, "{:g}%")
        self._speed_sliders[mode] = s
        return s

    def _keymap_editor(self, frame, title, items):
        Label(frame, title, size=18, bold=True, color=theme.TEXT, bg=theme.MAIN_BG).pack(anchor="w", pady=(px(14), px(4)))
        grid = tk.Frame(frame, bg=theme.MAIN_BG)
        grid.pack(anchor="w")
        cols = 3
        for i, (label, path) in enumerate(items):
            r, cidx = divmod(i, cols)
            cell = tk.Frame(grid, bg=theme.MAIN_BG)
            cell.grid(row=r, column=cidx, padx=(0, px(22)), pady=px(2), sticky="w")
            Label(cell, label, size=15, color=theme.TEXT_SOFT, bg=theme.MAIN_BG).pack(side="left", padx=(0, px(8)))
            var = tk.StringVar(value=str(self.cfg.get(path, "") or ""))
            ent = tk.Entry(cell, textvariable=var, width=2, justify="center", bg="#000000", fg="#ffffff",
                           insertbackground="#ffffff", relief="flat", highlightthickness=1,
                           highlightbackground="#3a3738", highlightcolor=theme.ACCENT, font=("TkDefaultFont", 12))
            ent.pack(side="right")
            var.trace_add("write", lambda *_, v=var, p=path, e=ent: self._keymap_edit(v, p))

    def _keymap_edit(self, var, path):
        value = var.get()
        if len(value) > 1:
            value = value[-1]
            var.set(value)
        self.cfg.set(path, value.lower() if value.isalpha() else value)

    # ------------------------------------------------------- events
    def _show_mode(self, index):
        for i, m in enumerate(MODE_KEYS):
            if i == index:
                self.mode_frames[m].pack(fill="x")
            else:
                self.mode_frames[m].pack_forget()
        self.scroll.bind_wheel_recursive(self.mode_frames[MODE_KEYS[index]])
        self.scroll._sync()

    def _cfg_changed(self, path):
        if path.startswith("hotkeys.") or path == "*":
            for n, chip in self.hot_chips.items():
                chip.set_text(self.cfg.get(f"hotkeys.{n}", ""))
        if path.startswith("fakeSound.") or path == "*":
            self.after(250, self._update_fake_status)   # the app (re)opens the audio streams after this listener
        if path in ("roll.background", "*") and getattr(self, "bg_name", None) is not None:
            self.bg_name.set(self._background_text())

    # ------------------------------------------------ roll background picture
    def _background_row(self):
        row = tk.Frame(self.root_inner, bg=theme.MAIN_BG)
        row.pack(fill="x", pady=px(5))
        Label(row, "Background image", size=17, color=theme.TEXT_SOFT, bg=theme.MAIN_BG).pack(side="left")
        holder = tk.Frame(row, bg=theme.MAIN_BG)
        holder.pack(side="right")
        PillButton(holder, "Remove", 96, 36, size=16, command=self._remove_background).pack(side="right", padx=(px(8), 0))
        PillButton(holder, "Choose image", 150, 36, size=16, command=self._choose_background).pack(side="right")
        self.bg_name = Label(self.root_inner, self._background_text(), size=14, color=theme.TEXT_FAINT, bg=theme.MAIN_BG)
        self.bg_name.pack(anchor="w")

    def _background_text(self) -> str:
        path = self.cfg.get("roll.background", "")
        if not path or not os.path.isfile(path):
            return "No picture: plain background"
        return f"Current picture: {os.path.basename(path)}"

    def _clear_background_files(self, keep=None):
        for old in glob.glob(str(config_dir() / "backgrounds" / (BACKGROUND_NAME + ".*"))):
            if old != keep:
                try:
                    os.remove(old)
                except OSError:
                    pass

    def _choose_background(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="Choose a background picture",
            filetypes=[("Pictures", "*.jpg *.jpeg *.png *.gif"), ("All files", "*.*")])
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext not in IMAGE_EXTS:
                raise ValueError("unsupported file type")
            with Image.open(path) as im:   # make sure the file really is a picture
                im.load()
            folder = config_dir() / "backgrounds"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / (BACKGROUND_NAME + ext)
            if os.path.abspath(path) != os.path.abspath(dest):
                shutil.copyfile(path, dest)
            self._clear_background_files(keep=str(dest))
        except (OSError, ValueError):
            messagebox.showinfo("Background picture", "This file cannot be used. Please choose a JPG, PNG or GIF picture.",
                                parent=self.winfo_toplevel())
            return
        self.cfg.set("roll.background", str(dest))

    def _remove_background(self):
        self.cfg.set("roll.background", "")
        self._clear_background_files()

    def show_chip_capturing(self, name):
        if name in self.hot_chips:
            self.hot_chips[name].set_text("…")

    def refresh(self):
        for mode, s in self._speed_sliders.items():
            s.set(round(self.app.speed.get(mode, 1.0) * 100))

    def _open_folder(self):
        path = str(config_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 - opening the user's own config folder
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            messagebox.showinfo("Config folder", path, parent=self.winfo_toplevel())

    def _reset(self):
        if messagebox.askyesno("Reset all settings", "Restore every setting (hotkeys, key maps, options) to its default?",
                               parent=self.winfo_toplevel()):
            self.app.reset_settings()
