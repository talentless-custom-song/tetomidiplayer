"""Player page shared by the Piano, Drum and Guitar modes (same layout as the reference design)."""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import filedialog

from ..engine.transport import PAUSED, PLAYING, STOPPED, Transport
from ..midi_data import MidiLoadError, Song, format_time, load_song
from ..rollmodel import build_roll
from . import theme
from .gfx import px
from .kit import Badge, IconButton, Label, PillButton, Slider, Toggle
from .roll import RollView

log = logging.getLogger(__name__)

# (label, config key) per mode, in display order.
OPTIONS = {
    "piano": [("88 key", "88Keys"), ("Sustain", "sustain"), ("Velocity", "velocity"), ("Note lengths", "noteLengths"),
              ("Loop", "loop")],
    "drums": [("Hold", "customHoldLength.enabled"), ("Release", "releaseOnPause"), ("Human", "randomFail.enabled"),
              ("Note lengths", "noteLengths"), ("Loop", "loop")],
    "guitar": [("Hold", "customHoldLength.enabled"), ("Release", "releaseOnPause"), ("Human", "randomFail.enabled"),
               ("Note lengths", "noteLengths"), ("Loop", "loop")],
}
OPTION_TIPS = {
    "88Keys": "Use the full 88-key range (extra notes use CTRL). Off: 61 keys.",
    "sustain": "Hold SPACE while the MIDI sustain pedal is down.",
    "velocity": "Send note velocity (ALT + key) and shade notes by velocity.",
    "loop": "Start again when the song ends.",
    "noteLengths": "On: notes keep their real length. Off: every note is short and the same length.",
    "customHoldLength.enabled": "Release every key after a fixed hold time (set it in Settings).",
    "releaseOnPause": "Release all keys when paused.",
    "randomFail.enabled": "Human error: random timing slips (set the amount in Settings).",
}
ROLL_INSET_X, ROLL_INSET_Y = 22, 12   # the roll is a little smaller than the free area (px at 100% scaling)
HEADINGS = {"piano": "select MIDI file", "drums": "select MIDI file", "guitar": "select MIDI file"}
CAPTIONS = {"piano": "Piano roll", "drums": "Drum pads", "guitar": "Guitar strings"}   # text on top of the roll


