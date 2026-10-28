"""JSON configuration with defaults, validation, change notification and debounced saving."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

from .defaults import fresh_defaults
from .paths import config_dir

log = logging.getLogger(__name__)

_SAVE_DELAY = 0.4


def _same_kind(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def merge_defaults(defaults: dict, user: dict) -> dict:
    """Return ``defaults`` overlaid with ``user``; wrongly-typed user values fall back to the default."""
    merged = copy.deepcopy(defaults)
    for key, value in user.items():
        if key in defaults:
            if isinstance(defaults[key], dict) and isinstance(value, dict):
                merged[key] = merge_defaults(defaults[key], value)
            elif _same_kind(defaults[key], value):
                merged[key] = value
        else:
            merged[key] = value
    return merged


class Config:
    def __init__(self, path: Path | str | None = None, autosave: bool = True):
        self.path = Path(path) if path else config_dir() / "config.json"
        self.autosave = autosave
        self.data: dict = fresh_defaults()
        self._listeners: list[Callable[[str], None]] = []
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root must be an object")
            self.data = merge_defaults(fresh_defaults(), raw)
        except FileNotFoundError:
            self.data = fresh_defaults()
        except (OSError, ValueError) as exc:
            log.warning("Config unreadable (%s); using defaults", exc)
            try:
                self.path.replace(self.path.with_suffix(".json.bad"))
            except OSError:
                pass
            self.data = fresh_defaults()

    def save(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                log.error("Could not save config: %s", exc)

    def _save_later(self) -> None:
        if not self.autosave:
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(_SAVE_DELAY, self.save)
            self._timer.daemon = True
            self._timer.start()

    # -------------------------------------------------------------- access
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = node[part] = {}
            node = nxt
        if node.get(parts[-1]) == value and type(node.get(parts[-1])) is type(value):
            return
        node[parts[-1]] = value
        self._save_later()
        for fn in list(self._listeners):
            try:
                fn(path)
            except Exception:  # a broken listener must never break the setter
                log.exception("config listener failed")

    def reset(self) -> None:
        self.data = fresh_defaults()
        self.save()
        for fn in list(self._listeners):
            fn("*")

    def on_change(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)
