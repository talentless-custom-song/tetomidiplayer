"""Fake keyboard sound: a real typing "clack" for every key the macro engine presses.

The sound is sent to an audio output device of your choice.  Pick a virtual cable (VB-Audio "CABLE Input" on
Windows, a null sink / PipeWire loopback on Linux) and use its other end ("CABLE Output") as the microphone in
Roblox: the voice chat then hears typing while the app plays.  Optionally the same sound is also played on the
default speakers so you can hear it yourself.

``FakeKeySound.on_key`` is registered as a ``KeySim`` listener; everything else (device handling, mixing) is
kept away from the key-sending path: ``on_key`` only appends a small "voice" to a list.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Any

import numpy as np

from .keysim import MODIFIERS
from .paths import resource_path

log = logging.getLogger(__name__)

CLICKS_FILE = "assets/sounds/keyboard_clicks.npz"
MAX_VOICES = 24            # chords of many notes must not pile up forever
MIN_GAIN, MAX_GAIN = 0.7, 1.0   # every click gets a little random loudness, like a real typist
DEFAULT_DEVICE_LABEL = "Default output"


def load_clicks(path: str | None = None) -> tuple[int, list[np.ndarray]]:
    """Return (sample rate, list of mono float32 clips in -1..1)."""
    with np.load(path or resource_path(CLICKS_FILE)) as data:
        rate = int(data["rate"])
        clips = [data[k].astype(np.float32) / 32768.0 for k in sorted(data.files) if k != "rate"]
    return rate, clips


def resample(clip: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or len(clip) == 0:
        return clip
    n = max(1, int(round(len(clip) * dst_rate / src_rate)))
    x_new = np.linspace(0, len(clip) - 1, n)
    return np.interp(x_new, np.arange(len(clip)), clip).astype(np.float32)


def is_typed_key(label: str) -> bool:
    """True for a key event label (see ``KeySim._emit``) that stands for a real key stroke.

    Modifier presses and the sustain SPACE are silent; the ALT+key pair used for velocity is one stroke, not two.
    """
    if not label or label in MODIFIERS:
        return False
    return "+alt" not in label


class Mixer:
    """Mixes click voices into one output stream (the audio callback runs ``render``)."""

    def __init__(self, clips: list[np.ndarray], channels: int = 2):
        self.clips = clips
        self.channels = max(1, channels)
        self._voices: list[list] = []          # [clip, position, gain]
        self._lock = threading.Lock()

    def trigger(self, gain: float = 1.0, clip: int | None = None) -> None:
        if not self.clips:
            return
        data = self.clips[random.randrange(len(self.clips)) if clip is None else clip % len(self.clips)]
        with self._lock:
            self._voices.append([data, 0, gain])
            if len(self._voices) > MAX_VOICES:
                del self._voices[0]

    def active(self) -> int:
        with self._lock:
            return len(self._voices)

    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, self.channels), dtype=np.float32)
        with self._lock:
            alive = []
            for voice in self._voices:
                data, pos, gain = voice
                n = min(frames, len(data) - pos)
                if n > 0:
                    out[:n] += (data[pos:pos + n] * gain)[:, None]
                    voice[1] = pos + n
                if voice[1] < len(data):
                    alive.append(voice)
            self._voices = alive
        np.clip(out, -1.0, 1.0, out=out)
        return out


class _Output:
    """One open sounddevice output stream feeding a ``Mixer``."""

    def __init__(self, sd: Any, device: int | None, base_rate: int, base_clips: list[np.ndarray]):
        info = sd.query_devices(device, "output")
        rate = int(info.get("default_samplerate") or base_rate)
        channels = max(1, min(2, int(info.get("max_output_channels") or 2)))
        self.name = str(info.get("name", "output"))
        clips = [resample(c, base_rate, rate) for c in base_clips]
        self.mixer = Mixer(clips, channels)
        self.stream = sd.OutputStream(device=device, samplerate=rate, channels=channels, dtype="float32",
                                      latency="low", callback=self._callback)
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata[:] = self.mixer.render(frames)

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            log.exception("closing audio stream")


def list_output_devices(sd: Any = None) -> list[tuple[int, str]]:
    """[(device index, name)] of the output devices of the default host API (no duplicates)."""
    try:
        sd = sd or _import_sounddevice()
        host = sd.default.hostapi
        seen, result = set(), []
        for index, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) <= 0 or dev.get("hostapi", host) != host:
                continue
            name = str(dev["name"])
            if name not in seen:
                seen.add(name)
                result.append((index, name))
        return result
    except Exception as exc:
        log.warning("Cannot list audio devices: %s", exc)
        return []


def _import_sounddevice():
    import sounddevice   # imports PortAudio; raises OSError when the library is missing
    return sounddevice


class FakeKeySound:
    """Owns the output streams and turns key events into clicks.  Settings live in ``fakeSound.*``."""

    def __init__(self, cfg, sd: Any = None, clicks: tuple[int, list[np.ndarray]] | None = None):
        self.cfg = cfg
        self._sd = sd
        self._clicks = clicks
        self._outputs: list[_Output] = []
        self._lock = threading.RLock()
        self.error = ""

    # -------------------------------------------------------------- settings
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("fakeSound.enabled", False))

    def _gain(self) -> float:
        return max(0.0, min(1.0, float(self.cfg.get("fakeSound.volume", 80)) / 100.0))

    def _device_index(self, sd: Any, name: str) -> int | None:
        if not name:
            return None
        for index, dev_name in list_output_devices(sd):
            if dev_name == name:
                return index
        raise LookupError(f"Audio device not found: {name}")

    # ---------------------------------------------------------------- streams
    def apply(self, force: bool = False) -> None:
        """Open or close the audio streams so they match the current settings (``force``: open even if off)."""
        with self._lock:
            self._close_outputs()
            self.error = ""
            if not self.enabled and not force:
                return
            try:
                sd = self._sd or _import_sounddevice()
                self._sd = sd
                if self._clicks is None:
                    self._clicks = load_clicks()
                rate, clips = self._clicks
                main = self._device_index(sd, str(self.cfg.get("fakeSound.device", "") or ""))
                targets = [main]
                if self.cfg.get("fakeSound.alsoSpeakers", False) and main is not None:
                    targets.append(None)   # None = the system default output
                for device in targets:
                    self._outputs.append(_Output(sd, device, rate, clips))
            except Exception as exc:   # missing PortAudio, device unplugged, ...
                log.warning("Fake keyboard sound unavailable: %s", exc)
                self._close_outputs()
                self.error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__

    def _close_outputs(self) -> None:
        for out in self._outputs:
            out.close()
        self._outputs = []

    def close(self) -> None:
        with self._lock:
            self._close_outputs()

    # ----------------------------------------------------------------- events
    def _trigger(self, loudness: float = 1.0) -> None:
        gain = self._gain() * loudness * random.uniform(MIN_GAIN, MAX_GAIN)
        clip = random.randrange(1 << 16)   # same clip on every output so speakers and mic stay in sync
        for out in list(self._outputs):
            out.mixer.trigger(gain, clip)

    def on_key(self, source, action: str, label: str) -> None:
        """``KeySim`` listener: one click per key press while the macro is typing."""
        if action != "press" or not self._outputs or not is_typed_key(label):
            return
        self._trigger()

    def test(self, count: int = 8, interval: float = 0.11) -> None:
        """Type a short burst so the user can check the routing (runs in a background thread)."""
        with self._lock:
            if not self._outputs:
                self.apply(force=True)
            if not self._outputs:
                return

        def run():
            import time
            for _ in range(count):
                self._trigger()
                time.sleep(interval * random.uniform(0.7, 1.4))
            if not self.enabled:   # the test opened the streams only for itself
                time.sleep(0.4)
                self.close()

        threading.Thread(target=run, daemon=True).start()
