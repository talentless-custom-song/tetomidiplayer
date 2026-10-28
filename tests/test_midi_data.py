import os
import tempfile
import unittest

import mido

from tetomidi.midi_data import MidiLoadError, format_time, load_song
from tests.helpers import make_midi


class MidiDataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_notes_durations_and_length_follow_tempo(self):
        p = make_midi(self.path("a.mid"), [(0, 1, 60, 90), (1, 2, 64, 50)], tempo=500000)  # 120 bpm
        song = load_song(p)
        self.assertEqual(len(song.notes), 2)
        n0, n1 = song.notes
        self.assertAlmostEqual(n0.start, 0.0, places=3)
        self.assertAlmostEqual(n0.end, 0.5, places=3)
        self.assertAlmostEqual(n1.start, 0.5, places=3)
        self.assertAlmostEqual(n1.end, 1.5, places=3)
        self.assertAlmostEqual(song.length, 1.5, places=3)
        self.assertEqual(n1.velocity, 50)
        self.assertAlmostEqual(song.bpm_at(0.2), 120.0, places=2)

    def test_tempo_change_is_respected(self):
        p = self.path("t.mid")
        mid = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack(); mid.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))      # 60 bpm
        tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=60, velocity=0, time=480))      # 1 s
        tr.append(mido.MetaMessage("set_tempo", tempo=250_000, time=0))         # 240 bpm
        tr.append(mido.Message("note_on", note=62, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=62, velocity=0, time=480))      # 0.25 s
        mid.save(p)
        song = load_song(p)
        self.assertAlmostEqual(song.length, 1.25, places=3)
        self.assertAlmostEqual(song.bpm_at(0.5), 60.0, places=2)
        self.assertAlmostEqual(song.bpm_at(1.1), 240.0, places=2)

    def test_default_tempo_when_file_has_none(self):
        p = self.path("n.mid")
        mid = mido.MidiFile(); tr = mido.MidiTrack(); mid.tracks.append(tr)
        tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
        mid.save(p)
        self.assertEqual(load_song(p).bpm_at(0), 120.0)

    def test_note_on_velocity_zero_ends_a_note(self):
        p = self.path("z.mid")
        mid = mido.MidiFile(); tr = mido.MidiTrack(); mid.tracks.append(tr)
        tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
        tr.append(mido.Message("note_on", note=60, velocity=0, time=480))
        mid.save(p)
        song = load_song(p)
        self.assertEqual(len(song.notes), 1)
        self.assertAlmostEqual(song.notes[0].end - song.notes[0].start, 0.5, places=3)

    def test_mode_views_split_percussion(self):
        p = self.path("m.mid")
        mid = mido.MidiFile(); tr = mido.MidiTrack(); mid.tracks.append(tr)
        for ch, note in ((0, 60), (9, 36)):
            tr.append(mido.Message("note_on", note=note, velocity=80, channel=ch, time=0))
            tr.append(mido.Message("note_off", note=note, velocity=0, channel=ch, time=240))
        mid.save(p)
        song = load_song(p)
        self.assertTrue(song.has_percussion)
        self.assertEqual({n.note for n in song.view("piano").notes}, {60})
        self.assertEqual({n.note for n in song.view("guitar").notes}, {60})
        self.assertEqual({n.note for n in song.view("drums").notes}, {36})

    def test_drum_view_uses_all_channels_without_percussion_channel(self):
        p = make_midi(self.path("d.mid"), [(0, 1, 38, 90)], channel=0)
        song = load_song(p)
        self.assertFalse(song.has_percussion)
        self.assertEqual(len(song.view("drums").notes), 1)

    def test_invalid_files_give_friendly_errors(self):
        bad = self.path("bad.mid")
        with open(bad, "wb") as f:
            f.write(b"this is not midi")
        with self.assertRaises(MidiLoadError) as cm:
            load_song(bad)
        self.assertIn("valid MIDI", str(cm.exception))
        with self.assertRaises(MidiLoadError):
            load_song(self.path("missing.mid"))
        txt = self.path("x.txt")
        with open(txt, "w") as f:
            f.write("x")
        with self.assertRaises(MidiLoadError):
            load_song(txt)
        empty = self.path("empty.mid")
        mid = mido.MidiFile(); mid.tracks.append(mido.MidiTrack()); mid.save(empty)
        with self.assertRaises(MidiLoadError):
            load_song(empty)

    def test_format_time(self):
        self.assertEqual(format_time(0), "00:00:00")
        self.assertEqual(format_time(3725.9), "01:02:05")


if __name__ == "__main__":
    unittest.main()