class PlayerPage(tk.Frame):
    def __init__(self, parent, app, mode: str):
        super().__init__(parent, bg=theme.MAIN_BG)
        self.app, self.mode, self.cfg = app, mode, app.cfg
        self.transport: Transport = app.transports[mode]
        self.song: Song | None = None
        self._last_text = {}
        self._narrow = None
        self._hot_chips: dict[str, PillButton] = {}
        self._build()
        self.cfg.on_change(self._on_cfg_change)
        saved = self.cfg.get(f"{mode}.currentFile", "")
        if saved and os.path.isfile(saved):
            self.load_path(saved, remember=False, quiet=True)
        self._apply_flags()
        self._refresh_status()

    # ------------------------------------------------------------ build
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        pad = px(24)

        head = tk.Frame(self, bg=theme.MAIN_BG)
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(px(26), px(14)))
        self.head = head
        self.heading = Label(head, HEADINGS[self.mode], size=24, color=theme.TEXT, bg=theme.MAIN_BG)
        self.heading.pack(side="left", padx=(px(100), px(10)))
        self.select_btn = PillButton(head, "Open", 112, 42, size=18, icon="folder", icon_size=20,
                                     command=self.select_file)
        self.select_btn.pack(side="left")
        self._tip(self.select_btn, "Open a MIDI file")
        self.mini_btn = PillButton(head, "Mini player", 130, 34, size=16, radius=17, command=self.app.show_mini)
        self.mini_btn.pack(side="right", padx=(px(10), 0))
        self.status = _FitLabel(head, size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.status.pack(side="left", fill="x", expand=True, padx=(px(14), 0))

        self.body = tk.Frame(self, bg=theme.MAIN_BG)
        self.body.grid(row=1, column=0, sticky="nsew", padx=pad)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.options = tk.Frame(self.body, bg=theme.MAIN_BG)
        self.toggles: dict[str, Toggle] = {}
        self.option_labels: list[Label] = []
        for label, key in OPTIONS[self.mode]:
            lab = Label(self.options, label, size=17, color=theme.TEXT_SOFT, bg=theme.MAIN_BG)
            tg = Toggle(self.options, command=lambda v, k=key: self._set_option(k, v), bg=theme.MAIN_BG)
            self.toggles[key] = tg
            self.option_labels.append(lab)
            self._tip(tg, OPTION_TIPS.get(key, ""))

        self.roll = RollView(self.body, self.mode, self.cfg)
        self.roll.grid(row=0, column=1, sticky="nsew", padx=px(ROLL_INSET_X), pady=(px(ROLL_INSET_Y), px(ROLL_INSET_Y + 4)))

        hot = tk.Frame(self, bg=theme.MAIN_BG)
        hot.grid(row=2, column=0, pady=(px(12), 0))
        for name, text in (("play", "play"), ("pause", "pause"), ("stop", "stop")):
            Label(hot, text, size=17, color=theme.TEXT_SOFT, bg=theme.MAIN_BG).pack(side="left", padx=(px(14), px(8)))
            chip = PillButton(hot, self.cfg.get(f"hotkeys.{name}", ""), 62, 32, size=16, radius=10,
                              command=lambda n=name: self.app.rebind_hotkey(n))
            chip.pack(side="left")
            self._hot_chips[name] = chip
            self._tip(chip, "Click, then press the key you want")

        bar = tk.Frame(self, bg=theme.MAIN_BG)
        bar.grid(row=3, column=0, sticky="ew", padx=pad, pady=(px(14), 0))
        bar.grid_columnconfigure(2, weight=1)
        self.play_btn = PillButton(bar, "PLAY", 87, 40, bg=theme.PLAY, hover=theme.PLAY_HOVER, radius=8, size=17,
                                   command=self.on_play)
        self.play_btn.grid(row=0, column=0, padx=(0, px(14)))
        self.stop_btn = PillButton(bar, "STOP", 87, 40, bg=theme.STOP, hover=theme.STOP_HOVER, radius=8, size=17,
                                   command=self.on_stop)
        self.stop_btn.grid(row=0, column=1, padx=(0, px(18)))
        self.slider = Slider(bar, on_drag=self._on_slider_drag, on_commit=self._on_slider_commit)
        self.slider.grid(row=0, column=2, sticky="ew")
        self.bpm = Badge(bar, "120", width=52, height=28, size=16)
        self.bpm.grid(row=0, column=3, padx=(px(10), px(18)))
        self.bpm.bind("<MouseWheel>", lambda e: self.change_speed(5 if e.delta > 0 else -5))
        self.bpm.bind("<Button-4>", lambda e: self.change_speed(5))
        self.bpm.bind("<Button-5>", lambda e: self.change_speed(-5))
        self._tip(self.bpm, "Tempo in BPM (includes playback speed). Scroll to change speed.")
        reset = IconButton(bar, "reset", 26, command=self.on_reset, tip="Reset: stop and speed back to 100%", bg=theme.MAIN_BG)
        reset.grid(row=0, column=4)

        self.time = Badge(self, "00:00:00/00:00:00", width=190, height=28, size=16)
        self.time.grid(row=4, column=0, pady=(px(6), px(22)))
        self.bind("<Configure>", self._on_resize)

    def _tip(self, widget, text):
        if text:
            from .kit import Tooltip
            Tooltip(widget, text)

    # ----------------------------------------------------- responsiveness
    def _on_resize(self, e):
        self.heading.pack_configure(padx=(px(100) if e.width > px(700) else px(0), px(10)))
        narrow = e.width < px(640)
        if narrow == self._narrow:
            return
        self._narrow = narrow
        for w in self.options.winfo_children():
            w.grid_forget()
        if narrow:   # options become a horizontal strip above the roll
            self.options.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(6)))
            self.roll.grid_configure(row=0, column=0, columnspan=2)
            self.body.grid_rowconfigure(1, weight=0)
            for c in range(4):   # columns share the full width so the strip has no empty side
                self.options.grid_columnconfigure(c, weight=1)
            for i, (lab, tg) in enumerate(zip(self.option_labels, self.toggles.values())):
                r, c = divmod(i, 2)   # 2 x 2 grid keeps the toggles on screen in small windows
                lab.grid(row=r, column=c * 2, padx=(px(8), px(6)), pady=px(2), sticky="e")
                tg.grid(row=r, column=c * 2 + 1, padx=(0, px(8)), pady=px(2), sticky="w")
                self.options.grid_rowconfigure(i, weight=0)
        else:
            for c in range(4):
                self.options.grid_columnconfigure(c, weight=0)
            self.options.grid(row=0, column=0, sticky="ns", padx=(0, px(18)))
            self.roll.grid_configure(row=0, column=1, columnspan=1)
            for i, (lab, tg) in enumerate(zip(self.option_labels, self.toggles.values())):
                self.options.grid_rowconfigure(i, weight=1)
                lab.grid(row=i, column=0, sticky="w", padx=(0, px(14)))
                tg.grid(row=i, column=1)

    # -------------------------------------------------------- options
    def _apply_flags(self):
        for key, tg in self.toggles.items():
            tg.set(bool(self.cfg.get(f"{self.mode}.{key}", False)))
        self.transport.loop = bool(self.cfg.get(f"{self.mode}.loop", False))
        if self.mode in self.app.speed:
            self.transport.set_speed(self.app.speed[self.mode])

    def _set_option(self, key: str, value: bool):
        self.cfg.set(f"{self.mode}.{key}", value)
        if key == "loop":
            self.transport.loop = value

    _ROLL_KEYS = ("88Keys", "pitchOffset", "transposeOffset", "drumsMap", "keymap", "foldOctaves",
                  "skipPercussion", "velocity", "noteLengths", "pianoMap")

    def _on_cfg_change(self, path: str):
        if path == "*":
            self._apply_flags()
            self.rebuild_roll()
            self._refresh_chips()
            return
        if path.startswith("hotkeys."):
            self._refresh_chips()
        elif path.startswith(self.mode + "."):
            rest = path[len(self.mode) + 1:]
            if rest.split(".")[0] in self._ROLL_KEYS:
                self.rebuild_roll()
            if rest in self.toggles or rest == "loop":
                self._apply_flags()
        elif path.startswith("roll."):
            self.roll.refresh_style()

    def _refresh_chips(self):
        for name, chip in self._hot_chips.items():
            chip.set_text(self.cfg.get(f"hotkeys.{name}", ""))

    # ------------------------------------------------------------ files
    def select_file(self):
        start = os.path.dirname(self.cfg.get(f"{self.mode}.currentFile", "") or "") or os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="Select MIDI file", initialdir=start if os.path.isdir(start) else None,
            filetypes=[("MIDI files", "*.mid *.midi *.MID *.MIDI"), ("All files", "*.*")])
        if path:
            self.load_path(path)

    def load_path(self, path: str, remember: bool = True, quiet: bool = False):
        self.status.set("Loading…", theme.TEXT_MUTED)
        self.update_idletasks()
        try:
            song = load_song(path)
        except MidiLoadError as exc:
            if not quiet:
                self.status.set(f"⚠ {exc}", theme.ERROR)
            else:
                self.status.set("")
            return False
        except Exception as exc:  # corrupted files can raise anything
            log.exception("loading %s", path)
            self.status.set(f"⚠ Could not read this file ({exc.__class__.__name__}).", theme.ERROR)
            return False
        self.song = song
        view = song.view(self.mode, bool(self.cfg.get(f"{self.mode}.skipPercussion", True)))
        self.transport.load(view, song.length)
        self.rebuild_roll()
        if remember:
            self.cfg.set(f"{self.mode}.currentFile", path)
        self._refresh_status()
        self._last_text.clear()
        self.update_view()
        return True

    def rebuild_roll(self):
        view = None
        if self.song:
            view = self.song.view(self.mode, bool(self.cfg.get(f"{self.mode}.skipPercussion", True)))
        self.roll.set_data(build_roll(self.mode, view, self.cfg.data[self.mode]))

    def _refresh_status(self):
        caption = CAPTIONS[self.mode]
        self.roll.set_caption(f"{caption}  ·  {self.song.name}" if self.song else caption)
        if self.song:
            n = len(self.transport.view.notes) if self.transport.view else 0
            self.status.set(f"{self.song.name}  ·  {format_time(self.song.length)[3:]}  ·  {n} notes", theme.TEXT_MUTED)
        elif not self.app.keys.backend.available:
            self.status.set(f"⚠ Keyboard simulation unavailable: {self.app.keys.backend.error}", theme.WARN)
        else:
            self.status.set("")

    # ---------------------------------------------------------- transport
    def on_play(self):
        if not self.song:
            self.status.set("Select a MIDI file first.", theme.WARN)
            return
        if self.transport.state == PLAYING:
            self.transport.pause()
        else:
            self.transport.play()
        self.update_view()

    def on_pause(self):
        if self.transport.state in (PLAYING, PAUSED):
            self.transport.toggle_pause()
        self.update_view()

    def on_hotkey_play(self):
        if self.song and self.transport.state != PLAYING:
            self.transport.play()
        elif not self.song:
            self.status.set("Select a MIDI file first.", theme.WARN)
        self.update_view()

    def on_stop(self):
        self.transport.stop()
        self.update_view()

    def on_reset(self):
        self.transport.stop()
        self.app.set_speed(self.mode, 1.0)
        self.update_view()

    def change_speed(self, delta_percent: float):
        step = float(self.cfg.get("app.speedStep", 5))
        sign = 1 if delta_percent > 0 else -1
        self.app.set_speed(self.mode, round((self.transport.speed + sign * step / 100), 4))
        self._last_text.pop("bpm", None)

    def leave(self):
        """Page is hidden: stop sending keys (unless the MIDI player window is playing this instrument)."""
        if self.app.midi_owns(self.mode):
            return
        self.transport.stop()
        self.update_view()

    # slider
    def _on_slider_drag(self, v):
        if self.song:
            self._set_text("time", f"{format_time(v * self.song.length)}/{format_time(self.song.length)}", self.time)

    def _on_slider_commit(self, v):
        if self.song:
            self.transport.seek(v * self.song.length)

    # -------------------------------------------------------------- tick
    def _set_text(self, key, text, widget):
        if self._last_text.get(key) != text:
            self._last_text[key] = text
            widget.set(text)

    def update_view(self):
        t = self.transport
        pos = t.position()
        if t.consume_finished():   # the state change already resets position and buttons
            self.app.song_finished(self.mode)
        self.roll.render(pos)
        length = t.length if self.song else 0.0
        if not self.slider.dragging:
            self.slider.set(pos / length if length else 0.0)
            self._set_text("time", f"{format_time(pos)}/{format_time(length)}", self.time)
        bpm = (self.song.bpm_at(pos) if self.song else 120.0) * t.speed
        self._set_text("bpm", str(int(round(bpm))), self.bpm)
        playing = t.state == PLAYING
        want = ("PAUSE", theme.PAUSE, theme.PAUSE_HOVER) if playing else ("PLAY", theme.PLAY, theme.PLAY_HOVER)
        if self._last_text.get("play") != want:
            self._last_text["play"] = want
            self.play_btn.set_text(want[0])
            self.play_btn.set_colors(want[1], want[2])


class _FitLabel(tk.Frame):
    """Single-line label that shortens its text with an ellipsis instead of overflowing."""

    def __init__(self, parent, size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG):
        super().__init__(parent, bg=bg, height=px(26))
        self.pack_propagate(False)
        self._label = Label(self, "", size=size, color=color, bg=bg)
        self._label.place(x=0, y=0)
        self._size, self._text, self._color = px(size), "", color
        self.bind("<Configure>", lambda e: self._fit())

    def set(self, text: str, color: str | None = None):
        self._text = text
        if color:
            self._color = color
        self._fit()

    def _fit(self):
        from . import gfx
        avail = max(self.winfo_width() - px(4), 0)
        text = self._text
        if avail > 0 and gfx.text_size(text, self._size)[0] > avail:
            while len(text) > 1 and gfx.text_size(text + "…", self._size)[0] > avail:
                text = text[:-1]
            text = text.rstrip() + "…"
        if self.winfo_width() <= 1:
            text = self._text
        self._label.set(text, self._color)
