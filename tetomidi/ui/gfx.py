"""Pillow drawing helpers.  Everything is rendered 4x oversampled and shrunk (smooth edges), and
all sizes pass through ``px`` so the UI follows the OS display scaling."""

from __future__ import annotations

import collections
import functools
import io
import math
import tkinter as tk

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

from ..paths import resource_path

_SS = 4
_scale = 1.0


def init_scale(root: tk.Misc) -> float:
    global _scale
    _PHOTOS.clear()   # Tk images belong to one interpreter: a new root starts with an empty cache
    try:
        _scale = max(1.0, min(3.0, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        _scale = 1.0
    return _scale


def px(n: float) -> int:
    return max(1, int(round(n * _scale)))


# PIL -> Tk conversion is the most expensive part of redrawing a button, so the converted image is kept for as long
# as the (lru-cached) PIL image lives.  The PIL image is stored next to its photo so its id can never be reused.
_PHOTOS: "collections.OrderedDict[int, tuple[Image.Image, ImageTk.PhotoImage]]" = collections.OrderedDict()
_PHOTOS_MAX = 400


def hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(c1: str, c2: str, t: float) -> str:
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ text
@functools.lru_cache(maxsize=64)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    name = "LiberationSerif-Bold.ttf" if bold else "LiberationSerif-Regular.ttf"
    try:
        return ImageFont.truetype(resource_path(f"assets/fonts/{name}"), size)
    except OSError:
        return ImageFont.load_default()


def text_size(text: str, size: int, bold: bool = False) -> tuple[int, int]:
    f = _font(size, bold)
    try:
        asc, desc = f.getmetrics()
        return math.ceil(f.getlength(text)) + 2, asc + desc + 2
    except AttributeError:
        return (len(text) * size // 2, size + 4)


@functools.lru_cache(maxsize=512)
def text_image(text: str, size: int, color: str, bold: bool = False) -> Image.Image:
    w, h = text_size(text, size, bold)
    img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((1, 1), text, font=_font(size, bold), fill=hex_to_rgb(color) + (255,))
    return img


@functools.lru_cache(maxsize=64)
def caption_image(text: str, size: int, color: str) -> Image.Image:
    """Bold text with a soft dark shadow, readable on top of notes or a background picture."""
    w, h = text_size(text, size, True)
    img = Image.new("RGBA", (w + 3, h + 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(size, True)
    d.text((2, 2), text, font=f, fill=(0, 0, 0, 200))
    d.text((1, 1), text, font=f, fill=hex_to_rgb(color) + (255,))
    return img


# ----------------------------------------------------------------- shapes
@functools.lru_cache(maxsize=256)
def pill_image(w: int, h: int, r: int, fill: str, border: str = "") -> Image.Image:
    big = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    box = (0, 0, w * _SS - 1, h * _SS - 1)
    d.rounded_rectangle(box, radius=min(r, h // 2) * _SS, fill=hex_to_rgb(fill) + (255,),
                        outline=hex_to_rgb(border) + (255,) if border else None, width=_SS if border else 0)
    return big.resize((w, h), Image.LANCZOS)


# ------------------------------------------------------- liquid glass
def _vgrad(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    """Vertical RGBA gradient (top colour -> bottom colour)."""
    g = Image.linear_gradient("L").resize((w, h), Image.BILINEAR)   # 0 at the top, 255 at the bottom
    return Image.composite(Image.new("RGBA", (w, h), bottom), Image.new("RGBA", (w, h), top), g)


def _rrect_mask(w: int, h: int, box: tuple, radius: float) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=max(radius, 0), fill=255)
    return m


def _scaled(mask: Image.Image, k: float) -> Image.Image:
    return mask.point(lambda v: int(v * k)) if k < 1.0 else mask


def _glass(w: int, h: int, r: int, tint: str, gloss: float, shape: str = "rect") -> Image.Image:
    """Frosted-glass slab: tinted gradient body, bright rim light, soft specular band on the upper half."""
    W, H = w * _SS, h * _SS
    R = min(r, h // 2) * _SS
    if shape == "ellipse":
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, W - 1, H - 1), fill=255)
    else:
        mask = _rrect_mask(W, H, (0, 0, W - 1, H - 1), R)
    top = hex_to_rgb(mix(tint, "#ffffff", 0.10 + 0.16 * gloss)) + (255,)
    bot = hex_to_rgb(mix(tint, "#000000", 0.20)) + (255,)
    img = _vgrad(W, H, top, bot)

    inset = max(_SS * 2, int(min(w, h) * 0.07 * _SS))
    hl_h = int(H * 0.48)
    hl_shape = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(hl_shape)
    box = (inset * 1.3, inset * 0.8, W - 1 - inset * 1.3, hl_h)
    if shape == "ellipse":
        d.ellipse(box, fill=255)
    else:
        d.rounded_rectangle(box, radius=max(R - inset, 2), fill=255)
    fade = Image.linear_gradient("L").resize((W, hl_h), Image.BILINEAR).point(lambda v: int(120 * gloss * (1 - v / 255) ** 1.4))
    hl_alpha = Image.new("L", (W, H), 0)
    hl_alpha.paste(fade, (0, 0))
    hl_alpha = ImageChops.multiply(hl_alpha, hl_shape)
    spec = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    spec.putalpha(hl_alpha)
    img = Image.alpha_composite(img, spec)

    k = _SS * 1.2   # 1 px rim: bright on top, a thin reflected light along the bottom
    inner = Image.new("L", (W, H), 0)
    ib = (k, k, W - 1 - k, H - 1 - k)
    ImageDraw.Draw(inner).ellipse(ib, fill=255) if shape == "ellipse" else ImageDraw.Draw(inner).rounded_rectangle(ib, radius=max(R - k, 0), fill=255)
    ring = ImageChops.subtract(mask, inner)
    rim_a = Image.linear_gradient("L").resize((W, H), Image.BILINEAR).point(
        lambda v: int(gloss * (30 + 170 * (1 - v / 255) ** 3 + 70 * (v / 255) ** 5)))
    rim = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    rim.putalpha(ImageChops.multiply(rim_a, ring))
    img = Image.alpha_composite(img, rim)

    alpha = ImageChops.multiply(img.getchannel("A"), mask)
    img.putalpha(alpha)
    return img.resize((w, h), Image.LANCZOS)


@functools.lru_cache(maxsize=384)
def glass_pill_image(w: int, h: int, r: int, tint: str, gloss: float = 1.0) -> Image.Image:
    return _glass(w, h, r, tint, gloss)


@functools.lru_cache(maxsize=96)
def glass_toggle_image(w: int, h: int, q: int, steps: int = 14) -> Image.Image:
    """Liquid-glass switch.  ``q / steps`` is the knob position (0 = off, 1 = on); the knob stretches
    while it travels like a drop of liquid."""
    t = q / steps
    track = _glass(w, h, h // 2, mix("#0d0c0e", "#2f6fa6", t), 0.55 + 0.25 * t)
    pad = max(3, h // 9)
    d = h - 2 * pad
    kw = int(d * (1 + 0.34 * math.sin(math.pi * t)))
    x0 = pad + round((w - 2 * pad - kw) * t)
    knob = _glass(kw, d, d // 2, mix("#8f8e96", "#f4f8ff", t), 1.0)
    out = track.copy()
    shadow = Image.new("RGBA", (kw, d), (0, 0, 0, 0))
    shadow.putalpha(_glass(kw, d, d // 2, "#000000", 0).getchannel("A").point(lambda v: int(v * 0.35)))
    out.alpha_composite(shadow, (x0, pad + max(1, h // 22)))
    out.alpha_composite(knob, (x0, pad))
    return out


@functools.lru_cache(maxsize=64)
def hex_image(w: int, h: int, fill: str) -> Image.Image:
    """Elongated hexagon with points on the left and right (tempo / time badges)."""
    big = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    inset = h * 0.5 * _SS
    W, H = w * _SS - 1, h * _SS - 1
    pts = [(0, H / 2), (inset, 0), (W - inset, 0), (W, H / 2), (W - inset, H), (inset, H)]
    ImageDraw.Draw(big).polygon(pts, fill=hex_to_rgb(fill) + (255,))
    return big.resize((w, h), Image.LANCZOS)


@functools.lru_cache(maxsize=16)
def toggle_image(w: int, h: int, on: bool) -> Image.Image:
    big = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, w * _SS - 1, h * _SS - 1), radius=h * _SS // 2,
                        fill=(0, 0, 0, 255), outline=(52, 50, 51, 255), width=_SS)
    pad = max(4, h // 8) * _SS
    d_k = h * _SS - 2 * pad
    x0 = w * _SS - pad - d_k if on else pad
    d.ellipse((x0, pad, x0 + d_k, pad + d_k), fill=(255, 255, 255, 255) if on else (112, 110, 111, 255))
    return big.resize((w, h), Image.LANCZOS)


@functools.lru_cache(maxsize=16)
def thumb_image(d: int) -> Image.Image:
    big = Image.new("RGBA", (d * _SS, d * _SS), (0, 0, 0, 0))
    dr = ImageDraw.Draw(big)
    dr.ellipse((0, 0, d * _SS - 1, d * _SS - 1), fill=(255, 255, 255, 255))
    ring = max(2, d // 7) * _SS
    dr.ellipse((ring, ring, d * _SS - 1 - ring, d * _SS - 1 - ring), fill=(0, 0, 0, 255))
    return big.resize((d, d), Image.LANCZOS)


@functools.lru_cache(maxsize=64)
def glow_image(w: int, h: int, color: str) -> Image.Image:
    """Soft light that rises from a lit key."""
    mask = Image.radial_gradient("L").resize((w, h * 2), Image.BICUBIC)  # 0 centre -> 255 edge
    alpha = mask.point(lambda v: max(0, int((255 - v) * 0.55) - 20 if v < 255 else 0))
    alpha = alpha.crop((0, 0, w, h))  # upper half only
    img = Image.new("RGBA", (w, h), hex_to_rgb(color) + (0,))
    img.putalpha(alpha)
    return img


@functools.lru_cache(maxsize=64)
def icon_image(name: str, size: int, alpha: float = 1.0) -> Image.Image:
    img = Image.open(resource_path(f"assets/icons/{name}.png")).convert("RGBA").resize((size, size), Image.LANCZOS)
    if alpha < 1.0:
        img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
    return img


# ------------------------------------------------------------ vector glyphs
@functools.lru_cache(maxsize=96)
def control_icon(kind: str, size: int, color: str) -> Image.Image:
    """Transport and folder glyphs drawn as shapes (so they stay sharp at any display scale)."""
    W = size * _SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = hex_to_rgb(color) + (255,)

    def at(*pts):
        return [(x * W, y * W) for x, y in pts]

    if kind == "prev":        # |<
        d.rounded_rectangle(at((0.22, 0.28), (0.31, 0.72)), radius=0.03 * W, fill=c)
        d.polygon(at((0.76, 0.27), (0.76, 0.73), (0.35, 0.5)), fill=c)
    elif kind == "next":      # >|
        d.rounded_rectangle(at((0.69, 0.28), (0.78, 0.72)), radius=0.03 * W, fill=c)
        d.polygon(at((0.24, 0.27), (0.24, 0.73), (0.65, 0.5)), fill=c)
    elif kind == "stop":
        d.rounded_rectangle(at((0.31, 0.31), (0.69, 0.69)), radius=0.05 * W, fill=c)
    elif kind == "pause":
        d.rounded_rectangle(at((0.33, 0.26), (0.45, 0.74)), radius=0.03 * W, fill=c)
        d.rounded_rectangle(at((0.55, 0.26), (0.67, 0.74)), radius=0.03 * W, fill=c)
    elif kind == "play":
        d.polygon(at((0.36, 0.24), (0.36, 0.76), (0.78, 0.5)), fill=c)
    elif kind == "folder":    # outline folder with a tab, like the Open button in the reference
        stroke = max(2, round(0.085 * W))
        outline = at((0.13, 0.80), (0.13, 0.25), (0.42, 0.25), (0.50, 0.36), (0.87, 0.36), (0.87, 0.80))
        d.line(outline + [outline[0]], fill=c, width=stroke, joint="curve")
    elif kind == "search":    # magnifier
        stroke = max(2, round(0.10 * W))
        d.ellipse(at((0.17, 0.17), (0.61, 0.61)), outline=c, width=stroke)
        d.line(at((0.57, 0.57), (0.82, 0.82)), fill=c, width=stroke)
        d.ellipse(at((0.78, 0.78), (0.86, 0.86)), fill=c)
    elif kind == "download":  # arrow into a tray
        stroke = max(2, round(0.09 * W))
        d.line(at((0.5, 0.16), (0.5, 0.56)), fill=c, width=stroke)
        d.polygon(at((0.29, 0.46), (0.71, 0.46), (0.5, 0.70)), fill=c)
        d.line(at((0.20, 0.82), (0.80, 0.82)), fill=c, width=stroke)
    elif kind == "back":      # <
        stroke = max(2, round(0.10 * W))
        d.line(at((0.60, 0.24), (0.36, 0.50), (0.60, 0.76)), fill=c, width=stroke, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


@functools.lru_cache(maxsize=128)
def round_button_image(d: int, fill: str, kind: str, glyph_color: str, gloss: float = 0.6) -> Image.Image:
    """Round glass button with a vector glyph in the middle (transport controls)."""
    disc = _glass(d, d, d // 2, fill, gloss, shape="ellipse")
    glyph = control_icon(kind, round(d * 0.5), glyph_color)
    off = (d - glyph.width) // 2
    disc.alpha_composite(glyph, (off, off))
    return disc


def background_image(path: str, w: int, h: int, dim: float) -> Image.Image:
    """Picture that covers a w x h area (scaled, centre-cropped), darkened by ``dim`` percent (0-90).

    Animated GIFs use their first frame.  Raises OSError / ValueError when the file cannot be read.
    """
    with Image.open(path) as src:
        src.seek(0)
        rgb = src.convert("RGB")
    sw, sh = rgb.size
    scale = max(w / sw, h / sh)
    rgb = rgb.resize((max(w, round(sw * scale)), max(h, round(sh * scale))), Image.LANCZOS)
    left, top = (rgb.width - w) // 2, (rgb.height - h) // 2
    img = rgb.crop((left, top, left + w, top + h)).convert("RGBA")
    shade = round(255 * min(max(float(dim), 0.0), 90.0) / 100)
    img.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, shade)))
    return img


def cover_image(data: bytes, w: int, h: int, r: int) -> Image.Image:
    """Picture bytes scaled to cover a w x h box (centre-cropped) with rounded corners.

    Raises OSError / ValueError when the bytes are not an image.
    """
    with Image.open(io.BytesIO(data)) as src:
        src.seek(0)
        rgb = src.convert("RGB")
    sw, sh = rgb.size
    scale = max(w / sw, h / sh)
    rgb = rgb.resize((max(w, round(sw * scale)), max(h, round(sh * scale))), Image.LANCZOS)
    left, top = (rgb.width - w) // 2, (rgb.height - h) // 2
    img = rgb.crop((left, top, left + w, top + h)).convert("RGBA")
    mask = Image.new("L", (w * _SS, h * _SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * _SS - 1, h * _SS - 1), radius=min(r, h // 2) * _SS, fill=255)
    img.putalpha(mask.resize((w, h), Image.LANCZOS))
    return img


def placeholder_image(w: int, h: int, r: int, fill: str = "#2a2829", glyph: str = "#6f6d6e") -> Image.Image:
    """Rounded dark box with a play glyph: stands in for a thumbnail that is loading or missing."""
    img = pill_image(w, h, r, fill).copy()
    g = control_icon("play", max(min(w, h) // 2, 8), glyph)
    img.alpha_composite(g, ((w - g.width) // 2, (h - g.height) // 2))
    return img


def photo(img: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(img)


def photo_cached(img: Image.Image) -> ImageTk.PhotoImage:
    """Like ``photo`` but reuses the Tk image of a PIL image that was already converted (widgets keep their own
    reference while the image is shown, so evicting an entry never blanks a visible image)."""
    key = id(img)
    hit = _PHOTOS.get(key)
    if hit is not None and hit[0] is img:
        _PHOTOS.move_to_end(key)
        return hit[1]
    ph = ImageTk.PhotoImage(img)
    _PHOTOS[key] = (img, ph)
    if len(_PHOTOS) > _PHOTOS_MAX:
        _PHOTOS.popitem(last=False)
    return ph
