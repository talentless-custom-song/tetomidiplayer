import os
import struct
import tempfile
import unittest

import numpy as np

from tests.helpers import make_cfg
from tetomidi.defaults import fresh_defaults
from tetomidi.pianosound import PCM16_SCALE, SOUNDFONT_FILE, PianoMixer, PianoSound
from tetomidi.paths import resource_path
from tetomidi.sf2 import SoundFont, SoundFontError

RATE = 22050


def chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def tiny_font(path: str, key_range=(0, 127), loop=True) -> str:
    """A one-sample SoundFont: a 440 Hz sine (A4 = key 69, 22050 Hz) with a loop over its last whole periods."""
    frames = 22050
    wave = (np.sin(2 * np.pi * 440.0 * np.arange(frames) / 22050.0) * 20000).astype("<i2")
    smpl = wave.tobytes() + b"\0" * 92
    name = lambda n: n.encode().ljust(20, b"\0")
    phdr = struct.pack("<20sHHHIII", name("Tiny"), 0, 0, 0, 0, 0, 0) + struct.pack("<20sHHHIII", name("EOP"), 0, 0, 1, 0, 0, 0)
    pbag = struct.pack("<HH", 0, 0) + struct.pack("<HH", 1, 0)
    pgen = struct.pack("<HBB", 41, 0, 0) + struct.pack("<HBB", 0, 0, 0)
    inst = struct.pack("<20sH", name("Sine"), 0) + struct.pack("<20sH", name("EOI"), 1)
    ibag = struct.pack("<HH", 0, 0) + struct.pack("<HH", 4, 0)
    igen = (struct.pack("<HBB", 43, *key_range) + struct.pack("<Hh", 54, 1 if loop else 0)
            + struct.pack("<Hh", 58, 69) + struct.pack("<HH", 53, 0))
    shdr = (struct.pack("<20sIIIIIBbHH", name("Sine"), 0, frames, frames - 4410, frames - 2, 22050, 69, 0, 0, 1)
            + struct.pack("<20sIIIIIBbHH", name("EOS"), 0, 0, 0, 0, 0, 0, 0, 0, 0))
    pdta = b"pdta" + b"".join(chunk(k, v) for k, v in (
        (b"phdr", phdr), (b"pbag", pbag), (b"pmod", b"\0" * 10), (b"pgen", pgen), (b"inst", inst), (b"ibag", ibag),
        (b"imod", b"\0" * 10), (b"igen", igen), (b"shdr", shdr)))
    body = (b"sfbk" + chunk(b"LIST", b"INFO" + chunk(b"ifil", struct.pack("<HH", 2, 1)) + chunk(b"INAM", b"Tiny\0\0"))
            + chunk(b"LIST", b"sdta" + chunk(b"smpl", smpl)) + chunk(b"LIST", pdta))
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def peak_hz(clip, rate):
    mono = clip[:, 0].astype(float)[2000:2000 + 16384]
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)), 1 << 16))
    return np.fft.rfftfreq(1 << 16, 1 / rate)[np.argmax(spec)]


class FakeStream:
    def __init__(self, samplerate=48000, channels=2, dtype="float32", latency="low", callback=None):
        self.samplerate, self.channels, self.callback = samplerate, channels, callback

    def start(self): pass
    def stop(self): pass
    def close(self): pass

    def pull(self, frames=512):
        out = np.zeros((frames, self.channels), dtype=np.float32)
        self.callback(out, frames, None, None)
        return out


