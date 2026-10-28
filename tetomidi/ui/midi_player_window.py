"""MIDI Player window: a playlist that drives the key-press player of the chosen instrument.

Like the mini player it owns no playback state: songs are loaded into the Piano / Drum / Guitar page and played
by that page's transport, so options, speed and the piano roll behave exactly as in the main window.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from ..engine.playlist import REPEAT_MODES, Playlist
from ..engine.transport import PLAYING
from ..midi_data import format_time
from . import theme
from .gfx import px
from .kit import Badge, Label, PillButton, Slider, Tooltip
from .player_page import PlayerPage, _FitLabel

MODES = (("piano", "Piano"), ("drums", "Drum"), ("guitar", "Guitar"))
MIDI_EXTS = (".mid", ".midi")
RESTART_AFTER = 3.0   # "Prev" restarts the song when more than this many seconds were played
LIST_BG, LIST_SELECT = "#171516", "#2c2a2b"


def find_midi_files(folder: str) -> list[str]:
    found: list[str] = []
    for root, _dirs, files in os.walk(folder):
        found += [os.path.join(root, f) for f in files if f.lower().endswith(MIDI_EXTS)]
    return sorted(found, key=str.lower)


class MidiPlayerWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app, self.cfg = app, app.cfg
        self.title("tetoMIDI - MIDI Player")
        self.configure(bg=theme.MAIN_BG)
        try:
            self.attributes("-topmost", bool(self.cfg.get("app.topmost", False)))
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        w, h = px(580), px(690)
        self.geometry(f"{w}x{h}")
        self.minsize(px(520), px(560))
        self._last: dict = {}

        mode = self.cfg.get("player.mode", "piano")
        self.mode = mode if mode in app.pages and mode in dict(MODES) else "piano"
        saved = [p for p in self.cfg.get("player.playlist", []) if isinstance(p, str) and os.path.isfile(p)]
        index = self.cfg.get("player.index", -1)
        self.playlist = Playlist(saved, index if isinstance(index, int) else -1, self.cfg.get("player.repeat", "off"),
                                 bool(self.cfg.get("player.shuffle", False)))
        self._build()
        self._refresh_list()
        self._refresh_mode_buttons()
        self.bind("<space>", lambda e: self.toggle_play())

    # ------------------------------------------------------------ helpers
    @property
    def page(self) -> PlayerPage:
        return self.app.pages[self.mode]

    def _build(self):
        pad = px(20)
        head = tk.Frame(self, bg=theme.MAIN_BG)
        head.pack(fill="x", padx=pad, pady=(px(18), px(6)))
        Label(head, "MIDI Player", size=24, bold=True, color=theme.TEXT, bg=theme.MAIN_BG).pack(side="left")
        self.mode_btns: dict[str, PillButton] = {}
        for key, title in reversed(MODES):   # packed right-to-left so they read Piano, Drum, Guitar
            btn = PillButton(head, title, 74, 32, size=15, radius=16, command=lambda k=key: self.set_mode(k))
            btn.pack(side="right", padx=(px(6), 0))
            self.mode_btns[key] = btn
        mini = PillButton(head, "Mini player", 118, 32, size=15, radius=16,
                          command=lambda: self.app.show_mini(self.mode))
        mini.pack(side="right", padx=(px(12), 0))
        Tooltip(mini, "Show the small player (same instrument, with a key console)")

        self.now = _FitLabel(self, size=16, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.now.pack(fill="x", padx=pad)

        box = tk.Frame(self, bg=LIST_BG)
        box.pack(fill="both", expand=True, padx=pad, pady=(px(10), px(8)))
        self.scroll = tk.Scrollbar(box, orient="vertical", width=px(12), bd=0, highlightthickness=0, relief="flat",
                                   bg="#3a3738", troughcolor=LIST_BG, activebackground="#4a4748")
        self.listbox = tk.Listbox(box, bg=LIST_BG, fg=theme.TEXT_SOFT, selectbackground=LIST_SELECT,
                                  selectforeground=theme.TEXT, activestyle="none", highlightthickness=0, bd=0,
                                  relief="flat", font=("TkDefaultFont", 11), exportselection=False,
                                  yscrollcommand=self.scroll.set)
        self.scroll.configure(command=self.listbox.yview)
        self.scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(px(6), 0), pady=px(4))
        self.listbox.bind("<Double-Button-1>", lambda e: self._play_selected())
        self.listbox.bind("<Return>", lambda e: self._play_selected())
        self.listbox.bind("<Delete>", lambda e: self.remove_selected())

        edit = tk.Frame(self, bg=theme.MAIN_BG)
        edit.pack(fill="x", padx=pad)
        for text, w, cmd in (("Add files", 100, self.add_files), ("Add folder", 108, self.add_folder),
                             ("Remove", 88, self.remove_selected), ("Clear", 72, self.clear)):
            PillButton(edit, text, w, 34, size=16, radius=17, command=cmd).pack(side="left", padx=(0, px(8)))
        self.count = Label(edit, "", size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.count.pack(side="right")

        seek = tk.Frame(self, bg=theme.MAIN_BG)
        seek.pack(fill="x", padx=pad, pady=(px(14), 0))
        seek.grid_columnconfigure(0, weight=1)
        self.slider = Slider(seek, on_drag=self._on_drag, on_commit=self._on_commit, bg=theme.MAIN_BG)
        self.slider.grid(row=0, column=0, sticky="ew")
        self.time = Badge(seek, "00:00:00/00:00:00", width=190, height=28, size=16)
        self.time.grid(row=0, column=1, padx=(px(12), 0))

        ctl = tk.Frame(self, bg=theme.MAIN_BG)
        ctl.pack(pady=(px(12), 0))
        PillButton(ctl, "PREV", 80, 40, size=17, radius=8, command=self.prev).pack(side="left", padx=px(6))
        self.play_btn = PillButton(ctl, "PLAY", 90, 40, bg=theme.PLAY, hover=theme.PLAY_HOVER, radius=8, size=17,
                                   command=self.toggle_play)
        self.play_btn.pack(side="left", padx=px(6))
        PillButton(ctl, "STOP", 90, 40, bg=theme.STOP, hover=theme.STOP_HOVER, radius=8, size=17,
                   command=self.stop).pack(side="left", padx=px(6))
        PillButton(ctl, "NEXT", 80, 40, size=17, radius=8, command=self.next).pack(side="left", padx=px(6))

        opt = tk.Frame(self, bg=theme.MAIN_BG)
        opt.pack(pady=(px(12), px(18)))
        self.repeat_btn = PillButton(opt, "Repeat: off", 118, 34, size=16, radius=17, command=self.cycle_repeat)
        self.repeat_btn.pack(side="left", padx=px(6))
        self.shuffle_btn = PillButton(opt, "Shuffle: off", 118, 34, size=16, radius=17, command=self.toggle_shuffle)
        self.shuffle_btn.pack(side="left", padx=px(6))
        PillButton(opt, "-", 34, 34, size=20, radius=17, command=lambda: self.change_speed(-1)).pack(side="left", padx=(px(18), px(4)))
        self.speed = Badge(opt, "100%", width=64, height=30, size=16)
        self.speed.pack(side="left")
        PillButton(opt, "+", 34, 34, size=20, radius=17, command=lambda: self.change_speed(1)).pack(side="left", padx=(px(4), 0))

    def _set(self, key, text, widget):
        if self._last.get(key) != text:
            self._last[key] = text
            widget.set(text)

    # ------------------------------------------------------------ playlist
    def _save_list(self):
        self.cfg.set("player.playlist", list(self.playlist.items))
        self.cfg.set("player.index", self.playlist.index)

    def _refresh_list(self):
        top = self.listbox.yview()[0]
        self.listbox.delete(0, "end")
        for i, path in enumerate(self.playlist.items):
            self.listbox.insert("end", f"{i + 1:>3}.  {os.path.basename(path)}")
        self._mark_current()
        self.listbox.yview_moveto(top)
        n = len(self.playlist.items)
        self.count.set(f"{n} song{'s' if n != 1 else ''}")

    def _mark_current(self):
        for i in range(self.listbox.size()):
            if i == self.playlist.index:
                self.listbox.itemconfig(i, background=theme.ACCENT, foreground="#ffffff", selectbackground=theme.ACCENT)
            else:
                self.listbox.itemconfig(i, background=LIST_BG, foreground=theme.TEXT_SOFT, selectbackground=LIST_SELECT)

    def add_paths(self, paths) -> int:
        paths = [p for p in paths if p.lower().endswith(MIDI_EXTS) and os.path.isfile(p)]
        added = self.playlist.add(paths)
        self._save_list()
        self._refresh_list()
        return added

    def add_files(self):
        start = os.path.dirname(self.playlist.items[-1]) if self.playlist.items else os.path.expanduser("~")
        paths = filedialog.askopenfilenames(
            parent=self, title="Add MIDI files", initialdir=start if os.path.isdir(start) else None,
            filetypes=[("MIDI files", "*.mid *.midi *.MID *.MIDI"), ("All files", "*.*")])
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Add every MIDI file in a folder (with subfolders)",
                                         mustexist=True)
        if folder:
            n = self.add_paths(find_midi_files(folder))
            if not n:
                self.now.set("No new MIDI files found in that folder.", theme.WARN)

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        for i in reversed(sel):
            if self.playlist.remove(i):   # the song that was loaded from the list is no longer in it
                self._last.pop("now", None)
        self._save_list()
        self._refresh_list()

    def clear(self):
        self.playlist.clear()
        self._save_list()
        self._refresh_list()

    # ---------------------------------------------------------- playback
    def _play_selected(self):
        sel = self.listbox.curselection()
        if sel:
            self._play_from(sel[0], lambda: self.playlist.next())

    def _play_from(self, i, advance, remember=True) -> bool:
        """Load and play entry ``i``; unreadable files are skipped by asking ``advance`` for the next entry."""
        page = self.page
        for _ in range(len(self.playlist.items) + 1):
            if i is None:
                return False
            if page.load_path(self.playlist.items[i], remember=False):
                self.playlist.go(i, remember)
                self._save_list()
                self._mark_current()
                self._apply_repeat()
                page.transport.play()
                page.update_view()
                self._last.pop("now", None)
                return True
            self.now.set(f"⚠ {os.path.basename(self.playlist.items[i])}: skipped", theme.ERROR)
            self.playlist.go(i, remember=False)
            i = advance()
        return False

    def use_mode(self, mode: str):
        """Play this list on ``mode`` (the mini player's previous / next and play use it)."""
        if mode != self.mode:
            self.set_mode(mode)

    def start(self):
        """Play the current (or first) entry of the list."""
        if self.playlist.items:
            self._play_from(max(self.playlist.index, 0), lambda: self.playlist.next())

    def toggle_play(self):
        page = self.page
        if page.song is None and self.playlist.items:
            self.start()
        else:
            page.on_play()

    def stop(self):
        self.page.on_stop()

    def next(self):
        self._play_from(self.playlist.next(), lambda: self.playlist.next())

    def prev(self):
        page = self.page
        if page.song is not None and page.transport.position() > RESTART_AFTER:
            page.transport.seek(0.0)
            return
        i = self.playlist.prev()
        if i is not None:
            self._play_from(i, lambda: self.playlist.prev(), remember=False)

    def on_finished(self):
        """The loaded song ended by itself: continue with the list."""
        page = self.page
        if page.song is not None and page.song.path == self.playlist.current():
            self._play_from(self.playlist.next(auto=True), lambda: self.playlist.next(auto=True))

    def hotkey(self, method: str, *args):
        """Global hotkeys act on this window's player while it is open."""
        page = self.page
        if method == "on_hotkey_play" and page.song is None and self.playlist.items:
            self.start()
        else:
            getattr(page, method)(*args)

    # ------------------------------------------------------------ options
    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.page.on_stop()
        self.mode = mode
        self.cfg.set("player.mode", mode)
        self._apply_repeat()
        self._refresh_mode_buttons()
        cur = self.playlist.current()
        if cur and (self.page.song is None or self.page.song.path != cur):
            self.page.load_path(cur, remember=False, quiet=True)
        self._last.clear()

    def _refresh_mode_buttons(self):
        for key, btn in self.mode_btns.items():
            if key == self.mode:
                btn.set_colors("#2f6fa6", "#3f84bf")
            else:
                btn.set_colors(theme.PILL, theme.PILL_HOVER)

    def _apply_repeat(self):
        """Repeat one = the page's Loop switch; the other modes are handled by the list."""
        self.cfg.set(f"{self.mode}.loop", self.playlist.repeat == "one")

    def cycle_repeat(self):
        self.playlist.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.playlist.repeat) + 1) % len(REPEAT_MODES)]
        self.cfg.set("player.repeat", self.playlist.repeat)
        self._apply_repeat()

    def toggle_shuffle(self):
        self.playlist.shuffle = not self.playlist.shuffle
        self.playlist._played = {self.playlist.index} if self.playlist.index >= 0 else set()
        self.cfg.set("player.shuffle", self.playlist.shuffle)

    def change_speed(self, sign: int):
        self.page.change_speed(sign)

    def on_settings_reset(self):
        """Settings were reset to defaults: forget the list too."""
        self.page.on_stop()
        self.playlist.clear()
        self.playlist.repeat, self.playlist.shuffle, self.mode = "off", False, "piano"
        self._refresh_mode_buttons()
        self._refresh_list()
        self._last.clear()

    # -------------------------------------------------------------- slider
    def _on_drag(self, v):
        p = self.page
        if p.song:
            self._set("time", f"{format_time(v * p.song.length)}/{format_time(p.song.length)}", self.time)

    def _on_commit(self, v):
        p = self.page
        if p.song:
            p.transport.seek(v * p.song.length)

    # ---------------------------------------------------------------- tick
    def update_view(self):
        p = self.page
        t = p.transport
        if t.consume_finished():
            self.on_finished()
        length = t.length if p.song else 0.0
        pos = t.position()
        if not self.slider.dragging:
            self.slider.set(pos / length if length else 0.0)
            self._set("time", f"{format_time(pos)}/{format_time(length)}", self.time)
        want = ("PAUSE", theme.PAUSE, theme.PAUSE_HOVER) if t.state == PLAYING else ("PLAY", theme.PLAY, theme.PLAY_HOVER)
        if self._last.get("play") != want:
            self._last["play"] = want
            self.play_btn.set_text(want[0])
            self.play_btn.set_colors(want[1], want[2])
        name = p.song.name if p.song else "Nothing loaded. Add files, then double-click a song."
        if self._last.get("now") != name:
            self._last["now"] = name
            self.now.set(name, theme.TEXT_SOFT if p.song else theme.TEXT_MUTED)
        self._set("speed", f"{int(round(self.app.speed[self.mode] * 100))}%", self.speed)
        for key, text, btn, active in (
                ("repeat", f"Repeat: {self.playlist.repeat}", self.repeat_btn, self.playlist.repeat != "off"),
                ("shuffle", f"Shuffle: {'on' if self.playlist.shuffle else 'off'}", self.shuffle_btn, self.playlist.shuffle)):
            if self._last.get(key) != (text, active):
                self._last[key] = (text, active)
                btn.set_text(text)
                btn.set_colors(*(("#2f6fa6", "#3f84bf") if active else (theme.PILL, theme.PILL_HOVER)))

    def close(self):
        """Hide the window; an instrument that is no longer on screen stops sending keys."""
        if self.app.current != self.mode:
            self.page.on_stop()
        self.withdraw()
