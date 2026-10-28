"""Global hotkeys (work while a game has focus) built on pynput's keyboard listener.

Callbacks run on the listener thread, so they only push the action onto a queue; the Tk main
loop drains it with ``drain()`` and runs the callables on the UI thread.
"""

from __future__ import annotations

import logging
import queue
from typing import Callable

log = logging.getLogger(__name__)

_ALIASES = {"page_up": "pgup", "page_down": "pgdn", "caps_lock": "capslock", "num_lock": "numlock"}


def key_name(key) -> str | None:
    """Normalize a pynput key to the lower-case names stored in the config ('f2', 'a', 'space')."""
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    name = getattr(key, "name", None)
    if not name:
        return None
    for suffix in ("_l", "_r", "_gr"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return _ALIASES.get(name, name)


class HotkeyManager:
    def __init__(self):
        self._bindings: dict[str, Callable[[], None]] = {}
        self._capture: Callable[[str], None] | None = None
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._listener = None
        self.error = ""

    def start(self) -> bool:
        try:
            from pynput import keyboard

            self._listener = keyboard.Listener(on_press=self._on_press)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:
            self.error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            log.warning("Global hotkeys unavailable: %s", self.error)
            return False

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def set_bindings(self, bindings: dict[str, Callable[[], None]]) -> None:
        self._bindings = {k.lower(): fn for k, fn in bindings.items() if k}

    def capture_next(self, callback: Callable[[str], None]) -> None:
        """The next key press is delivered to ``callback`` instead of triggering a hotkey."""
        self._capture = callback

    def cancel_capture(self) -> None:
        self._capture = None

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    def _on_press(self, key) -> None:
        name = key_name(key)
        if not name:
            return
        capture = self._capture
        if capture is not None:
            self._capture = None
            self._queue.put(lambda: capture(name))
            return
        fn = self._bindings.get(name)
        if fn is not None:
            self._queue.put(fn)

    def trigger(self, name: str) -> None:
        """Programmatic trigger (used by tests and by the on-screen chips)."""
        fn = self._bindings.get(name.lower())
        if fn:
            self._queue.put(fn)

    def drain(self) -> None:
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                log.exception("hotkey action failed")
