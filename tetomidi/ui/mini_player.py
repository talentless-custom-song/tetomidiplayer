"""Mini player: a small always-on-top window with the transport and a key console.

It owns no playback state: it drives the transport of whichever instrument page is active and mirrors that
page's song, position and play state.  Previous / next and play use the MIDI player's playlist.  The main
window is hidden while the mini player is shown.
"""

from __future__ import annotations

import tkinter as tk

from ..console import MAX_LINES
from ..engine.transport import PLAYING
from ..midi_data import format_time
from .. import platform_tweaks
from . import theme
from .gfx import px
from .kit import Badge, DarkScrollbar, IconButton, Label, PillButton, RoundButton, Slider, Tooltip
from .player_page import PlayerPage, _FitLabel

MODE_TITLES = {"piano": "Piano", "drums": "Drum", "guitar": "Guitar"}
MODE_ICONS = (("piano", "piano"), ("drums", "drum"), ("guitar", "guitar"))
WIDTH = 470            # px at 100% scaling; the height follows the content (console shown or hidden)
LOG_LINES = 8          # visible console lines
ACTIVE_BG, ACTIVE_HOVER = "#2f6fa6", "#3f84bf"
FRAME_LINE = "#3a3738"   # 1 px outline that replaces the title bar
CORNER = 16              # corner radius, px at 100% scaling


