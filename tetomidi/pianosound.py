"""Piano sound for the MIDI Hub preview: the song's own notes are played as a piano, not as keyboard clicks.

By default the notes come from the bundled SoundFont (``assets/sounds/Piano.sf2``, read by ``sf2.py``).  When the
SoundFont is switched off (``hub.useSoundfont``) or cannot be loaded, the notes are synthesised instead (a few
inharmonic partials with a fast and a slow decay, two slightly detuned strings, a soft hammer thump), so the
preview always has a sound - only numpy and sounddevice are needed, like the fake keyboard sound.  Every note is
rendered once per sample rate and cached; the pedal (CC 64) and the key release (a short damper fade) are honoured.

``PianoSound`` owns the audio stream; ``PianoPreviewDispatcher`` plugs into ``Transport`` in place of the macro
dispatcher, so the preview presses no keys at all.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from .engine.dispatchers import PianoDispatcher
from .fakesound import _import_sounddevice
from .keysim import KeySim, NullBackend
from .paths import resource_path
from .sf2 import SoundFont, SoundFontError

log = logging.getLogger(__name__)

NOTE_LOW, NOTE_HIGH = 21, 108      # A0 .. C8, the 88 keys
WARM_LOW, WARM_HIGH = 36, 96       # rendered in the background right after the stream opens (the 61-key range)
MAX_VOICES = 48                    # a long pedal with fast playing must not pile up forever
RELEASE_S = 0.35                   # damper fade after the key is released
FAST_RELEASE_S = 0.04              # a retriggered note, pause, stop or seek
DEFAULT_VOLUME = 80
SOUNDFONT_FILE = "assets/sounds/Piano.sf2"
PCM16_SCALE = 1.0 / 32768.0         # SoundFont clips are int16: the scale is folded into the voice gain


def synth_note(note: int, rate: int) -> np.ndarray:
    """One piano note as mono float32 with a peak of 1.0 (length depends on the pitch: bass rings, treble is short)."""
    f0 = 440.0 * 2.0 ** ((note - 69) / 12.0)
    tau0 = float(np.clip(4.2 * 2.0 ** (-(note - 60) / 16.0), 0.45, 6.0))   # decay time of the fundamental, seconds
    length = float(np.clip(tau0 * 5.0, 0.8, 4.5))
    n = int(length * rate)
    t = np.arange(n, dtype=np.float64) / rate
    stiffness = 8e-5 * (1.0 + max(0.0, (note - 72) / 8.0) + max(0.0, (45 - note) / 20.0))   # inharmonicity
    out = np.zeros(n, dtype=np.float64)
    nyquist = 0.45 * rate
    coarse = t[::32]    # the envelopes are smooth: compute them on a coarse grid, then interpolate
    for k in range(1, 15):
        fk = k * f0 * np.sqrt(1.0 + stiffness * k * k)
        if fk >= nyquist:
            break
        # the hammer hits 1/8 along the string, which weakens some partials
        amp = (1.0 / k ** 1.15) * (0.35 + 0.65 * abs(np.sin(np.pi * k / 8.0)))
        slow = tau0 / (1.0 + 0.55 * (k - 1))        # higher partials die sooner
        env = np.interp(t, coarse, 0.62 * np.exp(-coarse / (0.28 * slow)) + 0.38 * np.exp(-coarse / slow))   # prompt + after sound
        if k <= 8:   # two slightly detuned strings: the slow beating that makes a piano sound alive
            wave = 0.5 * (np.sin(2 * np.pi * fk * 0.99982 * t) + np.sin(2 * np.pi * fk * 1.00018 * t + 0.7))
        else:
            wave = np.sin(2 * np.pi * fk * t)
        out += amp * env * wave
    thump = int(0.02 * rate)
    if 0 < thump < n:
        noise = np.random.default_rng(note).standard_normal(thump)
        noise = np.convolve(noise, np.ones(8) / 8.0, mode="same")
        out[:thump] += 0.10 * noise * np.exp(-np.arange(thump) / (0.006 * rate))
    onset = max(1, int(0.0015 * rate))
    out[:onset] *= np.linspace(0.0, 1.0, onset)
    peak = float(np.max(np.abs(out)))
    if peak > 0:
        out /= peak
    return out.astype(np.float32)


def fold_note(note: int) -> int:
    """Bring ``note`` into the piano's range by octaves."""
    while note < NOTE_LOW:
        note += 12
    while note > NOTE_HIGH:
        note -= 12
    return note


