"""Minimal SoundFont 2 (``.sf2``) reader + note renderer, numpy only (no FluidSynth / native library needed).

It supports what a sampled piano needs: one preset -> instruments -> zones with key / velocity ranges, 16-bit
samples with loop points, tuning (root key, coarse / fine tune, scale tuning, sample pitch correction), pan,
initial attenuation and the volume envelope (delay, attack, hold, decay, sustain).  Filters, LFOs, modulators,
the modulation envelope and the 24-bit extension are ignored.

``SoundFont.render_note`` plays one key into a finite stereo clip (looped samples are unrolled for a few seconds
and faded out), so the caller can cache the clip and mix it like any other sample.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 256 * 1024 * 1024
LOOP_SECONDS = 4.0          # length of a note whose sample loops (the sound holds, then fades out)
TAIL_FADE_S = 0.30          # fade-out at the end of a clip that was cut short
EDGE_FADE_S = 0.008         # fade-out at the end of a sample that simply ends
MIN_LOOP = 8                # shortest loop the SF2 spec allows, in sample frames
PEAK = 0.97                 # clips are normalised to this fraction of full scale

# generator numbers (SF2 spec 8.1.2)
G_START, G_END, G_LOOP_START, G_LOOP_END = 0, 1, 2, 3
G_START_COARSE, G_END_COARSE = 4, 12
G_PAN = 17
G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_SUSTAIN, G_RELEASE = 33, 34, 35, 36, 37, 38
G_INSTRUMENT, G_KEY_RANGE, G_VEL_RANGE = 41, 43, 44
G_LOOP_START_COARSE, G_ATTENUATION, G_LOOP_END_COARSE = 45, 48, 50
G_COARSE_TUNE, G_FINE_TUNE, G_SAMPLE_ID, G_SAMPLE_MODES = 51, 52, 53, 54
G_SCALE_TUNING, G_ROOT_KEY = 56, 58

_RANGES = (G_KEY_RANGE, G_VEL_RANGE)
_UNSIGNED = (G_INSTRUMENT, G_SAMPLE_ID)


class SoundFontError(Exception):
    """The file is not a usable SoundFont (message is safe to log / show)."""


@dataclass(frozen=True)
class Sample:
    name: str
    start: int
    end: int
    loop_start: int
    loop_end: int
    rate: int
    pitch: int
    correction: int
    kind: int


@dataclass(frozen=True)
class Zone:
    sample: Sample
    key_lo: int
    key_hi: int
    vel_lo: int
    vel_hi: int
    gens: dict          # instrument generators merged with the instrument's global zone (+ preset tuning)


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def _chunks(buf: bytes, start: int, end: int):
    """(id, data offset, size) of the RIFF sub-chunks in ``buf[start:end]``."""
    pos = start
    while pos + 8 <= end:
        cid = buf[pos:pos + 4]
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        if pos + 8 + size > end:
            raise SoundFontError("truncated chunk")
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _records(buf: bytes, offset: int, size: int, fmt: str) -> list[tuple]:
    st = struct.Struct(fmt)
    count = size // st.size
    return [st.unpack_from(buf, offset + i * st.size) for i in range(count)]


def _gens(buf: bytes, offset: int, size: int) -> list[tuple[int, int, int]]:
    """[(operator, unsigned amount, signed amount)]"""
    return [(op, lo | (hi << 8), struct.unpack("<h", struct.pack("<H", lo | (hi << 8)))[0])
            for op, lo, hi in _records(buf, offset, size, "<HBB")]


def _bag_gens(bags: list[tuple], gens: list[tuple[int, int, int]], first: int, last: int) -> list[dict]:
    """One generator dict per zone of bags ``first..last-1``; ranges are (lo, hi), the rest signed ints."""
    zones = []
    for b in range(first, last):
        g0, g1 = bags[b][0], bags[b + 1][0]
        zone = {}
        for op, unsigned, signed in gens[g0:g1]:
            if op in _RANGES:
                zone[op] = (unsigned & 0xFF, unsigned >> 8)
            elif op in _UNSIGNED:
                zone[op] = unsigned
            else:
                zone[op] = signed
        zones.append(zone)
    return zones


def _timecents(value: int, default: float) -> float:
    return float(2.0 ** (value / 1200.0)) if value is not None else default


class SoundFont:
    """A parsed ``.sf2`` with one preset ready to play."""

    def __init__(self, pcm: np.ndarray, zones: list[Zone], name: str = ""):
        self.pcm = pcm              # int16, every sample of the file
        self.zones = zones
        self.name = name

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path, bank: int = 0, preset: int = 0, skip_samples: tuple[str, ...] = ("damper",)) -> "SoundFont":
        """Read ``path``.  Samples whose name contains a word of ``skip_samples`` are left out (a piano font
        often carries a "damper" thump that belongs to the key release, which the mixer fades on its own)."""
        path = Path(path)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise SoundFontError("file is too large")
            buf = path.read_bytes()
        except OSError as exc:
            raise SoundFontError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
        try:
            return cls._parse(buf, bank, preset, tuple(s.lower() for s in skip_samples))
        except SoundFontError:
            raise
        except (struct.error, IndexError, ValueError) as exc:
            raise SoundFontError(f"damaged SoundFont ({exc})") from exc

    @classmethod
    def _parse(cls, buf: bytes, bank: int, preset: int, skip: tuple[str, ...]) -> "SoundFont":
        if len(buf) < 12 or buf[:4] != b"RIFF" or buf[8:12] != b"sfbk":
            raise SoundFontError("not a SoundFont (.sf2) file")
        pcm = None
        sections: dict[bytes, tuple[int, int]] = {}
        title = ""
        for cid, off, size in _chunks(buf, 12, min(len(buf), 8 + struct.unpack_from("<I", buf, 4)[0])):
            if cid != b"LIST":
                continue
            kind = buf[off:off + 4]
            for sid, soff, ssize in _chunks(buf, off + 4, off + size):
                if kind == b"sdta" and sid == b"smpl":
                    pcm = np.frombuffer(buf, dtype="<i2", count=ssize // 2, offset=soff).astype(np.int16)
                elif kind == b"pdta":
                    sections[sid] = (soff, ssize)
                elif kind == b"INFO" and sid == b"INAM":
                    title = _name(buf[soff:soff + ssize])
        missing = [k.decode() for k in (b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr")
                   if k not in sections]
        if pcm is None or missing:
            raise SoundFontError("SoundFont has no sample data" if pcm is None else f"missing {missing[0]} chunk")

        def table(key: bytes, fmt: str) -> list[tuple]:
            return _records(buf, *sections[key], fmt)

        phdr = table(b"phdr", "<20sHHHIII")
        pbag, ibag = table(b"pbag", "<HH"), table(b"ibag", "<HH")
        pgen, igen = _gens(buf, *sections[b"pgen"]), _gens(buf, *sections[b"igen"])
        inst = table(b"inst", "<20sH")
        shdr = table(b"shdr", "<20sIIIIIBbHH")
        samples = [Sample(_name(n), s, e, ls, le, r, p, c, t) for n, s, e, ls, le, r, p, c, _l, t in shdr]

        index = next((i for i, h in enumerate(phdr[:-1]) if h[1] == preset and h[2] == bank), None)
        if index is None:
            index = 0 if len(phdr) > 1 else None
        if index is None:
            raise SoundFontError("SoundFont has no presets")
        preset_name = _name(phdr[index][0])

        zones: list[Zone] = []
        for pz in _split_global(_bag_gens(pbag, pgen, phdr[index][3], phdr[index + 1][3]), G_INSTRUMENT):
            inst_id = pz.get(G_INSTRUMENT)
            if inst_id is None or inst_id >= len(inst) - 1:
                continue
            p_klo, p_khi = pz.get(G_KEY_RANGE, (0, 127))
            p_vlo, p_vhi = pz.get(G_VEL_RANGE, (0, 127))
            for iz in _split_global(_bag_gens(ibag, igen, inst[inst_id][1], inst[inst_id + 1][1]), G_SAMPLE_ID):
                sid = iz.get(G_SAMPLE_ID)
                if sid is None or sid >= len(samples) - 1:
                    continue
                sample = samples[sid]
                if any(word in sample.name.lower() for word in skip):
                    continue
                klo, khi = iz.get(G_KEY_RANGE, (0, 127))
                vlo, vhi = iz.get(G_VEL_RANGE, (0, 127))
                klo, khi, vlo, vhi = max(klo, p_klo), min(khi, p_khi), max(vlo, p_vlo), min(vhi, p_vhi)
                if klo > khi or vlo > vhi:
                    continue
                gens = dict(iz)
                for op in (G_ATTENUATION, G_COARSE_TUNE, G_FINE_TUNE, G_PAN):   # preset-level values add up
                    if op in pz:
                        gens[op] = gens.get(op, 0) + pz[op]
                zones.append(Zone(sample, klo, khi, vlo, vhi, gens))
        if not zones:
            raise SoundFontError(f"preset '{preset_name}' has no playable samples")
        return cls(pcm, zones, title or preset_name)

    # ---------------------------------------------------------------- rendering
    def covers(self, note: int) -> bool:
        return any(z.key_lo <= note <= z.key_hi for z in self.zones)

    def render_note(self, note: int, rate: int, velocity: int = 100, seconds: float = LOOP_SECONDS) -> np.ndarray | None:
        """Stereo int16 clip ``(frames, 2)`` of ``note`` at ``rate`` Hz, peak-normalised; None if no zone plays it."""
        layers = []
        for zone in self.zones:
            if zone.key_lo <= note <= zone.key_hi and zone.vel_lo <= velocity <= zone.vel_hi:
                layer = self._render_zone(zone, note, rate, seconds)
                if layer is not None:
                    layers.append(layer)
        if not layers:
            return None
        frames = max(len(mono) for mono, _pan, _cut in layers)
        out = np.zeros((frames, 2), dtype=np.float32)
        cut = False
        for mono, pan, was_cut in layers:
            angle = (min(max(pan, -500), 500) + 500) / 1000.0 * (np.pi / 2.0)
            out[:len(mono), 0] += mono * np.cos(angle)
            out[:len(mono), 1] += mono * np.sin(angle)
            cut = cut or was_cut and len(mono) == frames
        if cut:   # the note was longer than the clip: fade the tail instead of chopping it
            fade = min(frames, int(TAIL_FADE_S * rate))
            out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
        peak = float(np.max(np.abs(out)))
        if peak <= 0.0:
            return None
        return np.round(out * (PEAK * 32767.0 / peak)).astype(np.int16)

    def _render_zone(self, zone: Zone, note: int, rate: int, seconds: float):
        """(mono float32 in the sample's own loudness, pan in 0.1 %, was cut at max length) or None."""
        g, s = zone.gens, zone.sample
        start = s.start + g.get(G_START, 0) + 32768 * g.get(G_START_COARSE, 0)
        end = s.end + g.get(G_END, 0) + 32768 * g.get(G_END_COARSE, 0)
        start, end = max(0, start), min(len(self.pcm), end)
        if end - start < 4:
            return None
        loop_start = s.loop_start + g.get(G_LOOP_START, 0) + 32768 * g.get(G_LOOP_START_COARSE, 0) - start
        loop_end = s.loop_end + g.get(G_LOOP_END, 0) + 32768 * g.get(G_LOOP_END_COARSE, 0) - start
        data = self.pcm[start:end].astype(np.float32) / 32768.0
        looped = (g.get(G_SAMPLE_MODES, 0) & 3) in (1, 3) and 0 <= loop_start and loop_end <= len(data) \
            and loop_end - loop_start >= MIN_LOOP

        root = g.get(G_ROOT_KEY, -1)
        if not 0 <= root <= 127:
            root = s.pitch if 0 <= s.pitch <= 127 else 60
        cents = (note - root) * g.get(G_SCALE_TUNING, 100) + 100 * g.get(G_COARSE_TUNE, 0) + g.get(G_FINE_TUNE, 0) + s.correction
        ratio = float(2.0 ** (cents / 1200.0)) * max(s.rate, 1) / float(rate)

        cap = max(1, int(seconds * rate))
        natural = int(np.ceil(len(data) / ratio))
        frames = cap if looped else min(cap, natural)
        pos = np.arange(frames, dtype=np.float64) * ratio
        mono = _resample(data, pos, loop_start if looped else None, loop_end if looped else None)

        mono *= _volume_envelope(g, frames, rate)
        mono *= float(10.0 ** (-max(0, g.get(G_ATTENUATION, 0)) / 200.0))
        if not looped:   # the sample ends by itself: only avoid a click
            fade = min(frames, max(1, int(EDGE_FADE_S * rate)))
            mono[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        pan = g.get(G_PAN, 0)
        if s.kind & 2:      # right / left sample types always sit on their side
            pan = 500
        elif s.kind & 4:
            pan = -500
        return mono, pan, looped and frames == cap


def _split_global(zones: list[dict], terminal: int) -> list[dict]:
    """Local zones with the global zone (the first one, when it has no ``terminal`` generator) merged in."""
    if not zones:
        return []
    if terminal not in zones[0]:
        base, zones = zones[0], zones[1:]
        return [{**base, **z} for z in zones]
    return list(zones)


def _resample(data: np.ndarray, pos: np.ndarray, loop_start: int | None, loop_end: int | None) -> np.ndarray:
    """Cubic (Catmull-Rom) interpolation of ``data`` at the fractional positions ``pos`` (loop-aware)."""
    n = len(data)
    base = np.floor(pos).astype(np.int64)
    t = (pos - base).astype(np.float32)

    def tap(offset: int) -> np.ndarray:
        idx = base + offset
        if loop_start is not None:
            length = loop_end - loop_start
            idx = np.where(idx >= loop_end, loop_start + (idx - loop_start) % length, idx)
        valid = (idx >= 0) & (idx < n)
        return np.where(valid, data[np.clip(idx, 0, n - 1)], 0.0).astype(np.float32)

    p0, p1, p2, p3 = tap(-1), tap(0), tap(1), tap(2)
    a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3
    b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3
    c = -0.5 * p0 + 0.5 * p2
    return (((a * t + b) * t + c) * t + p1).astype(np.float32)


def _volume_envelope(g: dict, frames: int, rate: int) -> np.ndarray:
    """Delay / attack / hold / decay / sustain gain for ``frames`` frames (the key release is done by the mixer)."""
    def seconds(op: int) -> float:
        return float(min(2.0 ** (g.get(op, -12000) / 1200.0), 100.0))

    delay, attack, hold, decay = seconds(G_DELAY), seconds(G_ATTACK), seconds(G_HOLD), seconds(G_DECAY)
    sustain_db = min(max(g.get(G_SUSTAIN, 0), 0), 1000) / 10.0
    t = np.arange(frames, dtype=np.float64) / rate
    ramp = np.clip((t - delay) / max(attack, 1e-4), 0.0, 1.0)            # amplitude rises linearly
    after = np.clip(t - delay - attack - hold, 0.0, None)
    att_db = np.minimum(sustain_db, 100.0 * after / max(decay, 1e-3))      # decay is 100 dB per ``decay`` seconds
    return (ramp * 10.0 ** (-att_db / 20.0)).astype(np.float32)