class MiniPlayer(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.withdraw()   # hidden until ``show_mini``: the frame is removed before the window is ever mapped
        self.app = app
        self.title("tetoMIDI mini")   # still used by the taskbar and Alt+Tab
        self.configure(bg=theme.MAIN_BG)
        self.resizable(False, False)
        self._drag = None
        self._taskbar_done = False
        # No title bar: only the UI is left.  If the platform cannot do it, the normal frame stays.
        self.frameless = platform_tweaks.make_frameless(self)
        if self.frameless:
            self.configure(highlightthickness=1, highlightbackground=FRAME_LINE, highlightcolor=FRAME_LINE)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", app.hide_mini)
        self._last = {}
        self._log_mode = None   # instrument whose lines are in the console list
        self._log_seq = 0       # newest console line already shown
        self._build()
        self._apply_size()
        self._bind_drag(self)
        self.bind("<Configure>", lambda e: self._reshape() if e.widget is self else None, add="+")
        self.bind("<Map>", lambda e: self._reshape() if e.widget is self else None, add="+")

    # ------------------------------------------------------------ frameless window
    def _bind_drag(self, widget):
        """Drag the window by any empty part of it (labels and frames; buttons, slider and console keep their clicks)."""
        if isinstance(widget, (tk.Toplevel, tk.Frame, Label, Badge)):
            widget.bind("<ButtonPress-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")
            widget.bind("<ButtonRelease-1>", self._drag_end, add="+")
        for child in widget.winfo_children():
            if not isinstance(child, tk.Listbox):
                self._bind_drag(child)

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        self._drag = None

    def _reshape(self, _event=None):
        """(Re)apply the rounded shape whenever the window gets a new size or is shown."""
        if not self.frameless:
            return
        key = (self.winfo_viewable(), self.winfo_width(), self.winfo_height())
        if key == getattr(self, "_shape_key", None):
            return
        self._shape_key = key
        self.after_idle(lambda: platform_tweaks.round_corners(self, px(CORNER)))

    def ensure_taskbar_button(self):
        """Once, after the first show: a frameless window gets its taskbar button back on Windows."""
        if self.frameless and not self._taskbar_done and self.winfo_viewable():
            self._taskbar_done = True
            platform_tweaks.show_in_taskbar(self)

    # ------------------------------------------------------------ build
    def _build(self):
        pad = px(16)
        top = tk.Frame(self, bg=theme.MAIN_BG)
        top.pack(fill="x", padx=pad, pady=(px(14), px(6)))
        self.mode_label = Label(top, "Piano", size=19, bold=True, color=theme.TEXT, bg=theme.MAIN_BG)
        self.mode_label.pack(side="left")
        close = PillButton(top, "×", 32, 32, size=22, radius=16, command=self.app.hide_mini)
        close.pack(side="right", padx=(px(12), 0))
        Tooltip(close, "Back to the main window")
        for mode, icon in reversed(MODE_ICONS):   # packed right-to-left so they read piano, drum, guitar
            IconButton(top, icon, 24, command=lambda m=mode: self.app.show_page(m), tip=MODE_TITLES[mode] + " player",
                       bg=theme.MAIN_BG).pack(side="right", padx=(px(8), 0))
        self.song = _FitLabel(self, size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.song.pack(fill="x", padx=pad)

        bar = tk.Frame(self, bg=theme.MAIN_BG)
        bar.pack(fill="x", padx=pad, pady=(px(8), px(4)))
        bar.grid_columnconfigure(0, weight=1)
        self.slider = Slider(bar, on_drag=self._on_drag, on_commit=self._on_commit, bg=theme.MAIN_BG)
        self.slider.grid(row=0, column=0, sticky="ew")
        self.time = Badge(bar, "00:00:00/00:00:00", width=190, height=26, size=15)
        self.time.grid(row=0, column=1, padx=(px(12), 0))

        row = tk.Frame(self, bg=theme.MAIN_BG)
        row.pack(fill="x", padx=pad, pady=(px(8), px(10)))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(2, weight=1)
        self.console_btn = PillButton(row, "Console", 84, 34, size=15, radius=17, command=self.toggle_console)
        self.console_btn.grid(row=0, column=0, sticky="w")
        ctl = tk.Frame(row, bg=theme.MAIN_BG)
        ctl.grid(row=0, column=1)
        small = dict(fill=theme.CONTROL, hover=theme.CONTROL_HOVER, glyph_color=theme.CONTROL_GLYPH)
        RoundButton(ctl, "prev", 46, command=lambda: self._step(-1), tip="Previous song (MIDI player list)",
                    **small).pack(side="left", padx=px(7))
        self.stop_btn = RoundButton(ctl, "stop", 46, command=self._stop, tip="Stop", **small)
        self.stop_btn.pack(side="left", padx=px(7))
        self.play_btn = RoundButton(ctl, "play", 60, fill=theme.MINI_PLAY, hover=theme.MINI_PLAY_HOVER,
                                    glyph_color=theme.MINI_PLAY_GLYPH, command=self._play, tip="Play / pause", gloss=0.5)
        self.play_btn.pack(side="left", padx=px(7))
        RoundButton(ctl, "next", 46, command=lambda: self._step(1), tip="Next song (MIDI player list)",
                    **small).pack(side="left", padx=px(7))
        PillButton(row, "Expand", 84, 34, size=15, radius=17, command=self.app.hide_mini).grid(row=0, column=2, sticky="e")

        self.console = tk.Frame(self, bg=theme.MAIN_BG)
        head = tk.Frame(self.console, bg=theme.MAIN_BG)
        head.pack(fill="x", pady=(0, px(4)))
        self.console_title = Label(head, "CONSOLE", size=13, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.console_title.pack(side="left")
        PillButton(head, "Clear", 64, 26, size=13, radius=13, command=self.clear_console).pack(side="right")
        box = tk.Frame(self.console, bg=theme.CONSOLE_BG)
        box.pack(fill="x")
        self.scroll = DarkScrollbar(box, command=lambda *a: self.log.yview(*a), width=10)
        self.log = tk.Listbox(box, height=LOG_LINES, bg=theme.CONSOLE_BG, fg=theme.CONSOLE_TEXT,
                              selectbackground=theme.CONSOLE_BG, selectforeground=theme.CONSOLE_TEXT,
                              activestyle="none", highlightthickness=0, bd=0, relief="flat",
                              font=("TkFixedFont", 10), exportselection=False, takefocus=0,
                              yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=(px(8), 0), pady=px(6))

    # ------------------------------------------------------------ size / console switch
    def console_on(self) -> bool:
        return bool(self.app.cfg.get("mini.console", True))

    def _apply_size(self):
        if self.console_on():
            self.console.pack(fill="x", padx=px(16), pady=(0, px(14)))
        else:
            self.console.pack_forget()
        self.console_btn.set_colors(*((ACTIVE_BG, ACTIVE_HOVER) if self.console_on() else (theme.PILL, theme.PILL_HOVER)))
        self.update_idletasks()   # height follows the content, so the window never leaves gaps
        self.geometry(f"{px(WIDTH)}x{self.winfo_reqheight()}")
        self._shape_key = None
        self._reshape()

    def toggle_console(self):
        self.app.cfg.set("mini.console", not self.console_on())
        self._apply_size()

    # ------------------------------------------------------------ helpers
    @property
    def page(self) -> PlayerPage | None:
        return self.app.player_page()

    def place_near_root(self):
        r = self.app.root
        self.geometry(f"+{max(r.winfo_x() + px(60), 0)}+{max(r.winfo_y() + px(60), 0)}")

    def _play(self):
        page = self.page
        if page is None:
            return
        if page.song is None:   # nothing loaded: start the MIDI player's list on this instrument
            midi = self.app.midi_window()
            if midi.playlist.items:
                midi.use_mode(page.mode)
                midi.start()
                return
        page.on_play()

    def _stop(self):
        if self.page:
            self.page.on_stop()

    def _step(self, direction: int):
        page = self.page
        if page is None:
            return
        midi = self.app.midi_window()
        if not midi.playlist.items:
            self.song.set("No song list yet: previous / next are not available.", theme.WARN)
            return
        midi.use_mode(page.mode)
        if direction > 0:
            midi.next()
        else:
            midi.prev()

    def _on_drag(self, v):
        p = self.page
        if p and p.song:
            self._set("time", f"{format_time(v * p.song.length)}/{format_time(p.song.length)}", self.time)

    def _on_commit(self, v):
        p = self.page
        if p and p.song:
            p.transport.seek(v * p.song.length)

    def _set(self, key, text, widget):
        if self._last.get(key) != text:
            self._last[key] = text
            widget.set(text)

    # ------------------------------------------------------------ console
    def clear_console(self):
        mode = self._log_mode or (self.page.mode if self.page else None)
        if mode is None:
            return
        console = self.app.consoles[mode]
        console.clear()
        self.log.delete(0, "end")
        self._log_mode, self._log_seq = mode, console.seq

    def _update_log(self, mode: str):
        console = self.app.consoles[mode]
        if mode != self._log_mode:   # another instrument is shown: start its list from its history
            self._log_mode, self._log_seq = mode, 0
            self.log.delete(0, "end")
            self.console_title.set(f"CONSOLE  ·  {MODE_TITLES[mode]}")
        fresh = console.since(self._log_seq)
        if not fresh:
            return
        at_end = self.log.size() == 0 or self.log.yview()[1] >= 0.999
        for seq, text in fresh:
            self.log.insert("end", text)
            self.log.itemconfig(self.log.size() - 1,
                                fg=theme.TEXT_FAINT if text.startswith("release") else theme.CONSOLE_TEXT)
            self._log_seq = seq
        extra = self.log.size() - MAX_LINES
        if extra > 0:
            self.log.delete(0, extra - 1)
        if at_end:
            self.log.see("end")

    # ------------------------------------------------------------ tick
    def update_view(self):
        p = self.page
        if p is None:
            return
        self._set("mode", MODE_TITLES[p.mode], self.mode_label)
        name = p.song.name if p.song else "No MIDI file loaded – open one in the main window"
        if self._last.get("song") != name:
            self._last["song"] = name
            self.song.set(name)
        t = p.transport
        length = t.length if p.song else 0.0
        pos = t.position()
        if not self.slider.dragging:
            self.slider.set(pos / length if length else 0.0)
            self._set("time", f"{format_time(pos)}/{format_time(length)}", self.time)
        want = "pause" if t.state == PLAYING else "play"
        if self._last.get("play") != want:
            self._last["play"] = want
            self.play_btn.set_glyph(want)
        if self.console_on():
            self._update_log(p.mode)
