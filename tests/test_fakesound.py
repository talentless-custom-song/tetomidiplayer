import unittest

import numpy as np

from tests.helpers import make_cfg, make_engine
from tetomidi.fakesound import FakeKeySound, Mixer, is_typed_key, load_clicks, resample


class FakeStream:
    instances = []

    def __init__(self, device=None, samplerate=48000, channels=2, dtype="float32", latency="low", callback=None):
        self.device, self.samplerate, self.channels, self.callback = device, samplerate, channels, callback
        self.started = self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def pull(self, frames=512):
        out = np.zeros((frames, self.channels), dtype=np.float32)
        self.callback(out, frames, None, None)
        return out


class FakeSD:
    """Stands in for the sounddevice module (two outputs: speakers = default, and a virtual cable)."""

    DEVICES = [
        {"name": "Speakers", "max_output_channels": 2, "default_samplerate": 48000.0, "hostapi": 0},
        {"name": "Microphone", "max_output_channels": 0, "default_samplerate": 44100.0, "hostapi": 0},
        {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_output_channels": 2, "default_samplerate": 44100.0, "hostapi": 0},
        {"name": "Other API device", "max_output_channels": 2, "default_samplerate": 44100.0, "hostapi": 1},
    ]

    class default:
        hostapi = 0

    def __init__(self):
        FakeStream.instances = []
        self.OutputStream = FakeStream

    def query_devices(self, device=None, kind=None):
        if device is None:
            if kind == "output":
                return self.DEVICES[0]
            return self.DEVICES
        return self.DEVICES[device]


def make_sound(**settings):
    cfg = make_cfg()
    for key, value in settings.items():
        cfg.set(f"fakeSound.{key}", value)
    return cfg, FakeKeySound(cfg, sd=FakeSD())


class ClickAssetTests(unittest.TestCase):
    def test_bundled_clicks_load(self):
        rate, clips = load_clicks()
        self.assertGreater(rate, 8000)
        self.assertGreater(len(clips), 20)
        for clip in clips:
            self.assertEqual(clip.dtype, np.float32)
            self.assertLessEqual(float(np.abs(clip).max()), 1.0)
            self.assertGreater(float(np.abs(clip).max()), 0.1)

    def test_resample_length(self):
        clip = np.ones(441, dtype=np.float32)
        self.assertEqual(len(resample(clip, 44100, 48000)), 480)
        self.assertIs(resample(clip, 44100, 44100), clip)


class MixerTests(unittest.TestCase):
    def test_voice_plays_then_ends(self):
        mixer = Mixer([np.full(300, 0.5, dtype=np.float32)], channels=2)
        mixer.trigger(1.0)
        first = mixer.render(256)
        self.assertAlmostEqual(float(first[0, 0]), 0.5, places=5)
        self.assertEqual(mixer.active(), 1)
        second = mixer.render(256)
        self.assertEqual(int((second[:, 0] > 0).sum()), 44)   # 300 - 256 samples left
        self.assertEqual(mixer.active(), 0)

    def test_overlap_is_clipped_and_voices_are_limited(self):
        mixer = Mixer([np.full(100, 0.9, dtype=np.float32)], channels=1)
        for _ in range(100):
            mixer.trigger(1.0)
        self.assertLessEqual(mixer.active(), 24)
        self.assertLessEqual(float(mixer.render(64).max()), 1.0)


class KeyFilterTests(unittest.TestCase):
    def test_only_real_key_strokes_click(self):
        self.assertTrue(is_typed_key("a"))
        self.assertTrue(is_typed_key("p+shift"))
        self.assertTrue(is_typed_key("q+ctrl"))
        for silent in ("shift", "ctrl", "alt", "space", "", "a+alt"):
            self.assertFalse(is_typed_key(silent), silent)


class FakeKeySoundTests(unittest.TestCase):
    def test_off_by_default_opens_nothing(self):
        cfg, sound = make_sound()
        sound.apply()
        self.assertEqual(FakeStream.instances, [])
        sound.on_key("piano", "press", "a")   # must not raise

    def test_enabled_opens_default_device_and_clicks(self):
        cfg, sound = make_sound(enabled=True)
        sound.apply()
        self.assertEqual(len(FakeStream.instances), 1)
        stream = FakeStream.instances[0]
        self.assertTrue(stream.started)
        self.assertEqual(stream.pull().max(), 0.0)
        sound.on_key("piano", "press", "a")
        self.assertGreater(float(np.abs(stream.pull()).max()), 0.05)

    def test_release_and_modifier_events_are_silent(self):
        cfg, sound = make_sound(enabled=True)
        sound.apply()
        stream = FakeStream.instances[0]
        sound.on_key("piano", "release", "a")
        sound.on_key("piano", "press", "shift")
        sound.on_key("piano", "press", "space")
        self.assertEqual(float(np.abs(stream.pull()).max()), 0.0)

    def test_volume_zero_is_silent_and_volume_applies_live(self):
        cfg, sound = make_sound(enabled=True, volume=0)
        sound.apply()
        stream = FakeStream.instances[0]
        sound.on_key("piano", "press", "a")
        self.assertEqual(float(np.abs(stream.pull()).max()), 0.0)
        cfg.set("fakeSound.volume", 100)
        sound.on_key("piano", "press", "a")
        self.assertGreater(float(np.abs(stream.pull()).max()), 0.05)

    def test_named_device_plus_speakers(self):
        name = "CABLE Input (VB-Audio Virtual Cable)"
        cfg, sound = make_sound(enabled=True, device=name, alsoSpeakers=True)
        sound.apply()
        self.assertEqual([s.device for s in FakeStream.instances], [2, None])
        self.assertEqual(FakeStream.instances[0].samplerate, 44100)
        self.assertEqual(FakeStream.instances[1].samplerate, 48000)
        sound.on_key("piano", "press", "a")
        for stream in FakeStream.instances:
            self.assertGreater(float(np.abs(stream.pull()).max()), 0.05)

    def test_speakers_option_needs_a_separate_main_device(self):
        cfg, sound = make_sound(enabled=True, alsoSpeakers=True)   # main device already is the default one
        sound.apply()
        self.assertEqual(len(FakeStream.instances), 1)

    def test_missing_device_reports_error_without_streams(self):
        cfg, sound = make_sound(enabled=True, device="Unplugged cable")
        sound.apply()
        self.assertEqual(FakeStream.instances, [])
        self.assertIn("Unplugged cable", sound.error)

    def test_disable_closes_streams(self):
        cfg, sound = make_sound(enabled=True)
        sound.apply()
        stream = FakeStream.instances[0]
        cfg.set("fakeSound.enabled", False)
        sound.apply()
        self.assertTrue(stream.closed)
        sound.on_key("piano", "press", "a")   # no streams: nothing happens

    def test_reapply_replaces_streams(self):
        cfg, sound = make_sound(enabled=True)
        sound.apply()
        sound.apply()
        self.assertTrue(FakeStream.instances[0].closed)
        self.assertFalse(FakeStream.instances[1].closed)

    def test_macro_key_presses_make_clicks(self):
        """End to end: the dispatcher presses keys, the KeySim listener plays a click for each."""
        cfg, backend, keys, disp, transport = make_engine("piano")
        sound = FakeKeySound(cfg, sd=FakeSD())
        cfg.set("fakeSound.enabled", True)
        sound.apply()
        keys.add_listener(sound.on_key)
        stream = FakeStream.instances[0]
        disp.note_on(60, 90, 0)
        self.assertGreater(float(np.abs(stream.pull()).max()), 0.05)
        disp.release_all()


if __name__ == "__main__":
    unittest.main()
