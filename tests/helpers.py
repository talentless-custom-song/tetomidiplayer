import os
import tempfile

import mido

from tetomidi.config import Config
from tetomidi.engine.dispatchers import create_dispatcher
from tetomidi.engine.transport import Transport
from tetomidi.keysim import KeySim, RecordingBackend


def make_cfg():
    d = tempfile.mkdtemp(prefix="tetomidi-test-")
    return Config(os.path.join(d, "config.json"), autosave=False)


def make_midi(path, notes, tempo=500000, channel=0, extra=None, ticks=480):
    """notes: [(start_beat, length_beats, note, velocity)]; one beat = one quarter note."""
    mid = mido.MidiFile(ticks_per_beat=ticks)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    evs = []
    for start, length, note, vel in notes:
        evs.append((round(start * ticks), 1, mido.Message("note_on", note=note, velocity=vel, channel=channel)))
        evs.append((round((start + length) * ticks), 0, mido.Message("note_off", note=note, velocity=0, channel=channel)))
    for t, msg in extra or []:
        evs.append((round(t * ticks), 1, msg))
    evs.sort(key=lambda e: (e[0], e[1]))
    last = 0
    for t, _, msg in evs:
        tr.append(msg.copy(time=t - last))
        last = t
    mid.save(path)
    return path


def make_engine(mode, cfg=None):
    cfg = cfg or make_cfg()
    backend = RecordingBackend()
    keys = KeySim(backend)
    disp = create_dispatcher(mode, keys, cfg)
    return cfg, backend, keys, disp, Transport(disp, cfg)
