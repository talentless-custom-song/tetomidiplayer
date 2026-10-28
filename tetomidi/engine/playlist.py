"""Play order for the MIDI player: list of files, repeat mode, shuffle and history (no GUI, no audio)."""

from __future__ import annotations

import random

REPEAT_MODES = ("off", "all", "one")


class Playlist:
    def __init__(self, paths=(), index: int = -1, repeat: str = "off", shuffle: bool = False, rng=None):
        self.items: list[str] = list(paths)
        self.index = index if 0 <= index < len(self.items) else -1
        self.repeat = repeat if repeat in REPEAT_MODES else "off"
        self.shuffle = bool(shuffle)
        self._rng = rng or random.Random()
        self._history: list[int] = []
        self._played: set[int] = set()

    # ------------------------------------------------------------ editing
    def add(self, paths) -> int:
        """Append files that are not in the list yet. Returns how many were added."""
        have = set(self.items)
        added = 0
        for p in paths:
            if p not in have:
                self.items.append(p)
                have.add(p)
                added += 1
        return added

    def remove(self, i: int) -> bool:
        """Remove entry ``i``. Returns True when it was the current entry."""
        if not 0 <= i < len(self.items):
            return False
        del self.items[i]
        fix = lambda j: j - 1 if j > i else j
        self._history = [fix(j) for j in self._history if j != i]
        self._played = {fix(j) for j in self._played if j != i}
        was_current = i == self.index
        if was_current:
            self.index = -1
        elif i < self.index:
            self.index -= 1
        return was_current

    def clear(self) -> None:
        self.items.clear()
        self.index = -1
        self._history.clear()
        self._played.clear()

    # ----------------------------------------------------------- queries
    def current(self) -> str | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    # -------------------------------------------------------- navigation
    def go(self, i: int, remember: bool = True) -> None:
        """Make ``i`` the current entry (remembered for Previous unless ``remember`` is False)."""
        if not 0 <= i < len(self.items):
            return
        if remember and self.index >= 0 and self.index != i:
            self._history.append(self.index)
            del self._history[:-200]
        self.index = i
        self._played.add(i)

    def next(self, auto: bool = False) -> int | None:
        """Index to play after the current one, or None when the list is finished.

        ``auto`` is True when the song ended by itself; pressing Next (``auto`` False) never ends the list."""
        n = len(self.items)
        if n == 0:
            return None
        if auto and self.repeat == "one" and self.index >= 0:
            return self.index
        if self.shuffle:
            pool = [i for i in range(n) if i not in self._played and i != self.index]
            if not pool:
                if auto and self.repeat != "all":
                    return None
                self._played = {self.index} if self.index >= 0 else set()
                pool = [i for i in range(n) if i != self.index] or [self.index if self.index >= 0 else 0]
            return self._rng.choice(pool)
        nxt = self.index + 1
        if nxt >= n:
            if auto and self.repeat != "all":
                return None
            nxt = 0
        return nxt

    def prev(self) -> int | None:
        """Index of the previous song (play history first, then list order)."""
        n = len(self.items)
        if n == 0:
            return None
        if self._history:
            return self._history.pop()   # not pushed again by go(): the caller moves back
        return (self.index - 1) % n if self.index >= 0 else n - 1
