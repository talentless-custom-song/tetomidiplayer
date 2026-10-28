"""MIDI file loading: tempo-aware absolute event times and paired notes for the piano roll."""

from __future__ import annotations

import bisect
import logging
import os
import struct
from dataclasses import dataclass, field

import mido

log = logging.getLogger(__name__)

PERCUSSION_CHANNEL = 9  # MIDI channel 10 (0-based 9)
EV_ON, EV_OFF, EV_CC = 0, 1, 2
SUSTAIN_CC = 64
MIN_NOTE_LEN = 0.04


class MidiLoadError(Exception):
    """Raised with a message that is safe to show to the user."""


@dataclass(frozen=True, slots=True)
class Note:
    start: float
    end: float
    note: int
    velocity: int
    channel: int


@dataclass(frozen=True, slots=True)
class Event:
    time: float
    kind: int          # EV_ON / EV_OFF / EV_CC
    note: int          # note number, or controller number for EV_CC
    value: int         # velocity, or controller value for EV_CC
    channel: int


@dataclass
class SongView:
    """Events and notes of a song filtered for one instrument mode."""

    events: list[Event]
    notes: list[Note]
    times: list[float] = field(init=False)

    def __post_init__(self):
        self.times = [e.time for e in self.events]


@dataclass
class Song:
    path: str
    length: float
    events: list[Event]
    notes: list[Note]
    tempos: list[tuple[float, float]]   # (time, bpm), sorted, first entry at 0.0
    has_percussion: bool
    _views: dict = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def bpm_at(self, t: float) -> float:
        i = bisect.bisect_right([x[0] for x in self.tempos], t) - 1
        return self.tempos[max(i, 0)][1]

    def view(self, mode: str, skip_percussion: bool = True) -> SongView:
        """``mode``: 'piano' | 'guitar' | 'drums'.

        * drums: only the percussion channel when the file has one, otherwise every channel
          (matches nanoMIDIPlayer, which fed every channel to the drum map).
        * piano/guitar: percussion channel removed (``skip_percussion``) so drum hits are not
          typed as piano keys; sustain pedal events only for piano.
        """
        key = (mode, skip_percussion)
        cached = self._views.get(key)
        if cached:
            return cached
        if mode == "drums":
            only_perc = self.has_percussion

            def keep(ch: int) -> bool:
                return ch == PERCUSSION_CHANNEL if only_perc else True
        else:
            def keep(ch: int) -> bool:
                return not (skip_percussion and ch == PERCUSSION_CHANNEL)

        events = [
            e for e in self.events
            if keep(e.channel) and (e.kind != EV_CC or mode == "piano")
        ]
        notes = [n for n in self.notes if keep(n.channel)]
        view = SongView(events=events, notes=notes)
        self._views[key] = view
        return view


def load_song(path: str) -> Song:
    """Parse a .mid/.midi file.  Raises :class:`MidiLoadError` with a friendly message."""
    if not path or not os.path.isfile(path):
        raise MidiLoadError("File not found.")
    if os.path.splitext(path)[1].lower() not in (".mid", ".midi"):
        raise MidiLoadError("Please choose a .mid or .midi file.")
    try:
        mid = mido.MidiFile(path, clip=True)
        if mid.type == 2:
            raise MidiLoadError("Asynchronous (type 2) MIDI files are not supported.")
        events: list[Event] = []
        notes: list[Note] = []
        tempos: list[tuple[float, float]] = []
        open_notes: dict[tuple[int, int], list[tuple[float, int]]] = {}
        has_perc = False
        t = 0.0
        for msg in mid:  # tempo-aware: msg.time is already in seconds
            t += msg.time
            if msg.is_meta:
                if msg.type == "set_tempo" and msg.tempo > 0:
                    tempos.append((t, 60_000_000 / msg.tempo))
                continue
            kind = msg.type
            if kind == "note_on" and msg.velocity > 0:
                events.append(Event(t, EV_ON, msg.note, msg.velocity, msg.channel))
                open_notes.setdefault((msg.channel, msg.note), []).append((t, msg.velocity))
                has_perc = has_perc or msg.channel == PERCUSSION_CHANNEL
            elif kind in ("note_off", "note_on"):
                events.append(Event(t, EV_OFF, msg.note, 0, msg.channel))
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    notes.append(Note(start, max(t, start + MIN_NOTE_LEN), msg.note, vel, msg.channel))
            elif kind == "control_change" and msg.control == SUSTAIN_CC:
                events.append(Event(t, EV_CC, msg.control, msg.value, msg.channel))
        length = t
        for (ch, note), stack in open_notes.items():  # notes never released: close at the end
            for start, vel in stack:
                notes.append(Note(start, max(length, start + MIN_NOTE_LEN), note, vel, ch))
    except MidiLoadError:
        raise
    except (OSError, EOFError, ValueError, KeyError, IndexError, TypeError, struct.error) as exc:
        log.warning("MIDI parse failed for %s: %s", path, exc)
        raise MidiLoadError("This file is not a valid MIDI file.") from exc

    if not notes:
        raise MidiLoadError("This MIDI file contains no notes.")
    notes.sort(key=lambda n: (n.start, n.note))
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0.0, 120.0))  # MIDI default tempo
    return Song(path=path, length=length, events=events, notes=notes, tempos=tempos, has_percussion=has_perc)


def format_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
