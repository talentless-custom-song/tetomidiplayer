"""Transport: the single playback clock shared by the macro dispatcher and the piano roll.

The worker thread dispatches events when ``position()`` reaches them; the UI reads the very same
``position()`` to draw the falling notes, so animation and key presses can never drift apart.
"""

from __future__ import annotations

import bisect
import logging
import random
import threading
import time

from ..config import Config
from ..midi_data import EV_CC, EV_OFF, EV_ON, SongView
from .dispatchers import Dispatcher

log = logging.getLogger(__name__)

STOPPED, PLAYING, PAUSED = "stopped", "playing", "paused"
MIN_SPEED, MAX_SPEED = 0.1, 5.0


class Transport:
    def __init__(self, dispatcher: Dispatcher, cfg: Config):
        self.dispatcher = dispatcher
        self.cfg = cfg
        self._lock = threading.RLock()
        self.view: SongView | None = None
        self.length = 0.0
        self.state = STOPPED
        self.loop = False
        self._speed = 1.0
        self._anchor_pos = 0.0
        self._anchor_time = 0.0
        self._seek_gen = 0
        self._seek_pos = 0.0   # position the worker must resume from when _seek_gen changes
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._finished = False

    # ------------------------------------------------------------ clock
    def _pos_locked(self) -> float:
        if self.state == PLAYING:
            p = self._anchor_pos + (time.monotonic() - self._anchor_time) * self._speed
        else:
            p = self._anchor_pos
        return min(max(p, 0.0), self.length)

    def position(self) -> float:
        with self._lock:
            return self._pos_locked()

    @property
    def speed(self) -> float:
        return self._speed

    def _reanchor_locked(self) -> None:
        self._anchor_pos = self._pos_locked()
        self._anchor_time = time.monotonic()

    def _jump_locked(self, pos: float) -> None:
        """Move the clock to ``pos`` and tell the worker to re-find its place in the event list."""
        self._anchor_pos = self._seek_pos = min(max(pos, 0.0), self.length)
        self._anchor_time = time.monotonic()
        self._seek_gen += 1

    # ---------------------------------------------------------- control
    def load(self, view: SongView | None, length: float = 0.0) -> None:
        self.stop()
        with self._lock:
            self.view = view
            self.length = length
            self._jump_locked(0.0)

    def play(self) -> bool:
        """Start (from the current position) or resume. Returns False when nothing is loaded."""
        with self._lock:
            if not self.view:
                return False
            if self.state == PLAYING:
                return True
            if self.state == PAUSED:
                self._anchor_time = time.monotonic()
                self.state = PLAYING
                return True
            self._jump_locked(0.0 if self._anchor_pos >= self.length else self._anchor_pos)
            self.state = PLAYING
            self._finished = False
            self._stop_evt = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop_evt,), name="tetomidi-transport", daemon=True)
            self._thread.start()
            return True

    def pause(self) -> None:
        with self._lock:
            if self.state != PLAYING:
                return
            self._reanchor_locked()
            self.state = PAUSED
        if self.dispatcher.c.get("releaseOnPause", True):
            self.dispatcher.release_all()

    def toggle_pause(self) -> None:
        if self.state == PLAYING:
            self.pause()
        elif self.state == PAUSED:
            self.play()

    def stop(self) -> None:
        with self._lock:
            thread, evt = self._thread, self._stop_evt
            evt.set()
            self.state = STOPPED
            self._jump_locked(0.0)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.dispatcher.release_all()

    def seek(self, t: float) -> None:
        with self._lock:
            self._jump_locked(t)
        self.dispatcher.release_all()  # notes held across a jump would otherwise stick

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._reanchor_locked()
            self._speed = min(max(speed, MIN_SPEED), MAX_SPEED)

    def consume_finished(self) -> bool:
        """True once after the song ended on its own (not looping)."""
        with self._lock:
            done, self._finished = self._finished, False
            return done

    # ----------------------------------------------------------- worker
    def _dispatch(self, ev) -> None:
        try:
            if ev.kind == EV_ON:
                self.dispatcher.note_on(ev.note, ev.value, ev.channel)
            elif ev.kind == EV_OFF:
                self.dispatcher.note_off(ev.note, ev.channel)
            elif ev.kind == EV_CC:
                self.dispatcher.control(ev.note, ev.value)
        except Exception:
            log.exception("dispatch failed for %r", ev)

    def _jitter(self, times: list[float], idx: int) -> float:
        """'Random fail' speed error: stretch/shrink the gap before this event (as nanoMIDIPlayer did)."""
        rf = self.dispatcher.c.get("randomFail", {})
        if not rf.get("enabled") or random.random() >= rf.get("speed", 0) / 100:
            return 0.0
        gap = times[idx] - (times[idx - 1] if idx else 0.0)
        return gap * (random.uniform(0.5, 1.5) - 1.0)

    def _run(self, stop_evt: threading.Event) -> None:
        view = self.view
        events, times = view.events, view.times
        n = len(events)
        idx, seen_gen, jitter = 0, -1, None
        while not stop_evt.is_set():
            with self._lock:
                state, gen, speed = self.state, self._seek_gen, self._speed
                pos, start_pos = self._pos_locked(), self._seek_pos
            if state == PAUSED:
                stop_evt.wait(0.01)
                continue
            if state != PLAYING:
                break
            if gen != seen_gen:  # (re)positioned: continue from the first event at/after pos
                idx, seen_gen, jitter = bisect.bisect_left(times, start_pos), gen, None

            if idx < n:
                if jitter is None:
                    jitter = self._jitter(times, idx)
                due = times[idx] + jitter
                if pos >= due:
                    self._dispatch(events[idx])
                    idx, jitter = idx + 1, None
                    continue
                wait = (due - pos) / speed
            else:
                if pos >= self.length:
                    if self.loop:
                        self.dispatcher.release_all()
                        with self._lock:
                            self._jump_locked(0.0)
                        continue
                    break
                wait = (self.length - pos) / speed
            time.sleep(min(max(wait, 0.0005), 0.004))

        if not stop_evt.is_set():  # natural end of the song
            with self._lock:
                if self._stop_evt is stop_evt:
                    self.state = STOPPED
                    self._jump_locked(0.0)
                    self._finished = True
            self.dispatcher.release_all()