def velocity_gain(velocity: int) -> float:
    v = max(1, min(127, int(velocity))) / 127.0
    return 0.12 + 0.30 * v ** 1.3     # per voice; the mixer soft-limits chords


class PianoMixer:
    """Mixes piano voices into one output stream (the audio callback runs ``render``)."""

    def __init__(self, channels: int = 2, rate: int = 44100):
        self.channels = max(1, channels)
        self.rate = rate
        self._voices: list[list] = []   # [data, position, gain, note, release position, release length (0 = held)]
        self._lock = threading.Lock()

    def note_on(self, data: np.ndarray, gain: float, note: int) -> None:
        fast = max(1, int(FAST_RELEASE_S * self.rate))
        with self._lock:
            for v in self._voices:      # the same key struck again damps the previous sound quickly
                if v[3] == note and not v[5]:
                    v[4], v[5] = 0, fast
            self._voices.append([data, 0, gain, note, 0, 0])
            if len(self._voices) > MAX_VOICES:
                del self._voices[0]

    def release(self, note: int, seconds: float = RELEASE_S) -> None:
        with self._lock:
            for v in self._voices:
                if v[3] == note and not v[5]:
                    v[4], v[5] = 0, max(1, int(seconds * self.rate))

    def release_all(self, seconds: float = FAST_RELEASE_S) -> None:
        with self._lock:
            for v in self._voices:
                if not v[5]:
                    v[4], v[5] = 0, max(1, int(seconds * self.rate))

    def active(self) -> int:
        with self._lock:
            return len(self._voices)

    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, self.channels), dtype=np.float32)
        with self._lock:
            alive = []
            for v in self._voices:
                data, pos, gain, _note, rel_pos, rel_len = v
                n = min(frames, len(data) - pos)
                if n <= 0:
                    continue
                seg = data[pos:pos + n] * gain
                if seg.ndim == 1:                     # mono clip: same signal on every channel
                    seg = seg[:, None]
                elif seg.shape[1] != self.channels:   # stereo clip on a mono device (or the other way round)
                    seg = seg.mean(axis=1, keepdims=True)
                ended = False
                if rel_len:
                    fade = np.clip(1.0 - (np.arange(n) + rel_pos) / rel_len, 0.0, 1.0)
                    seg = seg * (fade * fade)[:, None]
                    v[4] = rel_pos + n
                    ended = v[4] >= rel_len
                out[:n] += seg
                v[1] = pos + n
                if v[1] < len(data) and not ended:
                    alive.append(v)
            self._voices = alive
        np.tanh(out, out=out)      # soft limiter: big chords get denser, not harsh
        return out


