"""Falling-note piano roll (piano keyboard, drum lanes or guitar strings).

Pure view: it draws whatever ``RollData`` it is given at the playback position it is asked to
render, so the animation is driven by the transport clock and never runs on its own.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk

from ..config import Config
from ..mappings import DRUM_LABELS, DRUM_LANES, DRUM_NOTE_TO_NAME, GUITAR_STRING_LABELS, is_black
from ..rollmodel import RollData
from . import gfx, theme
from .gfx import px

MAX_NOTES_DRAWN = 700
VEL_SHADES = 8
PERSP_DEPTH = 3.0    # 3D piano roll: the top edge of the roll is this many times farther away than the keyboard
DEPTH_FADE = 0.5     # 3D piano roll: how much far notes darken towards the background
log = logging.getLogger(__name__)


class RollView(tk.Canvas):
    def __init__(self, parent, mode: str, cfg: Config):
        super().__init__(parent, bg=theme.ROLL_BG, highlightthickness=0, bd=0, width=px(300), height=px(200))
        self.mode, self.cfg = mode, cfg
        self.data = RollData(mode)
        self.pos = 0.0
        self.W = self.H = 0
        self._refs: list = []
        self._last_pos = None        # position of the last drawn frame (an unchanged position needs no redraw)
        self._caption = ""
        self._caption_item = None
        self._caption_ph = None
        self._bg_key, self._bg_ph = None, None   # custom background picture (see _background)
        self._reset_items()
        self.bind("<Configure>", lambda e: self._relayout())

    # ----------------------------------------------------------- layout
    def _reset_items(self):
        self._pool: list[int] = []
        self._pool_fill: list[str] = []
        self._pool_used = 0
        self._extrusion_pool: list[tuple[int, int]] = []
        self._extrusion_fill: list[tuple[str, str]] = []
        self._extrusion_used = 0
        self._lane_rect: dict[int, tuple[float, float]] = {}
        self._key_item: dict[int, int] = {}
        self._key_base: dict[int, str] = {}
        self._pad_label: dict[int, int] = {}
        self._fret_imgs: dict[int, object] = {}
        self._glow_item: dict[int, int] = {}
        self._glow_ph = None
        self._lit: set[int] = set()
        self._empty_item = None
        self._empty_shown = True
        self._caption_item = None
        self._refs = []

    def palette(self):
        return theme.NOTE_PALETTES.get(self.cfg.get("roll.noteColor", "blue"), theme.NOTE_PALETTES["blue"])

    def set_data(self, data: RollData):
        self.data = data
        self._relayout()

    def refresh_style(self):
        self._relayout()

    def _relayout(self):
        W, H = self.winfo_width(), self.winfo_height()
        if W < 20 or H < 20:
            return
        self.delete("all")
        self._reset_items()
        self.W, self.H = W, H
        bg = self._background(W, H)
        if bg is not None:   # the bottom layer: notes, keys and labels are drawn over it
            self.create_image(0, 0, image=bg, anchor="nw")
        self.kb_h = min(max(int(H * 0.17), px(40)), px(66))
        self.hit = H - self.kb_h
        pal = self.palette()
        if self.mode == "piano":
            self._layout_piano(pal)
        else:
            self._layout_lanes(pal)
        gw, gh = max(px(44), int(self._glow_width() * 2.6)), px(54)
        self._glow_ph = gfx.photo(gfx.glow_image(gw, gh, pal[2]))
        self._empty_ph = gfx.photo(gfx.text_image("Select a MIDI file to start", px(18), theme.TEXT_FAINT))
        self._empty_item = self.create_image(W // 2, self.hit // 2, image=self._empty_ph, tags=("empty",))
        self._lit = set()
        self._draw_caption()
        self.render(self.pos, force=True)

    # ----------------------------------------------------------- caption (text on top of the roll)
    def set_caption(self, text: str):
        if text != self._caption:
            self._caption = text
            self._draw_caption()

    def _draw_caption(self):
        if self._caption_item is not None:
            self.delete(self._caption_item)
            self._caption_item = None
        text, size = self._caption, px(15)
        if not text or self.W < 20:
            return
        avail = self.W - px(28)
        if gfx.text_size(text, size, True)[0] > avail:   # shorten with an ellipsis instead of overflowing
            while len(text) > 1 and gfx.text_size(text + "…", size, True)[0] > avail:
                text = text[:-1]
            text = text.rstrip() + "…"
        self._caption_ph = gfx.photo_cached(gfx.caption_image(text, size, theme.TEXT_SOFT))
        self._caption_item = self.create_image(px(14), px(8), image=self._caption_ph, anchor="nw", tags=("caption",))

    def _background(self, W: int, H: int):
        """Photo of the custom background for this size, or None (plain roll colour)."""
        path = self.cfg.get("roll.background", "")
        if not path or not os.path.isfile(path):
            return None
        dim = float(self.cfg.get("roll.backgroundDim", 50))
        key = (path, os.path.getmtime(path), W, H, dim)
        if key != self._bg_key:
            self._bg_key = key
            try:
                self._bg_ph = gfx.photo(gfx.background_image(path, W, H, dim))
            except (OSError, ValueError):
                log.warning("background image %s could not be read", path)
                self._bg_ph = None
        return self._bg_ph

    # ----------------------------------------------------------- 3D perspective (piano roll only)
    def _is3d(self) -> bool:
        return self.mode == "piano" and bool(self.cfg.get("roll.piano3D", False))

    def _horizon(self) -> float:
        """Screen y of the vanishing line: chosen so the top edge of the roll lands on y = 0."""
        return -self.hit / (PERSP_DEPTH - 1)

    def _persp_y(self, z: float) -> float:
        """Screen y of the depth ``z`` (1 = keyboard / hit line, PERSP_DEPTH = top edge)."""
        yh = self._horizon()
        return yh + (self.hit - yh) / z

    def _persp_x(self, x: float, z: float) -> float:
        """Screen x of the lane edge ``x`` (measured on the hit line) at depth ``z``: the lanes converge."""
        cx = self.W / 2
        return cx + (x - cx) / z

    def _guide_line(self, x: float):
        if self._is3d():
            self.create_line(x, self.hit, self._persp_x(x, PERSP_DEPTH), 0, fill=theme.LANE_LINE)
        else:
            self.create_line(x, 0, x, self.hit, fill=theme.LANE_LINE)

    def _note_box(self, rect, s, e, pos, look, pps, min_h, is3d):
        """Canvas coordinates of one note: a rectangle (x0, y0, x1, y1), or in 3D the four corners of the
        note's quad (bottom-left, bottom-right, top-right, top-left).  None when the note is off the top."""
        if not is3d:
            y1 = min(self.hit - (s - pos) * pps, self.hit)
            y0 = max(self.hit - (e - pos) * pps, 0)
            if y1 - y0 < min_h:
                y0 = y1 - min_h
            if y1 <= 0:
                return None
            return rect[0], y0, rect[1], y1
        us = min(max((s - pos) / look, 0.0), 1.0)     # 0 = on the hit line, 1 = top edge of the roll
        ue = min(max((e - pos) / look, 0.0), 1.0)
        zs = 1 + us * (PERSP_DEPTH - 1)
        ze = 1 + ue * (PERSP_DEPTH - 1)
        ys, ye = self._persp_y(zs), self._persp_y(ze)
        if ys - ye < min_h:
            ye = ys - min_h
        if ys <= 0:
            return None
        return (self._persp_x(rect[0], zs), ys, self._persp_x(rect[1], zs), ys,
                self._persp_x(rect[1], ze), ye, self._persp_x(rect[0], ze), ye)

    def _glow_width(self) -> float:
        rects = list(self._lane_rect.values())
        return (rects[0][1] - rects[0][0]) if rects else px(20)

    def _layout_piano(self, pal):
        lo, hi = self.data.key_range
        whites = [p for p in range(lo, hi + 1) if not is_black(p)]
        ww = self.W / len(whites)
        wx = {p: i * ww for i, p in enumerate(whites)}
        for p in whites:                      # octave guide lines (before each C)
            if p % 12 == 0 and p != lo:
                self._guide_line(wx[p])
        for p in whites:
            self._lane_rect[p] = (wx[p] + 1, wx[p] + ww - 1)
            self._key_item[p] = self.create_rectangle(wx[p], self.hit, wx[p] + ww, self.H,
                                                      fill=theme.KEY_WHITE, outline=theme.KEY_WHITE_EDGE)
            self._key_base[p] = theme.KEY_WHITE
        bw, bh = ww * 0.6, self.kb_h * 0.62
        for p in range(lo, hi + 1):
            if is_black(p) and (p - 1) in wx:
                cx = wx[p - 1] + ww
                self._lane_rect[p] = (cx - bw / 2, cx + bw / 2)
                self._key_item[p] = self.create_rectangle(cx - bw / 2, self.hit, cx + bw / 2, self.hit + bh,
                                                          fill=theme.KEY_BLACK, outline="#000000")
                self._key_base[p] = theme.KEY_BLACK
        if self.cfg.get("roll.keyLabels", True):
            self._piano_key_labels(wx, ww, bh)

    def _piano_key_labels(self, wx, ww, bh):
        """The computer key that plays each piano key (black keys show the shifted symbol / capital, like the
        macro presses it).  Keys outside 36-96 are pressed with CTRL and get a blue letter."""
        pm = self.cfg.get("piano.pianoMap", {}) or {}
        letters = pm.get("61keyMap", {})
        extra = {**pm.get("88keyMap", {}).get("lowNotes", {}), **pm.get("88keyMap", {}).get("highNotes", {})}
        lo, hi = self.data.key_range
        size = max(px(7), min(px(15), int(ww * 0.66)))
        bw = ww * 0.6
        for p in range(lo, hi + 1):
            key = letters.get(str(p))
            ctrl = key is None
            if ctrl:
                key = extra.get(str(p))
            if not key:
                continue
            black = is_black(p)
            if black:
                if (p - 1) not in wx:
                    continue
                cx, cy = wx[p - 1] + ww, self.hit + bh - size * 0.95
                color = theme.KEY_LABEL_CTRL_BLACK if ctrl else theme.KEY_LABEL_BLACK
            else:
                cx, cy = wx[p] + ww / 2, self.H - size * 0.85
                color = theme.KEY_LABEL_CTRL_WHITE if ctrl else theme.KEY_LABEL_WHITE
            ph = gfx.photo_cached(gfx.text_image(key, size, color, True))
            self._refs.append(ph)
            self.create_image(cx, cy, image=ph, tags=("keylabel",))

    def _lane_labels(self):
        if self.mode == "drums":
            dmap = self.cfg.get("drums.drumsMap", {})
            out = []
            for label, notes in DRUM_LANES:
                keys = []
                for n in notes:
                    k = dmap.get(DRUM_NOTE_TO_NAME.get(n, ""), "")
                    if k and k.upper() not in keys:
                        keys.append(k.upper())
                out.append((label, "/".join(keys)))
            return out
        return [(GUITAR_STRING_LABELS[i], "") for i in range(6)]

    def _layout_lanes(self, pal):
        labels = self._lane_labels()
        n = len(labels)
        lw = self.W / n
        for i, (label, hint) in enumerate(labels):
            x0, x1 = i * lw, (i + 1) * lw
            if i:
                self.create_line(x0, 0, x0, self.hit, fill=theme.LANE_LINE)
            self._lane_rect[i] = (x0 + min(lw * 0.18, px(14)), x1 - min(lw * 0.18, px(14)))
            self._key_item[i] = self.create_rectangle(x0 + 3, self.hit + 3, x1 - 3, self.H - 3,
                                                      fill=theme.PAD, outline="#232830")
            self._key_base[i] = theme.PAD
            size = px(15) if self.mode == "drums" else px(20)
            while size > px(9) and gfx.text_size(label, size)[0] > lw - 10:   # shrink to fit narrow lanes
                size -= 1
            ph = gfx.photo(gfx.text_image(label, size, theme.TEXT_SOFT, bold=self.mode == "guitar"))
            self._refs.append(ph)
            self.create_image((x0 + x1) / 2, self.hit + self.kb_h * (0.38 if hint else 0.5), image=ph)
            if hint:
                hsize = px(13)
                while hsize > px(8) and gfx.text_size(hint, hsize)[0] > lw - 8:
                    hsize -= 1
                hp = gfx.photo(gfx.text_image(hint, hsize, theme.TEXT_MUTED))
                self._refs.append(hp)
                self.create_image((x0 + x1) / 2, self.hit + self.kb_h * 0.72, image=hp)
            if self.mode == "guitar":  # fret read-out of the string, shown while lit
                self._fret_imgs[i] = {f: gfx.photo(gfx.text_image(str(f), px(16), "#ffffff", True)) for f in range(0, 16)}
                self._pad_label[i] = self.create_image((x0 + x1) / 2, self.hit + self.kb_h * 0.78, state="hidden")

    # ----------------------------------------------------------- frame
    def _shade(self, vel_idx: int) -> str:
        return gfx.mix(theme.ROLL_BG, self.palette()[0], 0.38 + 0.62 * (vel_idx + 1) / VEL_SHADES)

    def render(self, pos: float, force: bool = False):
        if not self._lane_rect:
            return
        if not force and pos == self._last_pos:   # paused / stopped: the picture cannot change
            return
        self._last_pos = pos
        self.pos = pos
        data = self.data
        body, edge, lit_col = self.palette()
        look = max(0.5, float(self.cfg.get("roll.fallSeconds", 3.0)))
        pps = self.hit / look
        lo, hi = data.window(pos, look)
        use_vel = self.mode == "piano" and bool(self.cfg.get("piano.velocity", False))
        is3d = self._is3d()
        lit: set[int] = set()
        used = 0
        extrusion_used = 0
        starts, ends, lanes, vels = data.starts, data.ends, data.lanes, data.vels
        for i in range(lo, hi):
            e = ends[i]
            if e <= pos:
                continue
            rect = self._lane_rect.get(lanes[i])
            if rect is None:
                continue
            s = starts[i]
            min_h = 3 if self.mode == "piano" else px(7)
            box = self._note_box(rect, s, e, pos, look, pps, min_h, is3d)
            if box is None:
                continue
            active = s <= pos
            if active:
                lit.add(lanes[i])
            fill = lit_col if active else (self._shade(min(vels[i] * VEL_SHADES // 128, VEL_SHADES - 1)) if use_vel else body)
            if is3d and not active:   # the farther a note is, the darker it is
                depth = min(max((s - pos) / look, 0.0), 1.0)
                fill = gfx.mix(fill, theme.ROLL_BG, DEPTH_FADE * depth)
            if is3d:
                x0, y0, x1, y1, x2, y2, x3, y3 = box
                dx, dy = px(3), px(4)
                faces = (
                    (x0, y0, x1, y1, x1 + dx, y1 + dy, x0 + dx, y0 + dy),
                    (x1, y1, x2, y2, x2 + dx, y2 + dy, x1 + dx, y1 + dy),
                )
                shades = (gfx.mix(fill, "#000000", 0.28), gfx.mix(fill, "#000000", 0.48))
                if extrusion_used == len(self._extrusion_pool):
                    items = tuple(self.create_polygon(*face, fill=color, outline=edge, width=1)
                                  for face, color in zip(faces, shades))
                    self._extrusion_pool.append(items)
                    self._extrusion_fill.append(shades)
                else:
                    items = self._extrusion_pool[extrusion_used]
                    if self._extrusion_fill[extrusion_used] != shades:
                        for item, color in zip(items, shades):
                            self.itemconfigure(item, fill=color)
                        self._extrusion_fill[extrusion_used] = shades
                for item, face in zip(items, faces):
                    self.coords(item, *face)
                    self.itemconfigure(item, state="normal")
                extrusion_used += 1
            if used == len(self._pool):
                if is3d:
                    self._pool.append(self.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill=fill, outline=edge, width=1))
                else:
                    self._pool.append(self.create_rectangle(0, 0, 0, 0, fill=fill, outline=edge, width=1))
                self._pool_fill.append(fill)
                if self._caption_item is not None:   # new items are created on top: keep the text above the notes
                    self.tag_raise(self._caption_item)
            item = self._pool[used]
            self.coords(item, *box)
            if self._pool_fill[used] != fill:
                self.itemconfigure(item, fill=fill)
                self._pool_fill[used] = fill
            if used >= self._pool_used:
                self.itemconfigure(item, state="normal")
            used += 1
            if used >= MAX_NOTES_DRAWN:
                break
        for k in range(used, self._pool_used):
            self.itemconfigure(self._pool[k], state="hidden")
        self._pool_used = used
        for i in range(extrusion_used, self._extrusion_used):
            for item in self._extrusion_pool[i]:
                self.itemconfigure(item, state="hidden")
        self._extrusion_used = extrusion_used

        want_empty = len(data) == 0
        if want_empty != self._empty_shown:
            self._empty_shown = want_empty
            self.itemconfigure(self._empty_item, state="normal" if want_empty else "hidden")

        if lit != self._lit or force:
            for lane in self._lit - lit:
                self._unlight(lane)
            for lane in lit - self._lit:
                self._light(lane, lit_col)
            self._lit = lit
        if self.mode == "guitar" and lit:
            self._update_frets(pos, lo, hi)

    def _light(self, lane: int, color: str):
        item = self._key_item.get(lane)
        if item is not None:
            self.itemconfigure(item, fill=color)
        if self.cfg.get("roll.glow", True) and lane in self._lane_rect and self._glow_ph is not None:
            x0, x1 = self._lane_rect[lane]
            g = self._glow_item.get(lane)
            if g is None:
                g = self._glow_item[lane] = self.create_image((x0 + x1) / 2, self.hit, image=self._glow_ph, anchor="s")
            else:
                self.coords(g, (x0 + x1) / 2, self.hit)
                self.itemconfigure(g, state="normal")
            self.tag_raise(g)

    def _unlight(self, lane: int):
        item = self._key_item.get(lane)
        if item is not None:
            self.itemconfigure(item, fill=self._key_base.get(lane, theme.PAD))
        g = self._glow_item.get(lane)
        if g is not None:
            self.itemconfigure(g, state="hidden")
        if self.mode == "guitar" and lane in self._pad_label:
            self.itemconfigure(self._pad_label[lane], state="hidden")

    def _update_frets(self, pos, lo, hi):
        data = self.data
        shown = set()
        for i in range(lo, hi):
            if data.starts[i] <= pos < data.ends[i] and data.lanes[i] not in shown:
                lane = data.lanes[i]
                shown.add(lane)
                item = self._pad_label.get(lane)
                if item is not None:
                    self.itemconfigure(item, image=self._fret_imgs[lane][min(data.frets[i], 15)], state="normal")
