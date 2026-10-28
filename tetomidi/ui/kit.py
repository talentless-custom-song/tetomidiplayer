"""Small widget kit drawn with Pillow images on Tk canvases (pixel-identical on Windows/Linux)."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import gfx, theme
from .gfx import px


def _bg_of(parent) -> str:
    try:
        return parent.cget("bg")
    except tk.TclError:
        return theme.MAIN_BG


class _Base(tk.Canvas):
    def __init__(self, parent, width: int, height: int, **kw):
        super().__init__(parent, width=width, height=height, bg=kw.pop("bg", _bg_of(parent)),
                         highlightthickness=0, bd=0, **kw)
        self._refs: list = []

    def _put(self, x, y, pil, anchor="center", tag=None):
        ph = gfx.photo_cached(pil)
        self._refs.append(ph)
        return self.create_image(x, y, image=ph, anchor=anchor, tags=(tag,) if tag else ())


class Label(_Base):
    """Text drawn with the bundled serif font."""

    def __init__(self, parent, text="", size=17, color=theme.TEXT, bold=False, anchor="w", **kw):
        self._size, self._color, self._bold, self._anchor, self._text = px(size), color, bold, anchor, text
        w, h = gfx.text_size(text or " ", self._size, bold)
        super().__init__(parent, w, h, **kw)
        self._draw()

    def _draw(self):
        self.delete("all")
        self._refs.clear()
        img = gfx.text_image(self._text or " ", self._size, self._color, self._bold)
        self.configure(width=img.width, height=img.height)
        self._put(0, 0, img, anchor="nw")

    def set(self, text: str, color: str | None = None):
        if text == self._text and (color is None or color == self._color):
            return
        self._text = text
        if color:
            self._color = color
        self._draw()


class PillButton(_Base):
    def __init__(self, parent, text, width, height, bg=theme.PILL, hover=theme.PILL_HOVER, fg=theme.TEXT,
                 size=17, radius=None, bold=False, command: Callable | None = None,
                 icon: str | None = None, icon_size: int = 18, **kw):
        self._wd, self._ht = px(width), px(height)
        super().__init__(parent, self._wd, self._ht, **kw)
        self._text, self._fg, self._size, self._bold = text, fg, px(size), bold
        self._icon, self._icon_px = icon, px(icon_size)   # optional vector glyph shown before the text
        self._radius = px(radius if radius is not None else height / 2)
        self._bg, self._hover = bg, hover
        self.command = command
        self._over = self._down = False
        self._t = 0.0          # animated hover progress 0..1
        self._job = None
        self._enabled = True
        self.configure(cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_over(True))
        self.bind("<Leave>", lambda e: self._set_over(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _set_over(self, v):
        self._over = v
        if not v:
            self._down = False
        self._animate()

    def _animate(self):
        if self._job is None:
            self._tick()

    def _tick(self):
        self._job = None
        target = 1.0 if self._over else 0.0
        self._t = target if abs(target - self._t) <= 0.2 else self._t + (0.2 if target > self._t else -0.2)
        try:
            self._draw()
            if self._t != target:
                self._job = self.after(16, self._tick)
        except tk.TclError:   # destroyed mid-animation
            pass

    def _press(self, _):
        if self._enabled:
            self._down = True
            self._draw()

    def _release(self, e):
        was = self._down
        self._down = False
        self._draw()
        if was and self._enabled and 0 <= e.x <= self._wd and 0 <= e.y <= self._ht and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        self._refs.clear()
        if not self._enabled:
            fill = gfx.mix(self._bg, theme.MAIN_BG, 0.5)
        elif self._down:
            fill = gfx.mix(self._hover, "#000000", 0.2)
        else:
            fill = gfx.mix(self._bg, self._hover, round(self._t * 6) / 6)
        self._put(0, 0, gfx.glass_pill_image(self._wd, self._ht, self._radius, fill), anchor="nw")
        color = self._fg if self._enabled else theme.TEXT_FAINT
        if self._icon:
            glyph = gfx.control_icon(self._icon, self._icon_px, color)
            text = gfx.text_image(self._text, self._size, color, self._bold) if self._text else None
            gap = px(8)
            total = glyph.width + (gap + text.width if text else 0)
            x0 = (self._wd - total) // 2
            self._put(x0, self._ht // 2, glyph, anchor="w")
            if text:
                self._put(x0 + glyph.width + gap, self._ht // 2 - 1, text, anchor="w")
        elif self._text:
            self._put(self._wd // 2, self._ht // 2 - 1, gfx.text_image(self._text, self._size, color, self._bold))

    def set_text(self, text: str):
        if text != self._text:
            self._text = text
            self._draw()

    def set_colors(self, bg: str, hover: str):
        if (bg, hover) != (self._bg, self._hover):
            self._bg, self._hover = bg, hover
            self._draw()

    def set_enabled(self, enabled: bool):
        if enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self._draw()


class Toggle(_Base):
    def __init__(self, parent, value=False, command: Callable[[bool], None] | None = None, width=85, height=40, **kw):
        self._wd, self._ht = px(width), px(height)
        super().__init__(parent, self._wd, self._ht, **kw)
        self.value, self.command = bool(value), command
        self._t = 1.0 if self.value else 0.0   # animated knob position 0..1
        self._job = None
        self.configure(cursor="hand2")
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    _STEPS, _STEP = 14, 0.085   # image steps and progress per 16 ms frame (about 180 ms end to end)

    def _draw(self):
        self.delete("all")
        self._refs.clear()
        self._put(0, 0, gfx.glass_toggle_image(self._wd, self._ht, round(self._t * self._STEPS), self._STEPS), anchor="nw")

    def _animate(self):
        if self._job is None:
            self._tick()

    def _tick(self):
        self._job = None
        target = 1.0 if self.value else 0.0
        self._t = target if abs(target - self._t) <= self._STEP else self._t + (self._STEP if target > self._t else -self._STEP)
        try:
            self._draw()
            if self._t != target:
                self._job = self.after(16, self._tick)
        except tk.TclError:   # destroyed mid-animation
            pass

    def _click(self, e):
        if 0 <= e.x <= self._wd and 0 <= e.y <= self._ht:
            self.set(not self.value)
            if self.command:
                self.command(self.value)

    def set(self, value: bool):
        if bool(value) != self.value:
            self.value = bool(value)
            self._animate()


class Slider(_Base):
    """Thin white track with a round black thumb. ``on_drag`` while dragging, ``on_commit`` on release."""

    def __init__(self, parent, value=0.0, on_drag=None, on_commit=None, height=26, **kw):
        self._ht = px(height)
        super().__init__(parent, 100, self._ht, **kw)
        self.configure(cursor="hand2")
        self._d = px(20)
        self.value = float(value)
        self.on_drag, self.on_commit = on_drag, on_commit
        self.dragging = False
        self._thumb_ph = gfx.photo(gfx.thumb_image(self._d))
        self._enabled = True
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def _x_for(self, v):
        w = max(self.winfo_width(), 2)
        r = self._d / 2
        return r + (w - 2 * r) * min(max(v, 0.0), 1.0)

    def _draw(self):
        self.delete("all")
        w, cy = max(self.winfo_width(), 2), self._ht // 2
        r = self._d // 2
        t = max(2, px(2))
        self.create_rectangle(r, cy - t // 2, w - r, cy - t // 2 + t, fill="#f1f1f1" if self._enabled else "#555", width=0)
        self.create_image(self._x_for(self.value), cy, image=self._thumb_ph)

    def _value_at(self, x):
        w, r = max(self.winfo_width(), 2), self._d / 2
        return min(max((x - r) / max(w - 2 * r, 1), 0.0), 1.0)

    def _press(self, e):
        if not self._enabled:
            return
        self.dragging = True
        self.value = self._value_at(e.x)
        self._draw()
        if self.on_drag:
            self.on_drag(self.value)

    def _motion(self, e):
        if self.dragging:
            self.value = self._value_at(e.x)
            self._draw()
            if self.on_drag:
                self.on_drag(self.value)

    def _release(self, e):
        if self.dragging:
            self.dragging = False
            self.value = self._value_at(e.x)
            self._draw()
            if self.on_commit:
                self.on_commit(self.value)

    def set(self, value: float):
        if self.dragging:
            return
        value = min(max(value, 0.0), 1.0)
        if abs(value - self.value) > 1e-4:
            self.value = value
            self._draw()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._draw()


class DarkScrollbar(_Base):
    """Slim vertical scrollbar in the app's dark colours (Tk's own scrollbar ignores colours on Windows).

    Works like ``tk.Scrollbar``: pass ``command=listbox.yview`` and give ``listbox.configure(yscrollcommand=bar.set)``.
    """

    THUMB, THUMB_HOVER = "#3a3738", "#5a5758"

    def __init__(self, parent, command: Callable | None = None, width: int = 10, track: str = theme.CONSOLE_BG, **kw):
        self._wd = px(width)
        super().__init__(parent, self._wd, px(10), bg=track, **kw)
        self.command = command
        self._first, self._last = 0.0, 1.0
        self._grab = None          # offset of the pointer inside the thumb while it is dragged
        self._over = False
        self._rect = None          # (top, height) of the thumb, or None when everything is visible
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1))
        self.bind("<Button-4>", lambda e: self._scroll(-1))
        self.bind("<Button-5>", lambda e: self._scroll(1))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw()

    def _hover(self, over: bool):
        self._over = over
        self._draw()

    def _scroll(self, step: int):
        if self.command:
            self.command("scroll", step * 3, "units")

    def _geometry(self):
        h = self.winfo_height()
        span = self._last - self._first
        if h < 4 or span >= 0.999:
            return None
        thumb = min(h, max(px(28), int(h * span)))
        room = max(h - thumb, 1)
        top = int(round(self._first / max(1.0 - span, 1e-6) * room))
        return min(max(top, 0), h - thumb), thumb, room

    def _draw(self):
        self.delete("all")
        self._refs.clear()
        g = self._geometry()
        self._rect = None if g is None else g[:2]
        if g is None:
            return
        top, thumb, _room = g
        inset = px(2)
        color = self.THUMB_HOVER if (self._over or self._grab is not None) else self.THUMB
        w = max(self._wd - 2 * inset, 2)
        self._put(inset, top, gfx.pill_image(w, thumb, w // 2, color), anchor="nw")

    def _move_to_thumb_top(self, top: float):
        g = self._geometry()
        if g is None or not self.command:
            return
        _t, thumb, room = g
        span = self._last - self._first
        frac = min(max(top / room, 0.0), 1.0) * (1.0 - span)
        self.command("moveto", frac)

    def _press(self, e):
        if self._rect is None:
            return
        top, thumb = self._rect
        if top <= e.y <= top + thumb:
            self._grab = e.y - top
        else:   # a click on the track centres the thumb there
            self._grab = thumb / 2
            self._move_to_thumb_top(e.y - self._grab)
        self._draw()

    def _motion(self, e):
        if self._grab is not None:
            self._move_to_thumb_top(e.y - self._grab)

    def _release(self, _e):
        self._grab = None
        self._draw()


class Badge(_Base):
    """Black hexagon with centred text (tempo / time read-outs)."""

    def __init__(self, parent, text="", width=60, height=28, size=16, **kw):
        self._wd, self._ht, self._size = px(width), px(height), px(size)
        super().__init__(parent, self._wd, self._ht, **kw)
        self._text = None
        self.set(text)

    def set(self, text: str):
        if text == self._text:
            return
        self._text = text
        self.delete("all")
        self._refs.clear()
        self._put(0, 0, gfx.hex_image(self._wd, self._ht, theme.PILL), anchor="nw")
        self._put(self._wd // 2, self._ht // 2 - 1, gfx.text_image(text, self._size, theme.TEXT))


class RoundButton(_Base):
    """Round, icon-only button: a glass disc with a vector glyph (transport controls)."""

    def __init__(self, parent, glyph: str, diameter: int, fill: str, hover: str, glyph_color: str,
                 command: Callable | None = None, tip: str = "", gloss: float = 0.6, **kw):
        self._d = px(diameter)
        super().__init__(parent, self._d, self._d, **kw)
        self.glyph, self.command = glyph, command
        self._fill, self._hover, self._glyph_color, self._gloss = fill, hover, glyph_color, gloss
        self._over = self._down = False
        self.configure(cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_over(True))
        self.bind("<Leave>", lambda e: self._set_over(False))
        self.bind("<ButtonPress-1>", lambda e: self._press())
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()
        if tip:
            Tooltip(self, tip)

    def _set_over(self, v: bool):
        self._over = v
        if not v:
            self._down = False
        self._draw()

    def _press(self):
        self._down = True
        self._draw()

    def _release(self, e):
        was = self._down
        self._down = False
        self._draw()
        if was and 0 <= e.x <= self._d and 0 <= e.y <= self._d and self.command:
            self.command()

    def _draw(self):
        fill = self._hover if self._over else self._fill
        if self._down:
            fill = gfx.mix(fill, "#000000", 0.25)
        img = gfx.round_button_image(self._d, fill, self.glyph, self._glyph_color, self._gloss)
        self.delete("all")
        self._refs.clear()
        self._put(0, 0, img, anchor="nw")

    def set_glyph(self, glyph: str):
        if glyph != self.glyph:
            self.glyph = glyph
            self._draw()

    def set_colors(self, fill: str, hover: str, glyph_color: str):
        if (fill, hover, glyph_color) != (self._fill, self._hover, self._glyph_color):
            self._fill, self._hover, self._glyph_color = fill, hover, glyph_color
            self._draw()


class IconButton(_Base):
    def __init__(self, parent, icon: str, size=28, command: Callable | None = None, tip: str = "", **kw):
        self._s = px(size)
        super().__init__(parent, self._s, self._s, **kw)
        self.icon, self.command = icon, command
        self._imgs = {a: gfx.photo(gfx.icon_image(icon, self._s, a)) for a in (0.78, 1.0, 0.55)}
        self.configure(cursor="hand2")
        self._state = 0.78
        self.bind("<Enter>", lambda e: self._show(1.0))
        self.bind("<Leave>", lambda e: self._show(0.78))
        self.bind("<ButtonPress-1>", lambda e: self._show(0.55))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()
        if tip:
            Tooltip(self, tip)

    def _show(self, a):
        self._state = a
        self._draw()

    def _release(self, e):
        self._show(1.0)
        if 0 <= e.x <= self._s and 0 <= e.y <= self._s and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        self.create_image(0, 0, image=self._imgs[self._state], anchor="nw")


class Tooltip:
    def __init__(self, widget, text: str, enabled: Callable[[], bool] | None = None):
        self.widget, self.text, self.win, self._job = widget, text, None, None
        self.enabled = enabled
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _):
        if self.enabled is None or self.enabled():
            self._job = self.widget.after(450, self._show)

    def _show(self):
        if self.win:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry(f"+{x}+{y}")
        tk.Label(self.win, text=self.text, bg="#000000", fg="#ffffff", bd=1, relief="solid",
                 padx=6, pady=2, font=("TkDefaultFont", 9)).pack()

    def _hide(self, _=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self.win:
            self.win.destroy()
            self.win = None


class Stepper(tk.Frame):
    """[<] value [>] selector for a list of options."""

    def __init__(self, parent, options: list[str], index=0, command: Callable[[int], None] | None = None, width=150, **kw):
        super().__init__(parent, bg=kw.pop("bg", _bg_of(parent)))
        self.options, self.index, self.command = options, index, command
        self._left = PillButton(self, "‹", 34, 34, size=20, command=lambda: self._step(-1))
        self._badge = Badge(self, options[index], width=width, height=34, size=16)
        self._right = PillButton(self, "›", 34, 34, size=20, command=lambda: self._step(1))
        self._left.pack(side="left")
        self._badge.pack(side="left", padx=px(4))
        self._right.pack(side="left")

    def _step(self, d):
        self.set((self.index + d) % len(self.options))
        if self.command:
            self.command(self.index)

    def set(self, index: int):
        self.index = index % len(self.options)
        self._badge.set(self.options[self.index])


class ScrollFrame(tk.Frame):
    """Vertically scrolling container with a slim custom scrollbar."""

    def __init__(self, parent, bg=theme.MAIN_BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Canvas(self, bg=bg, width=px(8), highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._sync())
        self.canvas.bind("<Configure>", self._resize)
        self.bar.bind("<ButtonPress-1>", self._bar_drag)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        for w in (self, self.canvas, self.inner):
            w.bind("<MouseWheel>", self._wheel, add="+")
            w.bind("<Button-4>", lambda e: self._scroll(-3), add="+")
            w.bind("<Button-5>", lambda e: self._scroll(3), add="+")

    def bind_wheel_recursive(self, widget=None):
        widget = widget or self.inner
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", lambda e: self._scroll(-3), add="+")
        widget.bind("<Button-5>", lambda e: self._scroll(3), add="+")
        for child in widget.winfo_children():
            self.bind_wheel_recursive(child)

    def _resize(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self._sync()

    def _sync(self):
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        self._draw_bar()

    def _wheel(self, e):
        self._scroll(-1 if e.delta > 0 else 1, units=3)

    def _scroll(self, n, units=1):
        total, view = self.inner.winfo_reqheight(), self.canvas.winfo_height()
        if total <= view:
            return
        self.canvas.yview_moveto(min(max(self.canvas.yview()[0] + n * units * 24 / total, 0.0), 1.0))
        self._draw_bar()

    def _draw_bar(self):
        self.bar.delete("all")
        top, bottom = self.canvas.yview()
        if bottom - top >= 0.999:
            return
        h = self.bar.winfo_height()
        self.bar.create_rectangle(1, top * h, self.bar.winfo_width() - 1, max(bottom * h, top * h + px(24)),
                                  fill="#3a3738", width=0)

    def _bar_drag(self, e):
        h = max(self.bar.winfo_height(), 1)
        top, bottom = self.canvas.yview()
        self.canvas.yview_moveto(min(max(e.y / h - (bottom - top) / 2, 0.0), 1.0))
        self._draw_bar()