class FakeSD:
    def __init__(self, rate=RATE, channels=2):
        self.rate, self.channels, self.streams = rate, channels, []

    def query_devices(self, device=None, kind=None):
        return {"name": "Speakers", "max_output_channels": self.channels, "default_samplerate": float(self.rate)}

    def OutputStream(self, **kw):
        self.streams.append(FakeStream(**kw))
        return self.streams[-1]


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tetomidi-sf2-")

    def test_tiny_font_plays_the_right_pitch(self):
        font = SoundFont.load(tiny_font(os.path.join(self.dir, "t.sf2")))
        clip = font.render_note(69, RATE)
        self.assertEqual(clip.dtype, np.int16)
        self.assertEqual(clip.shape, (4 * RATE, 2))
        self.assertAlmostEqual(peak_hz(clip, RATE), 440.0, delta=3)
        self.assertAlmostEqual(peak_hz(font.render_note(81, RATE), RATE), 880.0, delta=6)   # an octave up
        self.assertAlmostEqual(peak_hz(font.render_note(57, RATE), RATE), 220.0, delta=3)   # an octave down

    def test_looped_note_holds_then_fades_without_a_click(self):
        clip = SoundFont.load(tiny_font(os.path.join(self.dir, "t.sf2"))).render_note(69, RATE)
        mid = np.abs(clip[RATE:2 * RATE]).max()
        self.assertGreater(mid, 20000)                  # still sounding after a second: the loop works
        self.assertLess(np.abs(clip[-50:]).max(), 300)  # faded out at the end

    def test_unlooped_note_ends_with_the_sample(self):
        font = SoundFont.load(tiny_font(os.path.join(self.dir, "t.sf2"), loop=False))
        clip = font.render_note(69, RATE)
        self.assertEqual(len(clip), 22050)
        self.assertEqual(int(clip[-1, 0]), 0)

    def test_note_outside_the_key_range_has_no_clip(self):
        font = SoundFont.load(tiny_font(os.path.join(self.dir, "t.sf2"), key_range=(60, 72)))
        self.assertTrue(font.covers(65))
        self.assertFalse(font.covers(30))
        self.assertIsNone(font.render_note(30, RATE))

    def test_bad_files_raise_soundfont_error(self):
        junk = os.path.join(self.dir, "junk.sf2")
        with open(junk, "wb") as fh:
            fh.write(b"not a soundfont at all")
        with self.assertRaises(SoundFontError):
            SoundFont.load(junk)
        with self.assertRaises(SoundFontError):
            SoundFont.load(os.path.join(self.dir, "missing.sf2"))
        good = open(tiny_font(os.path.join(self.dir, "t.sf2")), "rb").read()
        cut = os.path.join(self.dir, "cut.sf2")
        with open(cut, "wb") as fh:
            fh.write(good[:len(good) // 2])
        with self.assertRaises(SoundFontError):
            SoundFont.load(cut)


class BundledPianoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = SoundFont.load(resource_path(SOUNDFONT_FILE))

    def test_every_piano_key_renders_in_stereo(self):
        for note in (21, 40, 60, 84, 108):
            clip = self.font.render_note(note, 44100)
            self.assertIsNotNone(clip, note)
            self.assertEqual(clip.shape[1], 2)
            self.assertGreater(int(np.abs(clip).max()), 20000)

    def test_middle_c_is_in_tune(self):
        clip = self.font.render_note(60, 48000)
        self.assertAlmostEqual(peak_hz(clip, 48000), 261.63, delta=2.5)

    def test_damper_sample_is_left_out(self):
        self.assertFalse(any("damper" in z.sample.name.lower() for z in self.font.zones))


class MixerTests(unittest.TestCase):
    def test_stereo_int16_clip_keeps_its_channels(self):
        mixer = PianoMixer(channels=2, rate=RATE)
        clip = np.zeros((100, 2), dtype=np.int16)
        clip[:, 0] = 32767
        mixer.note_on(clip, np.float32(PCM16_SCALE), 60)
        out = mixer.render(50)
        self.assertGreater(float(out[:, 0].min()), 0.5)
        self.assertEqual(float(np.abs(out[:, 1]).max()), 0.0)

    def test_stereo_clip_on_a_mono_device_is_averaged(self):
        mixer = PianoMixer(channels=1, rate=RATE)
        clip = np.full((100, 2), 16000, dtype=np.int16)
        mixer.note_on(clip, np.float32(PCM16_SCALE), 60)
        self.assertEqual(mixer.render(10).shape, (10, 1))


class PianoSoundFontTests(unittest.TestCase):
    def make(self, **hub):
        cfg = make_cfg()
        for key, value in hub.items():
            cfg.set(f"hub.{key}", value)
        sd = FakeSD()
        sound = PianoSound(cfg, sd=sd)
        sound.apply()
        return cfg, sound, sd

    def test_soundfont_is_the_default_and_makes_sound(self):
        _, sound, sd = self.make()
        self.assertTrue(sound.using_soundfont)
        sound.note_on(60, 100)
        out = sd.streams[0].pull(RATE // 2)
        self.assertGreater(float(np.abs(out).max()), 0.05)
        self.assertLessEqual(float(np.abs(out).max()), 1.0)
        self.assertNotEqual(float(np.abs(out[:, 0] - out[:, 1]).max()), 0.0)   # real stereo
        sound.close()

    def test_switched_off_uses_the_synth(self):
        _, sound, sd = self.make(useSoundfont=False)
        self.assertFalse(sound.using_soundfont)
        sound.note_on(60, 100)
        self.assertGreater(float(np.abs(sd.streams[0].pull(512)).max()), 0.0)
        sound.close()

    def test_broken_font_file_falls_back_to_the_synth(self):
        bad = os.path.join(tempfile.mkdtemp(), "bad.sf2")
        with open(bad, "wb") as fh:
            fh.write(b"nope")
        _, sound, sd = self.make(soundfont=bad)
        self.assertFalse(sound.using_soundfont)
        self.assertTrue(sound.font_error)
        sound.note_on(60, 100)
        self.assertGreater(float(np.abs(sd.streams[0].pull(512)).max()), 0.0)
        sound.close()

    def test_setting_change_takes_effect_on_the_next_apply(self):
        cfg, sound, _ = self.make()
        sound.note_on(60, 100)
        self.assertEqual(sound._cache[60].dtype, np.int16)
        cfg.set("hub.useSoundfont", False)
        sound.apply()
        sound.note_on(60, 100)
        self.assertEqual(sound._cache[60].dtype, np.float32)
        sound.close()


class DefaultsTests(unittest.TestCase):
    def test_fake_keyboard_sound_is_off_by_default(self):
        self.assertFalse(fresh_defaults()["fakeSound"]["enabled"])
        self.assertFalse(make_cfg().get("fakeSound.enabled"))

    def test_hub_soundfont_defaults(self):
        hub = fresh_defaults()["hub"]
        self.assertTrue(hub["useSoundfont"])
        self.assertEqual(hub["soundfont"], "")

    def test_old_config_without_the_new_keys_gets_them(self):
        import json
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as fh:
            json.dump({"hub": {"apiUrl": "https://x.example"}}, fh)
        from tetomidi.config import Config
        cfg = Config(path, autosave=False)
        self.assertEqual(cfg.get("hub.apiUrl"), "https://x.example")
        self.assertTrue(cfg.get("hub.useSoundfont"))
        self.assertFalse(cfg.get("fakeSound.enabled"))


if __name__ == "__main__":
    unittest.main()
