"""Intro screen: logo, "tetoMIDI player" and an accent line reveal one after another, then the splash
cross-fades into the main window (which stays fully invisible until then).

Smoothness: every fade step is rendered *before* the clock starts (no image building while it runs), the
elements also glide up a few pixels while they fade in, the window alpha moves every frame, and the frame timer
subtracts the time a frame took so the pace does not drift.
"""

from __future__ import annotations

import logging
import time
import tkinter as tk
from typing import Callable

from .. import APP_NAME, VERSION_LABEL
from . import gfx, theme
from .gfx import px

log = logging.getLogger(__name__)

WIN_FADE_MS = 420      # splash window fades in
INTRO_MS = 1800        # total time of the staggered reveal before the hand-over
FADE_OUT_MS = 750      # splash fades out while the main window fades in
FRAME_MS = 12          # target frame time (about 80 fps; Tk cannot go faster than the timer allows)
LEVELS = 24            # alpha steps per element (all of them are prepared up front)

# (start ms, duration ms) of each element's reveal
T_LOGO, T_TITLE, T_SUB, T_LINE = (80, 600), (380, 600), (720, 520), (480, 1050)
# how far (px at 100% scaling) each element glides up while it appears
RISE = {"logo": 10, "name": 16, "player": 16, "sub": 9}


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)   # smootherstep: zero slope at both ends


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def set_alpha(win: tk.Misc, value: float) -> bool:
    try:
        win.attributes("-alpha", max(0.0, min(1.0, value)))
        return True
    except tk.TclError:   # platform without window transparency
        return False


def _raw(ms: float, span: tuple[int, int]) -> float:
    return max(0.0, min(1.0, (ms - span[0]) / span[1]))


class Splash:
    """Borderless centred window.  ``root`` must already be at alpha 0 (see ``TetoApp``)."""

    BG = theme.SIDEBAR_BG

    def __init__(self, root: tk.Tk, on_done: Callable[[], None] | None = None, target_alpha: float = 1.0):
        self.root, self.on_done, self.target = root, on_done, target_alpha
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=theme.NAV_ACTIVE)   # 1px frame colour around the canvas
        set_alpha(self.win, 0.0)
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.w, self.h = px(560), px(320)
        x = (self.win.winfo_screenwidth() - self.w) // 2
        y = (self.win.winfo_screenheight() - self.h) // 2
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

        self.cv = tk.Canvas(self.win, bg=self.BG, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True, padx=1, pady=1)

        self._logo_size = px(88)
        self._title_font, self._sub_font = px(58), px(21)
        self._bold_w = gfx.text_size(APP_NAME, self._title_font, bold=True)[0]
        self._player_w = gfx.text_size(" player", self._title_font)[0]
        bg = self.BG
        self._builders = {
            "logo": lambda a: gfx.icon_image("logo", self._logo_size, a),
            "name": lambda a: gfx.text_image(APP_NAME, self._title_font, gfx.mix(bg, theme.TEXT, a), True),
            "player": lambda a: gfx.text_image(" player", self._title_font, gfx.mix(bg, theme.ACCENT, a)),
            "sub": lambda a: gfx.text_image(f"MIDI macro player  ·  {VERSION_LABEL}", self._sub_font,
                                            gfx.mix(bg, theme.TEXT_MUTED, a)),
        }
        self._cache: dict = {}
        self._level: dict[str, int] = {}
        anchors = {"logo": "center", "name": "w", "player": "w", "sub": "center"}
        self._items = {k: self.cv.create_image(0, 0, anchor=a, state="hidden") for k, a in anchors.items()}
        self._line = self.cv.create_line(0, 0, 0, 0, fill=theme.ACCENT, width=px(3), capstyle="round", state="hidden")
        self._place()
        self._prewarm()

        self._phase = "intro"
        self._draw(0.0)
        try:   # settle the initial layout of the (invisible) main window before the clock starts
            self.win.update()
        except tk.TclError:
            pass
        self._start = time.monotonic()
        self.win.after(1, self._step)

    # ------------------------------------------------------------------ layout
    def _place(self):
        cx, h = self.w // 2 - 1, self.h
        total = self._bold_w + self._player_w
        left = cx - total // 2
        self._base = {"logo": (cx, int(h * 0.27)), "name": (left, int(h * 0.56)),
                      "player": (left + self._bold_w, int(h * 0.56)), "sub": (cx, int(h * 0.75))}
        self._line_y, self._line_half = int(h * 0.88), px(120)

    # ------------------------------------------------------------------ drawing
    def _image(self, name: str, q: int):
        key = (name, q)
        img = self._cache.get(key)
        if img is None:
            img = self._cache[key] = gfx.photo(self._builders[name](q / LEVELS))
        return img

    def _prewarm(self):
        """Render every fade step now, so no image has to be built while the animation runs."""
        for name in self._builders:
            for q in range(1, LEVELS + 1):
                self._image(name, q)

    def _show(self, name: str, alpha: float, rise_t: float):
        q = round(alpha * LEVELS)
        item = self._items[name]
        if q <= 0:
            if self._level.get(name) != 0:
                self._level[name] = 0
                self.cv.itemconfigure(item, state="hidden")
            return
        x, y = self._base[name]
        self.cv.coords(item, x, y + int(round((1.0 - rise_t) * px(RISE[name]))))   # glides up into place
        if self._level.get(name) != q:
            self._level[name] = q
            self.cv.itemconfigure(item, state="normal", image=self._image(name, q))

    def _draw(self, ms: float):
        for name, span in (("logo", T_LOGO), ("name", T_TITLE), ("player", T_TITLE), ("sub", T_SUB)):
            raw = _raw(ms, span)
            self._show(name, ease(raw), ease_out(raw))
        # accent line grows from the centre
        grow = ease_out((ms - T_LINE[0]) / T_LINE[1])
        half = int(self._line_half * grow)
        cx = self.w // 2 - 1
        self.cv.coords(self._line, cx - half, self._line_y, cx + half, self._line_y)
        self.cv.itemconfigure(self._line, state="normal" if half > 0 else "hidden")

    # ------------------------------------------------------------------ timeline
    def _elapsed_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000.0

    def _step(self):
        began = time.perf_counter()
        try:
            ms = self._elapsed_ms()
            if self._phase == "intro":
                set_alpha(self.win, ease(ms / WIN_FADE_MS))
                self._draw(ms)
                if ms >= INTRO_MS:
                    self._phase, self._start = "out", time.monotonic()
            else:
                t = ease(ms / FADE_OUT_MS)
                set_alpha(self.win, 1.0 - t)
                set_alpha(self.root, t * self.target)
                if ms >= FADE_OUT_MS:
                    return self._finish()
            spent = int((time.perf_counter() - began) * 1000)
            self.win.after(max(1, FRAME_MS - spent), self._step)   # the frame's own work is not added to the wait
        except tk.TclError:   # window closed while the intro was running
            self._finish()

    def _finish(self):
        set_alpha(self.root, self.target)
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self._cache.clear()
        if self.on_done:
            self.on_done()
