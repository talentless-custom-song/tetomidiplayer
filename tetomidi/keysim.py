"""Keyboard simulation used by the macro engines (the 'macro' mechanism of nanoMIDIPlayer).

``KeySim`` is a thin, thread-safe layer over a backend.  The real backend is pynput; tests use
``RecordingBackend``; if the OS refuses keyboard injection (no X11 display, ...) a ``NullBackend``
keeps the UI usable and the reason is shown to the user.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

# Never injected, so macros cannot trigger the app's own F-key hotkeys (same list as nanoMIDIPlayer).
BLOCKED_KEYS = {f"f{i}" for i in range(1, 13)} | {"tab", "backspace", "esc"}
MODIFIERS = {"shift", "ctrl", "alt", "space"}
MODIFIER_ORDER = ("shift", "ctrl", "alt", "space")


class NullBackend:
    available = False

    def __init__(self, reason: str = ""):
        self.error = reason

    def press(self, key: str) -> None:
        pass

    def release(self, key: str) -> None:
        pass


class RecordingBackend:
    """Test double: records ('press'|'release', key) tuples with timestamps."""

    available = True
    error = ""

    def __init__(self, clock=None):
        import time
        self.clock = clock or time.monotonic
        self.log: list[tuple[float, str, str]] = []
        self._lock = threading.Lock()

    def press(self, key: str) -> None:
        with self._lock:
            self.log.append((self.clock(), "press", key))

    def release(self, key: str) -> None:
        with self._lock:
            self.log.append((self.clock(), "release", key))

    def actions(self):
        return [(a, k) for _, a, k in self.log]


class PynputBackend:
    available = True
    error = ""

    def __init__(self):
        from pynput import keyboard  # may raise on headless / Wayland-only systems

        self._kb = keyboard
        self._controller = keyboard.Controller()
        self._special = {
            "shift": keyboard.Key.shift,
            "ctrl": keyboard.Key.ctrl,
            "alt": keyboard.Key.alt,
            "space": keyboard.Key.space,
        }

    def _translate(self, key: str):
        return self._special.get(key, key)

    def press(self, key: str) -> None:
        self._controller.press(self._translate(key))

    def release(self, key: str) -> None:
        self._controller.release(self._translate(key))


def create_default_backend():
    try:
        return PynputBackend()
    except Exception as exc:  # pynput raises ImportError/Xlib errors depending on platform
        log.warning("Keyboard simulation unavailable: %s", exc)
        return NullBackend(str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__)


class KeySim:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else create_default_backend()
        self.held: set[str] = set()
        self._lock = threading.RLock()
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        """``fn(source, action, label)`` runs after every key that was really sent (``source`` may be None)."""
        self._listeners.append(fn)

    def _emit(self, source, action: str, key: str) -> None:
        if not self._listeners:
            return
        label = key
        if action == "press" and key not in MODIFIERS:   # "p+shift": modifiers held while the key goes down
            label += "".join("+" + m for m in MODIFIER_ORDER if m in self.held and m != key)
        for fn in list(self._listeners):
            try:
                fn(source, action, label)
            except Exception:   # a broken listener must never stop the keys
                log.exception("key listener failed")

    @staticmethod
    def _normalize(key: str) -> str:
        return key.strip().lower()

    def press(self, key: str, source: str | None = None) -> None:
        key = self._normalize(key)
        if not key or key in BLOCKED_KEYS:
            return
        if len(key) > 1 and key not in MODIFIERS:
            return  # only single characters and shift/ctrl/alt/space are supported
        with self._lock:
            try:
                self.backend.press(key)
                self.held.add(key)
                self._emit(source, "press", key)
            except Exception:
                log.exception("press(%r) failed", key)

    def release(self, key: str, source: str | None = None) -> None:
        key = self._normalize(key)
        if not key or key in BLOCKED_KEYS:
            return
        if len(key) > 1 and key not in MODIFIERS:
            return
        with self._lock:
            try:
                self.backend.release(key)
                self._emit(source, "release", key)
            except Exception:
                log.exception("release(%r) failed", key)
            self.held.discard(key)

    def release_all(self) -> None:
        with self._lock:
            for key in list(self.held):
                self.release(key)


class ScopedKeys:
    """View of a shared ``KeySim`` for one instrument: same interface, but its key events carry ``source``.

    Dispatchers talk to this object, so the console can show which instrument pressed each key.
    """

    def __init__(self, keys: KeySim, source: str):
        self._keys, self._source = keys, source

    def press(self, key: str) -> None:
        self._keys.press(key, self._source)

    def release(self, key: str) -> None:
        self._keys.release(key, self._source)

    def release_all(self) -> None:
        self._keys.release_all()

    def __getattr__(self, name):   # held, backend, ...
        return getattr(self._keys, name)
