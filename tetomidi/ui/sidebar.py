"""Black sidebar: logo, instrument navigation, MIDI Hub, settings, Discord and website links."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from typing import Callable

from .. import DISCORD_URL, VERSION_LABEL, WEBSITE_URL, APP_NAME
from . import gfx, theme
from .gfx import px
from .kit import IconButton, Tooltip

NAV_ITEMS = [("piano", "MIDI PLAYER", "piano"), ("drums", "DRUM PLAYER", "drum"),
             ("guitar", "GUITAR PLAYER", "guitar"), ("hub", "MIDI HUB", "hub"), ("settings", "SETTING", "gear")]


def _approach(value: float, target: float, step: float) -> float:
    if abs(target - value) <= step:
        return target
    return value + step if target > value else value - step


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


class NavItem(tk.Canvas):
    def __init__(self, parent, label: str, icon: str, command: Callable[[], None], width: int):
        self.compact = False
        self.h = px(54)
        super().__init__(parent, width=width, height=self.h, bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.command = width, command
        self.active = self.over = False
        self._a = self._h = 0.0       # animated progress (0..1) of the active / hover highlight
        self._job = None
        icon_size = px(30)
        self._icon = gfx.photo(gfx.icon_image(icon, icon_size))
        self._text = gfx.photo(gfx.text_image(label, px(19), theme.TEXT))
        self._bg = {}
        self.bind("<Enter>", lambda e: self._set("over", True))
        self.bind("<Leave>", lambda e: self._set("over", False))
        self.bind("<ButtonRelease-1>", lambda e: command() if 0 <= e.x <= self.w and 0 <= e.y <= self.h else None)
        self._icon_size = icon_size
        self._draw()
        Tooltip(self, label.title(), enabled=lambda: self.compact)

    def resize(self, width: int, compact: bool):
        self.w, self.compact = width, compact
        self._bg.clear()
        self.configure(width=width)
        self._draw()

    def _set(self, attr, value):
        setattr(self, attr, value)
        self._animate()

    # ------------------------------------------------------- animation
    _STEP_ACTIVE, _STEP_HOVER, _FRAME_MS = 0.16, 0.22, 16

    def _animate(self):
        """Ease the hover / active highlight towards its target a few frames at a time."""
        if self._job is None:
            self._tick()

    def _tick(self):
        self._job = None
        ta, th = (1.0 if self.active else 0.0), (1.0 if self.over else 0.0)
        self._a = _approach(self._a, ta, self._STEP_ACTIVE)
        self._h = _approach(self._h, th, self._STEP_HOVER)
        try:
            self._draw()
            if self._a != ta or self._h != th:
                self._job = self.after(self._FRAME_MS, self._tick)
        except tk.TclError:   # widget destroyed mid-animation
            pass

    def _fill(self):
        c = gfx.mix(theme.SIDEBAR_BG, theme.NAV_HOVER, round(self._h * 8) / 8)
        return gfx.mix(c, theme.NAV_ACTIVE, round(self._a * 8) / 8)

    def _draw(self):
        self.delete("all")
        fill = self._fill()
        if fill != theme.SIDEBAR_BG:
            gloss = round(max(self._a, self._h * 0.6) * 8) / 8
            ph = self._bg.get((fill, gloss)) or self._bg.setdefault(
                (fill, gloss), gfx.photo(gfx.glass_pill_image(self.w - px(20), self.h - px(6), px(12), fill, gloss)))
            self.create_image(px(10), px(3), image=ph, anchor="nw")
        if self._a > 0:   # accent bar grows out of the middle
            half = (self.h / 2 - px(12)) * _ease(self._a)
            self.create_rectangle(0, self.h / 2 - half, px(4), self.h / 2 + half, fill=theme.ACCENT, width=0)
        if self.compact:
            self.create_image(self.w // 2, self.h // 2, image=self._icon)
            return
        self.create_image(px(26), self.h // 2, image=self._icon, anchor="w")
        self.create_image(px(26) + self._icon_size + px(14), self.h // 2 - 1, image=self._text, anchor="w")

    def set_active(self, active: bool):
        if active != self.active:
            self.active = active
            self._animate()


class Sidebar(tk.Frame):
    def __init__(self, parent, on_select: Callable[[str], None], width: int):
        super().__init__(parent, bg=theme.SIDEBAR_BG, width=width)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.items: dict[str, NavItem] = {}

        self.width, self.compact = width, False
        self.logo = tk.Canvas(self, width=width, height=px(96), bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0)
        self.logo.pack(fill="x")
        self._logo_imgs = [gfx.photo(gfx.icon_image("logo", px(46))),
                           gfx.photo(gfx.text_image(APP_NAME, px(31), theme.TEXT_SOFT)),
                           gfx.photo(gfx.text_image(VERSION_LABEL, px(17), theme.TEXT_SOFT))]
        self._name_w = gfx.text_size(APP_NAME, px(31))[0]
        self._draw_logo()

        tk.Frame(self, bg=theme.SIDEBAR_BG, height=px(14)).pack()
        for key, label, icon in NAV_ITEMS:
            item = NavItem(self, label, icon, lambda k=key: on_select(k), width)
            item.pack(fill="x", pady=px(5))
            self.items[key] = item

        self.bottom = tk.Frame(self, bg=theme.SIDEBAR_BG)
        self.bottom.pack(side="bottom", fill="x", pady=px(26))
        self.links = [
            IconButton(self.bottom, "discord", 40, command=lambda: webbrowser.open_new_tab(DISCORD_URL),
                       tip="Join our Discord", bg=theme.SIDEBAR_BG),
            IconButton(self.bottom, "globe", 40, command=lambda: webbrowser.open_new_tab(WEBSITE_URL),
                       tip="Open the tetoMIDI website", bg=theme.SIDEBAR_BG),
        ]
        self._pack_links()

    def _draw_logo(self):
        c = self.logo
        c.delete("all")
        if self.compact:
            c.create_image(self.width // 2, px(52), image=self._logo_imgs[0])
            return
        c.create_image(px(34), px(52), image=self._logo_imgs[0], anchor="w")
        c.create_image(px(34) + px(46) + px(12), px(50), image=self._logo_imgs[1], anchor="w")
        c.create_image(px(34) + px(46) + px(12) + self._name_w + px(6), px(56), image=self._logo_imgs[2], anchor="w")

    def _pack_links(self):
        for b in self.links:
            b.pack_forget()
        if self.compact:
            self.bottom.pack_configure(padx=0)
            for b in self.links:
                b.pack(side="top", pady=px(8))
        else:
            self.bottom.pack_configure(padx=px(30))
            self.links[0].pack(side="left", padx=(0, px(20)))
            self.links[1].pack(side="left")

    def set_compact(self, compact: bool):
        """Icon-only sidebar for narrow windows."""
        if compact == self.compact:
            return
        self.compact = compact
        self.width = px(86) if compact else px(262)
        self.configure(width=self.width)
        self.logo.configure(width=self.width)
        self._draw_logo()
        for item in self.items.values():
            item.resize(self.width, compact)
        self._pack_links()

    def set_active(self, key: str):
        for k, item in self.items.items():
            item.set_active(k == key)
