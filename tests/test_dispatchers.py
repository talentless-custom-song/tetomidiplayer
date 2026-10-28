import unittest

from tests.helpers import make_engine


class PianoTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.backend, self.keys, self.d, _ = make_engine("piano")

    def acts(self):
        return self.backend.actions()

    def test_plain_note_presses_mapped_key(self):
        self.d.note_on(60, 80, 0)          # 61-key map: 60 -> 't'
        self.assertIn(("press", "t"), self.acts())
        self.d.note_off(60, 0)
        self.assertEqual(self.acts()[-1], ("release", "t"))

    def test_uppercase_key_uses_shift(self):
        self.d.note_on(61, 80, 0)          # 'T'
        a = self.acts()
        self.assertEqual(a[a.index(("press", "shift")):], [("press", "shift"), ("press", "t"), ("release", "shift")])

    def test_symbol_key_presses_digit_with_shift(self):
        self.d.note_on(37, 80, 0)          # '!' -> shift + '1' (note 36)
        a = self.acts()
        self.assertEqual(a[a.index(("press", "shift")):], [("press", "shift"), ("press", "1"), ("release", "shift")])
        self.d.note_off(37, 0)
        self.assertEqual(self.acts()[-1], ("release", "1"))

    def test_extended_88_key_range_uses_ctrl_layer(self):
        self.d.note_on(21, 80, 0)          # low A0 -> ctrl + '1'
        a = self.acts()
        self.assertEqual(a[-3:], [("press", "ctrl"), ("press", "1"), ("release", "ctrl")])

    def test_88key_switch_off_ignores_extended_notes(self):
        self.cfg.set("piano.88Keys", False)
        self.d.note_on(21, 80, 0)
        self.assertEqual(self.acts(), [])
        self.d.note_on(60, 80, 0)
        self.assertIn(("press", "t"), self.acts())

    def test_velocity_sends_alt_plus_velocity_key(self):
        self.cfg.set("piano.velocity", True)
        self.d.note_on(60, 100, 0)
        a = self.acts()
        self.assertEqual(a[:4], [("press", "alt"), ("press", "g"), ("release", "g"), ("release", "alt")])
        self.assertIn(("press", "t"), a)

    def test_velocity_buckets_match_nanomidiplayer_algorithm(self):
        # Reference: findVelocityKey() from nanoMIDIPlayer's midiWindows.py, copied verbatim.
        vmap = self.cfg.data["piano"]["pianoMap"]["velocityMap"]

        def original(velocity):
            thresholds = sorted(int(k) for k in vmap.keys())
            minimum, maximum, index = 0, len(thresholds) - 1, 0
            while minimum <= maximum:
                index = (minimum + maximum) // 2
                if index == 0 or index == len(thresholds) - 1:
                    break
                if thresholds[index] < velocity:
                    minimum = index + 1
                else:
                    maximum = index - 1
            return vmap[str(thresholds[index])]

        for v in range(0, 128):
            self.assertEqual(self.d.velocity_key(v), original(v), v)

    def test_pitch_and_key_offsets_shift_notes(self):
        self.cfg.set("piano.pitchOffset", 12)
        self.d.note_on(48, 80, 0)          # 48 + 12 = 60 -> 't'
        self.assertIn(("press", "t"), self.acts())

    def test_sustain_pedal_maps_to_space(self):
        self.d.control(64, 100)
        self.assertEqual(self.acts(), [])  # sustain disabled
        self.cfg.set("piano.sustain", True)
        self.d.control(64, 100)
        self.assertEqual(self.acts()[-1], ("press", "space"))
        self.d.control(64, 0)
        self.assertEqual(self.acts()[-1], ("release", "space"))

    def test_finger_limit_skips_extra_notes(self):
        self.cfg.set("piano.fingerLimit", 2)
        for n in (60, 62, 64):
            self.d.note_on(n, 80, 0)
        presses = [k for a, k in self.acts() if a == "press"]
        self.assertEqual(presses, ["t", "y"])
        self.assertNotIn("u", presses)

    def test_custom_hold_releases_automatically(self):
        import time
        self.cfg.set("piano.customHoldLength", {"enabled": True, "noteLength": 0.05})
        self.d.note_on(60, 80, 0)
        time.sleep(0.15)
        self.assertEqual(self.acts()[-1], ("release", "t"))

    def test_note_lengths_on_by_default_keeps_key_held_until_note_off(self):
        import time
        self.assertTrue(self.cfg.get("piano.noteLengths"))
        self.d.note_on(60, 80, 0)
        time.sleep(0.3)
        self.assertEqual(self.acts()[-1], ("press", "t"))     # still held: the real note length applies

    def test_note_lengths_off_taps_every_key_for_the_same_short_time(self):
        import time
        from tetomidi.rollmodel import SHORT_NOTE
        self.cfg.set("piano.noteLengths", False)
        self.d.note_on(60, 80, 0)
        self.assertEqual(self.acts()[-1], ("press", "t"))
        time.sleep(SHORT_NOTE + 0.2)
        self.assertEqual(self.acts()[-1], ("release", "t"))    # released without any note_off

    def test_release_all_clears_held_keys(self):
        self.d.note_on(60, 80, 0)
        self.d.note_on(64, 80, 0)
        self.d.release_all()
        self.assertEqual(self.keys.held, set())

    def test_random_transpose_release_matches_pressed_key(self):
        import random
        self.cfg.set("piano.randomFail", {"enabled": True, "speed": 0.0, "transpose": 100.0})
        random.seed(3)
        self.d.note_on(60, 80, 0)
        pressed = [k for a, k in self.acts() if a == "press"]
        self.d.note_off(60, 0)
        self.assertEqual(self.acts()[-1][0], "release")
        self.assertIn(self.acts()[-1][1], [p for p in pressed] + ["t"])
        self.assertEqual(self.keys.held - {"shift", "ctrl", "alt"}, set())


class DrumTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.backend, self.keys, self.d, _ = make_engine("drums")

    def test_default_drum_map(self):
        for note, key in ((38, "c"), (36, "n"), (35, "b"), (42, "x"), (46, "z"), (51, "u")):
            self.backend.log.clear()
            self.d.note_on(note, 100, 9)
            self.assertEqual(self.backend.actions(), [("press", key)])
            self.d.note_off(note, 9)
            self.assertEqual(self.backend.actions()[-1], ("release", key))

    def test_unmapped_note_is_ignored(self):
        self.d.note_on(80, 100, 9)
        self.assertEqual(self.backend.actions(), [])

    def test_user_edited_map_is_used(self):
        self.cfg.set("drums.drumsMap.snare", "k")
        self.d.note_on(38, 100, 9)
        self.assertEqual(self.backend.actions(), [("press", "k")])


class GuitarTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.backend, self.keys, self.d, _ = make_engine("guitar")

    def test_default_keymap_and_retrigger(self):
        self.d.note_on(40, 90, 0)
        self.assertEqual(self.backend.actions(), [("release", "1"), ("press", "1")])
        self.d.note_off(40, 0)
        self.assertEqual(self.backend.actions()[-1], ("release", "1"))

    def test_out_of_range_notes_fold_by_octave(self):
        self.d.note_on(28, 90, 0)   # E1 -> folded up to E2 (40) -> '1'
        self.assertIn(("press", "1"), self.backend.actions())
        self.backend.log.clear()
        self.d.note_on(88, 90, 0)   # folded down into 40..75
        self.assertTrue(any(a == "press" for a, _ in self.backend.actions()))

    def test_folding_can_be_disabled(self):
        self.cfg.set("guitar.foldOctaves", False)
        self.d.note_on(28, 90, 0)
        self.assertEqual(self.backend.actions(), [])

    def test_user_keymap_edit(self):
        self.cfg.set("guitar.keymap.45", "m")
        self.d.note_on(45, 90, 0)
        self.assertIn(("press", "m"), self.backend.actions())


class BlockedKeyTests(unittest.TestCase):
    def test_function_keys_are_never_injected(self):
        cfg, backend, keys, d, _ = make_engine("drums")
        cfg.set("drums.drumsMap.snare", "f2")
        d.note_on(38, 100, 9)
        self.assertEqual(backend.actions(), [])


if __name__ == "__main__":
    unittest.main()
