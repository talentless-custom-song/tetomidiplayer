"""Default configuration for tetoMIDI."""

from __future__ import annotations

import copy

from .defaults_maps import DRUMS_MAP, GUITAR_KEYMAP, PIANO_MAP


def _common(**extra):
    base = {
        "currentFile": "",
        "loop": False,
        "noteLengths": True,   # off: every note is short and the same length (roll and key presses)
        "releaseOnPause": True,
        "customHoldLength": {"enabled": False, "noteLength": 0.1},
        "fingerLimit": 11,  # 11 = unlimited (same convention as nanoMIDIPlayer)
        "randomFail": {"enabled": False, "speed": 5.0},
    }
    base.update(extra)
    return base


DEFAULTS = {
    "hotkeys": {"play": "f2", "pause": "f3", "stop": "f4", "speedup": "f5", "slowdown": "f6"},
    "app": {"topmost": False, "speedStep": 5, "opacity": 1.0},
    "player": {"playlist": [], "index": -1, "repeat": "off", "shuffle": False, "mode": "piano"},
    "mini": {"console": True},
    # MIDI Hub: server address ("" = the built-in HUB_API_URL); preview sound = piano SoundFont (hub.soundfont:
    # path of an .sf2, "" = the bundled assets/sounds/Piano.sf2) or, when off, the synthesised piano
    "hub": {"apiUrl": "", "useSoundfont": True, "soundfont": ""},
    # fake keyboard sound: a typing click per key press, sent to an audio device (e.g. a virtual cable -> Roblox mic)
    "fakeSound": {"enabled": False, "volume": 80, "device": "", "alsoSpeakers": False},
    "roll": {"fallSeconds": 3.0, "glow": True, "keyLabels": True, "noteColor": "blue",
             "background": "", "backgroundDim": 50,   # background: copied image file ("" = plain colour)
             "piano3D": False},                       # piano roll only: perspective view (notes recede to the top)
    "piano": _common(
        **{
            "88Keys": True,
            "sustain": False,
            "velocity": False,
            "noDoubles": True,
            "sustainCutoff": 63,
            "pitchOffset": 0,
            "transposeOffset": 0,
            "skipPercussion": True,
            "randomFail": {"enabled": False, "speed": 5.0, "transpose": 5.0},
            "pianoMap": PIANO_MAP,
        }
    ),
    "drums": _common(drumsMap=DRUMS_MAP),
    "guitar": _common(foldOctaves=True, keymap=GUITAR_KEYMAP, skipPercussion=True),
}


def fresh_defaults() -> dict:
    return copy.deepcopy(DEFAULTS)