class PianoSound:
    """Plays notes on the default output.  Volume follows the ``fakeSound.volume`` setting.

    Notes are played from the SoundFont (bundled ``Piano.sf2``, or the file in ``hub.soundfont``) unless
    ``hub.useSoundfont`` is off or the file cannot be read; then the built-in synthesised piano is used.
    """

    def __init__(self, cfg, sd: Any = None):
        self.cfg = cfg
        self._sd = sd
        self._font: SoundFont | None = None
        self._font_path = ""              # file the current ``_font`` came from ("" = not loaded)
        self.font_error = ""              # why the SoundFont is not used (empty when it is, or when it is switched off)
        self._stream = None
        self._mixer: PianoMixer | None = None
        self._rate = 44100
        self._cache: dict[int, np.ndarray] = {}
        self._pedal = False
        self._pending: set[int] = set()   # keys released while the pedal was down
        self._lock = threading.RLock()
        self._warm_gen = 0
        self.error = ""

    # ------------------------------------------------------------- stream
    @property
    def using_soundfont(self) -> bool:
        return self._font is not None

    def _select_font(self) -> None:
        """Load (or drop) the SoundFont according to the settings; the note cache is cleared when the source changes."""
        before = self._font_path if self._font is not None else ""
        self.font_error = ""
        path = ""
        if self.cfg.get("hub.useSoundfont", True):
            path = str(self.cfg.get("hub.soundfont", "") or "") or resource_path(SOUNDFONT_FILE)
            if path != self._font_path or self._font is None:
                try:
                    self._font = SoundFont.load(path)
                    self._font_path = path
                except SoundFontError as exc:
                    log.warning("SoundFont %s not used: %s", path, exc)
                    self.font_error = str(exc)
                    self._font, self._font_path, path = None, "", ""
        else:
            self._font, self._font_path = None, ""
        if (self._font_path if self._font is not None else "") != before:
            self._cache.clear()

    def apply(self) -> None:
        """(Re)open the output stream."""
        with self._lock:
            self._close_stream()
            self.error = ""
            self._select_font()
            try:
                sd = self._sd or _import_sounddevice()
                self._sd = sd
                info = sd.query_devices(None, "output")
                rate = int(info.get("default_samplerate") or 44100)
                channels = max(1, min(2, int(info.get("max_output_channels") or 2)))
                if rate != self._rate:
                    self._cache.clear()
                self._rate = rate
                self._mixer = PianoMixer(channels, rate)
                self._stream = sd.OutputStream(samplerate=rate, channels=channels, dtype="float32",
                                               latency="low", callback=self._callback)
                self._stream.start()
            except Exception as exc:   # missing PortAudio, no output device, ...
                log.warning("Piano preview sound unavailable: %s", exc)
                self._close_stream()
                self.error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
                return
            self._warm_gen += 1
            threading.Thread(target=self._warm_up, args=(self._warm_gen,), daemon=True).start()

    def _callback(self, outdata, frames, time_info, status) -> None:
        mixer = self._mixer
        outdata[:] = mixer.render(frames) if mixer else 0.0

    def _close_stream(self) -> None:
        stream, self._stream, self._mixer = self._stream, None, None
        self._pedal = False
        self._pending.clear()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("closing audio stream")

    def close(self) -> None:
        with self._lock:
            self._warm_gen += 1    # stops the warm-up thread
            self._close_stream()

    def _warm_up(self, gen: int) -> None:
        """Render the usual range in the background so the first notes of a song do not stall."""
        for note in sorted(range(WARM_LOW, WARM_HIGH + 1), key=lambda n: abs(n - 66)):   # the middle is used most
            if gen != self._warm_gen:
                return
            self._sample(note)
            time.sleep(0.002)

    def _sample(self, note: int) -> np.ndarray:
        data = self._cache.get(note)
        if data is None:
            font = self._font
            clip = font.render_note(note, self._rate) if font is not None else None
            data = self._cache[note] = clip if clip is not None else synth_note(note, self._rate)
        return data

    # ------------------------------------------------------------- events
    def _volume(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.cfg.get("fakeSound.volume", DEFAULT_VOLUME)) / 100.0))
        except (TypeError, ValueError):
            return DEFAULT_VOLUME / 100.0

    def note_on(self, note: int, velocity: int = 90) -> None:
        mixer = self._mixer
        if mixer is None:
            return
        note = fold_note(int(note))
        self._pending.discard(note)
        data = self._sample(note)
        scale = PCM16_SCALE if data.dtype == np.int16 else 1.0
        mixer.note_on(data, np.float32(velocity_gain(velocity) * self._volume() * 2.0 * scale), note)

    def note_off(self, note: int) -> None:
        mixer = self._mixer
        if mixer is None:
            return
        note = fold_note(int(note))
        if self._pedal:
            self._pending.add(note)
        else:
            mixer.release(note)

    def pedal(self, down: bool) -> None:
        mixer = self._mixer
        if mixer is None or down == self._pedal:
            return
        self._pedal = down
        if not down:
            pending, self._pending = self._pending, set()
            for note in pending:
                mixer.release(note)

    def release_all(self) -> None:
        """Pause / stop / seek: every sounding note fades out at once and the pedal is lifted."""
        self._pedal = False
        self._pending.clear()
        if self._mixer is not None:
            self._mixer.release_all()


class PianoPreviewDispatcher(PianoDispatcher):
    """``Transport`` dispatcher that plays the MIDI notes as piano instead of pressing keys.

    It keeps the piano dispatcher's config view (``c``), which ``Transport`` reads for ``releaseOnPause`` and
    ``randomFail``; pitch/key offsets of the macro player are not applied (the preview plays the song as written).
    """

    def __init__(self, sound: PianoSound, cfg):
        super().__init__(KeySim(NullBackend()), cfg)
        self.sound = sound

    def note_on(self, note, velocity, channel):
        if velocity <= 0:
            self.sound.note_off(note)
        else:
            self.sound.note_on(note, velocity)

    def note_off(self, note, channel):
        self.sound.note_off(note)

    def control(self, control, value):
        if control == 64:
            self.sound.pedal(value >= 64)

    def release_all(self):
        self.sound.release_all()
