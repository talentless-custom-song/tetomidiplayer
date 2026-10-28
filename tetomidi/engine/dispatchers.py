"""Macro dispatchers: turn MIDI note events into simulated key presses.

``PianoDispatcher`` and ``DrumDispatcher`` are ports of nanoMIDIPlayer's ``simulateKey`` /
drum ``parseMidi`` logic (same key maps, shift/ctrl layering for the 61/88-key layouts, velocity
via ALT+key, sustain via SPACE, finger limit, custom hold length, random transpose).
``GuitarDispatcher`` applies the same mechanism to an editable note->key map.
"""

from __future__ import annotations

import logging
import random
import re
import threading

from ..config import Config
from ..keysim import KeySim
from ..mappings import DRUM_NOTE_TO_NAME, guitar_resolve_note
from ..rollmodel import SHORT_NOTE

log = logging.getLogger(__name__)

_SHIFTED_SYMBOLS = re.compile(r"[!@$%^*(]")


class Dispatcher:
    """Base class: finger limit, custom hold length and bookkeeping shared by all instruments."""

    mode = ""

    def __init__(self, keys: KeySim, cfg: Config):
        self.keys = keys
        self.cfg = cfg
        self._held_count = 0
        self._timers: list[threading.Timer] = []
        self._lock = threading.RLock()

    @property
    def c(self) -> dict:
        return self.cfg.data[self.mode]

    # -- interface used by Transport ---------------------------------
    def note_on(self, note: int, velocity: int, channel: int) -> None:
        raise NotImplementedError

    def note_off(self, note: int, channel: int) -> None:
        raise NotImplementedError

    def control(self, control: int, value: int) -> None:
        pass

    def release_all(self) -> None:
        with self._lock:
            for t in self._timers:
                t.cancel()
            self._timers.clear()
            self._held_count = 0
        self.keys.release_all()

    # -- helpers ------------------------------------------------------
    def _press_and_maybe_release(self, key: str) -> None:
        with self._lock:
            limit = int(self.c.get("fingerLimit", 11))
            if limit <= 10 and self._held_count >= limit:
                return  # finger limit reached: skip the note
            self._held_count += 1
        self.keys.press(key)
        hold = self.c.get("customHoldLength", {})
        if hold.get("enabled"):
            length = float(hold.get("noteLength", 0.1))
        elif not self.c.get("noteLengths", True):
            length = SHORT_NOTE   # "Note lengths" off: every key is tapped for the same short time
        else:
            return
        def _release():
            self.keys.release(key)
            with self._lock:
                self._held_count = max(0, self._held_count - 1)

        timer = threading.Timer(length, _release)
        timer.daemon = True
        with self._lock:
            self._timers = [t for t in self._timers if t.is_alive()]
            self._timers.append(timer)
        timer.start()

    def _note_released(self) -> None:
        with self._lock:
            self._held_count = max(0, self._held_count - 1)


