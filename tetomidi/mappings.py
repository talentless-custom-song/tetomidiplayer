"""Note <-> name tables shared by the macro engines and the piano-roll views."""

from __future__ import annotations

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def is_black(note: int) -> bool:
    return note % 12 in (1, 3, 6, 8, 10)


# ---------------------------------------------------------------- drums
# MIDI note -> key of ``drumsMap`` (identical to nanoMIDIPlayer's drum macro table).
DRUM_NOTE_TO_NAME = {
    42: "closed_Hi-Hat", 44: "closed_Hi-Hat2", 46: "open_Hi-Hat",
    48: "tom1", 50: "tom1_2", 60: "tom", 62: "tom2_2",
    49: "rightCrash", 55: "leftCrash", 38: "snare", 40: "snare2", 37: "snareSide",
    35: "kick", 36: "kick2", 51: "ride", 53: "rideBell", 39: "cowbell",
    52: "crashChina", 57: "splashCrash", 45: "lowTom", 47: "lowMidTom",
}

DRUM_LABELS = {
    "kick": "Kick", "kick2": "Kick 2", "snare": "Snare", "snare2": "Snare 2",
    "snareSide": "Side stick", "tom1": "Tom 1", "tom1_2": "Tom 1 (alt)", "tom": "Tom",
    "tom2_2": "Tom 2", "lowTom": "Low tom", "lowMidTom": "Low-mid tom",
    "closed_Hi-Hat": "Hi-hat closed", "closed_Hi-Hat2": "Hi-hat pedal", "open_Hi-Hat": "Hi-hat open",
    "leftCrash": "Crash left", "rightCrash": "Crash right", "splashCrash": "Splash",
    "crashChina": "China", "ride": "Ride", "rideBell": "Ride bell", "cowbell": "Cowbell",
}

# Lanes shown in the drum roll: (label, [MIDI notes]).
DRUM_LANES = [
    ("Kick", [35, 36]),
    ("Snare", [38, 40, 37]),
    ("Hi-hat", [42, 44]),
    ("Open", [46]),
    ("Tom hi", [48, 50, 60, 62]),
    ("Tom lo", [45, 47]),
    ("Crash", [49, 55, 52, 57]),
    ("Ride", [51, 53]),
    ("Bell", [39]),
]
DRUM_NOTE_TO_LANE = {n: i for i, (_, notes) in enumerate(DRUM_LANES) for n in notes}

# --------------------------------------------------------------- guitar
GUITAR_OPEN_STRINGS = [40, 45, 50, 55, 59, 64]  # E2 A2 D3 G3 B3 E4 (low -> high)
GUITAR_STRING_LABELS = ["E", "A", "D", "G", "B", "e"]
GUITAR_MAX_FRET = 15


def fold_into_range(note: int, low: int, high: int) -> int:
    """Move ``note`` by octaves until it lies inside [low, high] (best effort)."""
    if high - low < 11:
        return note
    while note < low:
        note += 12
    while note > high:
        note -= 12
    return note


def guitar_string_fret(note: int) -> tuple[int, int] | None:
    """Pick a (string index, fret) for ``note``: the lowest fret on the highest usable string."""
    best = None
    for s, open_note in enumerate(GUITAR_OPEN_STRINGS):
        fret = note - open_note
        if 0 <= fret <= GUITAR_MAX_FRET and (best is None or fret < best[1] or (fret == best[1] and s > best[0])):
            best = (s, fret)
    return best


def guitar_resolve_note(note: int, keymap: dict, fold: bool = True) -> int | None:
    """MIDI note that actually gets played for ``note`` given the guitar keymap (None = silent)."""
    if keymap.get(str(note)):
        return note
    if fold:
        nums = [int(k) for k, v in keymap.items() if k.isdigit() and v]
        if nums:
            folded = fold_into_range(note, min(nums), max(nums))
            if keymap.get(str(folded)):
                return folded
    return None
