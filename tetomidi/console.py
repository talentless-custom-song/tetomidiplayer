"""Key console: a short, thread-safe history of the key presses each instrument sent.

Dispatchers run on the transport worker thread, so ``add`` is called from there; the mini player reads the
history from the Tk thread with ``since``.  Every line gets a sequence number, so a reader only fetches what
it has not shown yet.
"""

from __future__ import annotations

import threading
from collections import deque

MAX_LINES = 300


class KeyConsole:
    def __init__(self, max_lines: int = MAX_LINES):
        self.max_lines = max_lines
        self._lines: deque[tuple[int, str]] = deque(maxlen=max_lines)
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, text))

    @property
    def seq(self) -> int:
        """Sequence number of the newest line (kept across ``clear``)."""
        with self._lock:
            return self._seq

    def since(self, seq: int) -> list[tuple[int, str]]:
        """Lines that were added after sequence number ``seq``, oldest first."""
        with self._lock:
            return [(n, text) for n, text in self._lines if n > seq]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()   # the sequence keeps counting, so readers never see old lines again