class PianoDispatcher(Dispatcher):
    mode = "piano"

    def __init__(self, keys: KeySim, cfg: Config):
        super().__init__(keys, cfg)
        self.sustain_active = False
        self._transposed: dict[int, list[int]] = {}

    # -- mapping -------------------------------------------------------
    def resolve(self, note: int) -> tuple[int, str] | None:
        """Note (after pitch/key offsets) and the key it maps to, or None when out of range."""
        c = self.c
        note = note + c.get("pitchOffset", 0) + c.get("transposeOffset", 0)
        pm = c["pianoMap"]
        s = str(note)
        letter = pm["61keyMap"]
        if s in letter:
            return note, letter[s]
        if c.get("88Keys", True):
            if s in pm["88keyMap"]["lowNotes"]:
                return note, pm["88keyMap"]["lowNotes"][s]
            if s in pm["88keyMap"]["highNotes"]:
                return note, pm["88keyMap"]["highNotes"][s]
        return None

    def velocity_key(self, velocity: int) -> str:
        vmap = self.c["pianoMap"]["velocityMap"]
        thresholds = sorted(int(k) for k in vmap)
        lo, hi, idx = 0, len(thresholds) - 1, 0
        while lo <= hi:  # same search as nanoMIDIPlayer (kept for identical velocity buckets)
            idx = (lo + hi) // 2
            if idx == 0 or idx == len(thresholds) - 1:
                break
            if thresholds[idx] < velocity:
                lo = idx + 1
            else:
                hi = idx - 1
        return vmap[str(thresholds[idx])]

    # -- events --------------------------------------------------------
    def note_on(self, note, velocity, channel):
        c = self.c
        rf = c.get("randomFail", {})
        if rf.get("enabled") and random.random() < rf.get("transpose", 0) / 100:
            new_note = note + random.randint(-12, 12)
            self._transposed.setdefault(note, []).append(new_note)
            note = new_note
        resolved = self.resolve(note)
        if resolved is None:
            return
        note, key = resolved
        letter = c["pianoMap"]["61keyMap"]

        if c.get("velocity"):
            vk = self.velocity_key(velocity)
            self.keys.press("alt")
            self.keys.press(vk)
            self.keys.release(vk)
            self.keys.release("alt")

        if 36 <= note <= 96:
            symbol = bool(_SHIFTED_SYMBOLS.search(key))
            base = letter.get(str(note - 1), key.lower()) if symbol else None
            if c.get("noDoubles", True):
                self.keys.release(base if symbol else key.lower())
            if symbol:
                self.keys.press("shift")
                self._press_and_maybe_release(base)
                self.keys.release("shift")
            elif key.isupper():
                self.keys.press("shift")
                self._press_and_maybe_release(key.lower())
                self.keys.release("shift")
            else:
                self._press_and_maybe_release(key)
        else:  # extended 88-key range: CTRL layer
            self.keys.release(key.lower())
            self.keys.press("ctrl")
            self._press_and_maybe_release(key.lower())
            self.keys.release("ctrl")

    def note_off(self, note, channel):
        queue = self._transposed.get(note)
        if queue:  # this note was randomly transposed when it started: release the shifted one
            mapped = queue.pop(0)
            if not queue:
                del self._transposed[note]
            note = mapped
        resolved = self.resolve(note)
        if resolved is None:
            return
        note, key = resolved
        letter = self.c["pianoMap"]["61keyMap"]
        if 36 <= note <= 96 and _SHIFTED_SYMBOLS.search(key):
            self.keys.release(letter.get(str(note - 1), key.lower()))
        else:
            self.keys.release(key.lower())
        self._note_released()

    def control(self, control, value):
        if control != 64 or not self.c.get("sustain"):
            return
        cutoff = self.c.get("sustainCutoff", 63)
        if not self.sustain_active and value > cutoff:
            self.sustain_active = True
            self.keys.press("space")
        elif self.sustain_active and value < cutoff:
            self.sustain_active = False
            self.keys.release("space")

    def release_all(self):
        self.sustain_active = False
        self._transposed.clear()
        super().release_all()


class DrumDispatcher(Dispatcher):
    mode = "drums"

    def key_for(self, note: int) -> str | None:
        name = DRUM_NOTE_TO_NAME.get(note)
        return (self.c["drumsMap"].get(name) or None) if name else None

    def note_on(self, note, velocity, channel):
        key = self.key_for(note)
        if key:
            self._press_and_maybe_release(key)

    def note_off(self, note, channel):
        key = self.key_for(note)
        if key:
            self.keys.release(key)
            self._note_released()


class GuitarDispatcher(Dispatcher):
    mode = "guitar"

    def resolve(self, note: int) -> str | None:
        keymap = self.c["keymap"]
        resolved = guitar_resolve_note(note, keymap, self.c.get("foldOctaves", True))
        return keymap.get(str(resolved)) if resolved is not None else None

    def note_on(self, note, velocity, channel):
        key = self.resolve(note)
        if not key:
            return
        lower = key.lower()
        self.keys.release(lower)  # retrigger cleanly when the same string/fret is struck again
        if key.isupper():
            self.keys.press("shift")
            self._press_and_maybe_release(lower)
            self.keys.release("shift")
        else:
            self._press_and_maybe_release(key)

    def note_off(self, note, channel):
        key = self.resolve(note)
        if key:
            self.keys.release(key.lower())
            self._note_released()


def create_dispatcher(mode: str, keys: KeySim, cfg: Config) -> Dispatcher:
    return {"piano": PianoDispatcher, "drums": DrumDispatcher, "guitar": GuitarDispatcher}[mode](keys, cfg)
