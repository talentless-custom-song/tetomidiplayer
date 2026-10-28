import os
import tempfile
import time
import unittest

from tetomidi.midi_data import load_song
from tests.helpers import make_engine, make_midi


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # 120 bpm: beats at 0, .5, 1.0, 1.5 s -> keys for notes 60,62,64,65 = t,y,u,i
        self.path = make_midi(os.path.join(self.dir, "s.mid"),
                              [(0, 0.5, 60, 90), (1, 0.5, 62, 90), (2, 0.5, 64, 90), (3, 0.5, 65, 90)])
        self.song = load_song(self.path)
        self.cfg, self.backend, self.keys, self.disp, self.tr = make_engine("piano")
        self.tr.load(self.song.view("piano"), self.song.length)
        self.addCleanup(self.tr.stop)

    def presses(self):
        t0 = self.t0
        return [(round(t - t0, 3), k) for t, a, k in self.backend.log if a == "press" and k in "tyui"]

    def wait_state(self, state, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.tr.state == state:
                return True
            time.sleep(0.005)
        return False

    def test_events_fire_on_schedule(self):
        self.t0 = time.monotonic()
        self.assertTrue(self.tr.play())
        self.assertTrue(self.wait_state("stopped", 4))
        p = self.presses()
        self.assertEqual([k for _, k in p], ["t", "y", "u", "i"])
        for (t, _), want in zip(p, (0.0, 0.5, 1.0, 1.5)):
            self.assertAlmostEqual(t, want, delta=0.06)
        self.assertTrue(self.tr.consume_finished())
        self.assertFalse(self.tr.consume_finished())
        self.assertEqual(self.keys.held, set())
        self.assertEqual(self.tr.position(), 0.0)

    def test_speed_scales_the_clock(self):
        self.tr.set_speed(2.0)
        self.t0 = time.monotonic()
        self.tr.play()
        self.assertTrue(self.wait_state("stopped", 3))
        p = self.presses()
        for (t, _), want in zip(p, (0.0, 0.25, 0.5, 0.75)):
            self.assertAlmostEqual(t, want, delta=0.06)

    def test_position_tracks_the_dispatch_clock(self):
        self.tr.play()
        time.sleep(0.6)
        pos = self.tr.position()
        self.assertAlmostEqual(pos, 0.6, delta=0.08)
        self.assertEqual([k for a, k in self.backend.actions() if a == "press" and k in "tyui"][:2], ["t", "y"])

    def test_pause_freezes_clock_and_resume_continues(self):
        self.tr.play()
        time.sleep(0.3)
        self.tr.pause()
        self.assertEqual(self.tr.state, "paused")
        frozen = self.tr.position()
        time.sleep(0.4)
        self.assertAlmostEqual(self.tr.position(), frozen, places=3)
        self.assertEqual(self.keys.held, set())          # released on pause
        pressed_before = [k for a, k in self.backend.actions() if a == "press" and k in "tyui"]
        self.assertEqual(pressed_before, ["t"])           # 'y' (0.5s) must not fire while paused
        self.tr.toggle_pause()
        self.assertEqual(self.tr.state, "playing")
        self.assertTrue(self.wait_state("stopped", 4))
        self.assertEqual([k for a, k in self.backend.actions() if a == "press" and k in "tyui"], ["t", "y", "u", "i"])

    def test_seek_skips_earlier_events(self):
        self.tr.play()
        self.tr.seek(1.2)                                  # after 't' and 'y', before 'i' (1.5)
        time.sleep(0.1)
        self.assertGreaterEqual(self.tr.position(), 1.2)
        self.assertTrue(self.wait_state("stopped", 3))
        pressed = [k for a, k in self.backend.actions() if a == "press" and k in "tyui"]
        self.assertEqual(pressed[-1], "i")
        self.assertNotIn("u", pressed[1:])                 # 1.0s event skipped after the seek

    def test_seek_while_stopped_sets_start_position(self):
        self.tr.seek(1.0)
        self.assertAlmostEqual(self.tr.position(), 1.0, places=3)
        self.tr.play()
        self.assertTrue(self.wait_state("stopped", 3))
        pressed = [k for a, k in self.backend.actions() if a == "press" and k in "tyui"]
        self.assertEqual(pressed, ["u", "i"])

    def test_loop_restarts_until_stopped(self):
        self.tr.loop = True
        self.tr.set_speed(5.0)
        self.tr.play()
        time.sleep(1.2)                                    # song takes 0.4 s at 5x
        self.assertEqual(self.tr.state, "playing")
        count = len([1 for a, k in self.backend.actions() if a == "press" and k == "t"])
        self.assertGreaterEqual(count, 2)
        self.tr.stop()
        self.assertEqual(self.tr.state, "stopped")
        self.assertEqual(self.keys.held, set())

    def test_stop_releases_every_key_and_resets_position(self):
        self.tr.play()
        time.sleep(0.7)
        self.tr.stop()
        self.assertEqual(self.keys.held, set())
        self.assertEqual(self.tr.position(), 0.0)
        self.assertFalse(self.tr.consume_finished())      # manual stop is not "finished"

    def test_play_without_song_returns_false(self):
        _, _, _, _, tr = make_engine("piano")
        self.assertFalse(tr.play())

    def test_speed_is_clamped(self):
        self.tr.set_speed(50)
        self.assertEqual(self.tr.speed, 5.0)
        self.tr.set_speed(0.0)
        self.assertEqual(self.tr.speed, 0.1)


if __name__ == "__main__":
    unittest.main()
