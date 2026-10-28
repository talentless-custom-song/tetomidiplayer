"""Piano-roll data model (no GUI): notes -> lanes, with a fast 'what is visible at time t' query.

The lane assignment uses the *same* mapping rules as the macro dispatchers (pitch/key offsets and
88/61-key range for piano, drum map for drums, guitar keymap + octave folding for guitar), so the
roll shows exactly the notes that will be pressed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .mappings import (
    DRUM_NOTE_TO_LANE,
    DRUM_NOTE_TO_NAME,
    guitar_resolve_note,
    guitar_string_fret,
)
from .midi_data import SongView

SHORT_NOTE = 0.12   # length (s) of every note when "Note lengths" is off

PIANO_RANGE_88 = (21, 108)
PIANO_RANGE_61 = (36, 96)


@dataclass
class RollData:
    kind: str                       # 'piano' | 'drums' | 'guitar'
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    lanes: list[int] = field(default_factory=list)    # piano: MIDI pitch, drums: lane, guitar: string
    vels: list[int] = field(default_factory=list)
    frets: list[int] = field(default_factory=list)    # guitar only (0 otherwise)
    prefix_end: list[float] = field(default_factory=list)
    key_range: tuple[int, int] = PIANO_RANGE_88

    def finish(self) -> "RollData":
        best = 0.0
        self.prefix_end = []
        for e in self.ends:
            best = max(best, e)
            self.prefix_end.append(best)
        return self

    def __len__(self) -> int:
        return len(self.starts)

    def window(self, pos: float, look: float) -> tuple[int, int]:
        """Index range [lo, hi) that may be visible between ``pos`` and ``pos + look``."""
        lo = bisect.bisect_right(self.prefix_end, pos)
        hi = bisect.bisect_left(self.starts, pos + look)
        return lo, max(lo, hi)

    def _add(self, n, lane: int, fret: int = 0, uniform: bool = False) -> None:
        self.starts.append(n.start)
        self.ends.append(n.start + SHORT_NOTE if uniform else n.end)
        self.lanes.append(lane)
        self.vels.append(n.velocity)
        self.frets.append(fret)


def build_piano(view: SongView | None, piano_cfg: dict) -> RollData:
    lo, hi = PIANO_RANGE_88 if piano_cfg.get("88Keys", True) else PIANO_RANGE_61
    data = RollData("piano", key_range=(lo, hi))
    uniform = not piano_cfg.get("noteLengths", True)
    if view:
        shift = piano_cfg.get("pitchOffset", 0) + piano_cfg.get("transposeOffset", 0)
        for n in view.notes:
            pitch = n.note + shift
            if lo <= pitch <= hi:
                data._add(n, pitch, uniform=uniform)
    return data.finish()


def build_drums(view: SongView | None, drums_cfg: dict) -> RollData:
    data = RollData("drums")
    uniform = not drums_cfg.get("noteLengths", True)
    if view:
        dmap = drums_cfg.get("drumsMap", {})
        for n in view.notes:
            name = DRUM_NOTE_TO_NAME.get(n.note)
            if name and dmap.get(name):
                data._add(n, DRUM_NOTE_TO_LANE[n.note], uniform=uniform)
    return data.finish()


def build_guitar(view: SongView | None, guitar_cfg: dict) -> RollData:
    data = RollData("guitar")
    uniform = not guitar_cfg.get("noteLengths", True)
    if view:
        keymap = guitar_cfg.get("keymap", {})
        fold = guitar_cfg.get("foldOctaves", True)
        for n in view.notes:
            note = guitar_resolve_note(n.note, keymap, fold)
            if note is None:
                continue
            sf = guitar_string_fret(note)
            if sf:
                data._add(n, sf[0], sf[1], uniform=uniform)
    return data.finish()


def build_roll(mode: str, view: SongView | None, mode_cfg: dict) -> RollData:
    return {"piano": build_piano, "drums": build_drums, "guitar": build_guitar}[mode](view, mode_cfg)
