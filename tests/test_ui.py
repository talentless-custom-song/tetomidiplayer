"""GUI integration tests (need Tk and a display: run with `xvfb-run -a python -m unittest` on Linux)."""

import os
import tempfile
import time
import unittest

try:
    import tkinter
    _r = tkinter.Tk()
    _r.destroy()
    HAVE_DISPLAY = True
except Exception:  # no tkinter / no display
    HAVE_DISPLAY = False

from tests.helpers import make_midi


def click(widget, x=None, y=None):
    widget.update_idletasks()
    x = widget.winfo_width() // 2 if x is None else x
    y = widget.winfo_height() // 2 if y is None else y
    widget.event_generate("<Enter>")
    widget.event_generate("<ButtonPress-1>", x=x, y=y)
    widget.event_generate("<ButtonRelease-1>", x=x, y=y)
    widget.update()


@unittest.skipUnless(HAVE_DISPLAY, "needs Tk and a display")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TETOMIDI_CONFIG_DIR"] = tempfile.mkdtemp(prefix="tm-ui-")
        from tetomidi.keysim import KeySim, RecordingBackend
        from tetomidi.ui.app import TetoApp

        cls.dir = tempfile.mkdtemp()
        cls.piano_mid = make_midi(os.path.join(cls.dir, "piano.mid"),
                                  [(i * 0.5, 0.4, 60 + (i % 5), 90) for i in range(40)])
        cls.drum_mid = make_midi(os.path.join(cls.dir, "drums.mid"),
                                 [(i * 0.5, 0.1, (36, 38, 42)[i % 3], 100) for i in range(40)], channel=9)
        cls.backend = RecordingBackend()
        cls.app = TetoApp(keys=KeySim(cls.backend))
        cls.app.root.geometry("1060x620+0+0")
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()

    def setUp(self):
        self.app.show_page("piano")
        for t in self.app.transports.values():
            t.stop()
        self.backend.log.clear()
        self.app.root.update()

    def presses(self):
        return [k for a, k in self.backend.actions() if a == "press"]

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.root.update()
            time.sleep(0.01)

    # --- navigation ---------------------------------------------------
    def test_sidebar_has_the_five_entries_and_switches_pages(self):
        side = self.app.sidebar
        self.assertEqual(list(side.items), ["piano", "drums", "guitar", "hub", "settings"])
        for key in ("drums", "guitar", "hub", "settings", "piano"):
            click(side.items[key])
            self.assertEqual(self.app.current, key)
            self.assertTrue(side.items[key].active)

    def test_discord_and_website_icons_open_the_right_links(self):
        import tetomidi.ui.sidebar as sb
        opened = []
        orig = sb.webbrowser.open_new_tab
        sb.webbrowser.open_new_tab = lambda url: opened.append(url) or True
        try:
            buttons = [w for w in self.app.sidebar.winfo_children()[-1].winfo_children()]
            self.assertEqual(len(buttons), 2)
            for b in buttons:
                click(b)
        finally:
            sb.webbrowser.open_new_tab = orig
        self.assertEqual(opened, ["https://discord.gg/peaqm6uqgB", "https://tetomidi-pianits.vercel.app/"])

    # --- files --------------------------------------------------------
    def test_invalid_file_shows_a_clear_message_and_keeps_the_old_song(self):
        page = self.app.pages["piano"]
        self.assertTrue(page.load_path(self.piano_mid))
        bad = os.path.join(self.dir, "bad.mid")
        with open(bad, "wb") as f:
            f.write(b"garbage")
        self.assertFalse(page.load_path(bad))
        self.assertIn("not a valid MIDI", page.status._text)
        self.assertIs(page.song.path, self.piano_mid)

    def test_valid_file_fills_roll_and_time(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        self.app.root.update()
        self.assertEqual(len(page.roll.data), 40)
        self.assertEqual(page.time._text, "00:00:00/00:00:09")

    def test_piano_roll_3d_setting_draws_extruded_notes(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        self.app.cfg.set("roll.piano3D", True)
        self.app.root.update()
        self.assertTrue(page.roll._is3d())
        self.assertGreater(page.roll._pool_used, 0)
        self.assertEqual(page.roll._extrusion_used, page.roll._pool_used)
        self.assertEqual(page.roll.type(page.roll._pool[0]), "polygon")

        self.app.cfg.set("roll.piano3D", False)
        self.app.root.update()
        self.assertFalse(page.roll._is3d())
        self.assertEqual(page.roll.type(page.roll._pool[0]), "rectangle")

    # --- playback -----------------------------------------------------
    def test_play_pause_stop_buttons_and_roll_follow_the_clock(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        click(page.play_btn)
        self.pump(0.9)
        self.assertEqual(page.transport.state, "playing")
        self.assertEqual(page.play_btn._text, "PAUSE")
        self.assertTrue(self.presses())
        pos = page.transport.position()
        self.assertAlmostEqual(page.slider.value, pos / page.transport.length, delta=0.03)
        self.assertTrue(page.roll._lit, "keys under playing notes must be lit")
        click(page.play_btn)
        self.assertEqual(page.transport.state, "paused")
        frozen = page.transport.position()
        self.pump(0.3)
        self.assertEqual(page.transport.position(), frozen)
        click(page.stop_btn)
        self.assertEqual(page.transport.state, "stopped")
        self.assertEqual(self.app.keys.held, set())
        self.assertEqual(page.time._text, "00:00:00/00:00:09")

    def test_hotkeys_f2_f3_f4_drive_the_current_page(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        hk = self.app.hotkeys
        hk.trigger("f2"); self.pump(0.2)
        self.assertEqual(page.transport.state, "playing")
        hk.trigger("f3"); self.pump(0.1)
        self.assertEqual(page.transport.state, "paused")
        hk.trigger("f3"); self.pump(0.1)
        self.assertEqual(page.transport.state, "playing")
        hk.trigger("f4"); self.pump(0.1)
        self.assertEqual(page.transport.state, "stopped")

    def test_slider_seek_moves_the_clock(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        s = page.slider
        s.update()
        x = int(s._x_for(0.5))
        click(s, x=x, y=s.winfo_height() // 2)
        self.assertAlmostEqual(page.transport.position() / page.transport.length, 0.5, delta=0.05)

    def test_loop_toggle_is_real(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        click(page.toggles["loop"])
        self.assertTrue(self.app.cfg.get("piano.loop"))
        self.assertTrue(page.transport.loop)
        click(page.toggles["loop"])
        self.assertFalse(page.transport.loop)

    def test_88key_toggle_changes_roll_and_macro_range(self):
        page = self.app.pages["piano"]
        low = make_midi(os.path.join(self.dir, "low.mid"), [(0, 1, 22, 90), (1, 1, 60, 90)])
        page.load_path(low)
        self.assertEqual(len(page.roll.data), 2)
        click(page.toggles["88Keys"])
        self.assertFalse(self.app.cfg.get("piano.88Keys"))
        self.assertEqual(len(page.roll.data), 1)          # note 22 no longer drawn...
        page.on_play(); self.pump(1.4); page.on_stop()
        self.assertNotIn("2", self.presses())             # ...and not pressed (22 -> ctrl+'2')
        click(page.toggles["88Keys"])

    def test_note_lengths_toggle_is_on_by_default_and_reshapes_the_roll(self):
        from tetomidi.rollmodel import SHORT_NOTE
        page = self.app.pages["piano"]
        self.assertTrue(self.app.cfg.get("piano.noteLengths"))
        self.assertTrue(page.toggles["noteLengths"].value)
        page.load_path(self.piano_mid)                               # 0.4 beat at 120 bpm = 0.2 s
        d = page.roll.data
        self.assertAlmostEqual(d.ends[0] - d.starts[0], 0.2, places=2)
        click(page.toggles["noteLengths"])
        self.assertFalse(self.app.cfg.get("piano.noteLengths"))
        d = page.roll.data
        self.assertAlmostEqual(d.ends[0] - d.starts[0], SHORT_NOTE, places=3)
        click(page.toggles["noteLengths"])
        self.assertAlmostEqual(page.roll.data.ends[0] - page.roll.data.starts[0], 0.2, places=2)

    def test_every_mode_has_a_note_lengths_button(self):
        for mode in ("piano", "drums", "guitar"):
            self.assertIn("noteLengths", self.app.pages[mode].toggles)

    def test_toggle_animates_to_its_new_position(self):
        tg = self.app.pages["piano"].toggles["loop"]
        click(tg)
        self.assertTrue(tg.value)
        self.pump(0.5)
        self.assertEqual(tg._t, 1.0)       # finished sliding
        click(tg)
        self.pump(0.5)
        self.assertEqual(tg._t, 0.0)

    def test_window_opacity_setting_is_clamped_and_saved(self):
        self.app.set_opacity(0.6)
        self.assertEqual(self.app.cfg.get("app.opacity"), 0.6)
        self.app.set_opacity(0.05)                                    # never fully invisible
        self.assertEqual(self.app.cfg.get("app.opacity"), 0.4)
        self.app.set_opacity(1.0)
        self.assertEqual(self.app.opacity(), 1.0)

    def test_mini_player_controls_the_active_page_and_returns_to_main(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        self.app.show_mini()
        mini = self.app.mini
        self.pump(0.2)
        self.assertEqual(self.app.root.state(), "withdrawn")
        self.assertEqual(mini.state(), "normal")
        click(mini.play_btn); self.pump(0.3)
        self.assertEqual(page.transport.state, "playing")
        self.assertEqual(mini.play_btn.glyph, "pause")
        # the key console of the mini player shows the presses this instrument sent
        mini.update_view()
        lines = [mini.log.get(i) for i in range(mini.log.size())]
        self.assertTrue(any(line.startswith("press: ") for line in lines), lines[:5])
        click(mini.stop_btn); self.pump(0.2)
        self.assertEqual(page.transport.state, "stopped")
        self.app.hide_mini()
        self.pump(0.2)
        self.assertEqual(self.app.root.state(), "normal")
        self.assertEqual(mini.state(), "withdrawn")

    def test_velocity_and_sustain_toggles_write_config(self):
        page = self.app.pages["piano"]
        click(page.toggles["velocity"]); click(page.toggles["sustain"])
        self.assertTrue(self.app.cfg.get("piano.velocity"))
        self.assertTrue(self.app.cfg.get("piano.sustain"))
        click(page.toggles["velocity"]); click(page.toggles["sustain"])

    # --- instruments --------------------------------------------------
    def test_instrument_pages_press_their_own_keys(self):
        piano, drums = self.app.pages["piano"], self.app.pages["drums"]
        piano.load_path(self.piano_mid)
        drums.load_path(self.drum_mid)
        click(self.app.sidebar.items["drums"])
        click(drums.play_btn)
        self.pump(0.8)
        click(drums.stop_btn)
        keys = set(self.presses())
        self.assertTrue(keys & {"b", "n", "c", "v", "x"}, keys)    # kick / snare / hi-hat macro keys
        self.assertFalse(keys & {"t", "y"})                         # not piano keys
        self.backend.log.clear()
        click(self.app.sidebar.items["piano"])
        click(piano.play_btn)
        self.pump(0.8)
        click(piano.stop_btn)
        self.assertTrue(set(self.presses()) & {"t", "y", "u", "i", "o"})

    def test_leaving_a_page_stops_its_playback(self):
        page = self.app.pages["piano"]
        page.load_path(self.piano_mid)
        click(page.play_btn)
        self.pump(0.3)
        click(self.app.sidebar.items["guitar"])
        self.assertEqual(page.transport.state, "stopped")
        self.assertEqual(self.app.keys.held, set())

    # --- settings -----------------------------------------------------
    def test_settings_keymap_edit_changes_what_is_pressed(self):
        import tkinter as tk
        from tetomidi.ui.settings_page import SettingsPage

        settings = self.app.pages["settings"]
        self.assertIsInstance(settings, SettingsPage)
        entries = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Entry):
                    entries.append(c)
                walk(c)
        walk(settings.mode_frames["guitar"])
        self.assertEqual(len(entries), 36)
        entries[0].delete(0, "end"); entries[0].insert(0, "k")      # note 40 (E2) -> 'k'
        self.assertEqual(self.app.cfg.get("guitar.keymap.40"), "k")
        guitar = self.app.pages["guitar"]
        guitar.load_path(make_midi(os.path.join(self.dir, "g.mid"), [(0, 1, 40, 90)]))
        click(self.app.sidebar.items["guitar"])
        click(guitar.play_btn); self.pump(0.4); click(guitar.stop_btn)
        self.assertIn("k", self.presses())
        self.app.cfg.set("guitar.keymap.40", "1")

    def test_hotkey_rebind_through_capture(self):
        hk = self.app.hotkeys
        self.app.rebind_hotkey("play")
        self.assertTrue(hk.capturing)
        hk._on_press(type("K", (), {"char": None, "name": "f9"})())
        self.pump(0.2)
        self.assertEqual(self.app.cfg.get("hotkeys.play"), "f9")
        self.assertEqual(self.app.pages["piano"]._hot_chips["play"]._text, "f9")
        self.app.rebind_hotkey("play")
        hk._on_press(type("K", (), {"char": None, "name": "f2"})())
        self.pump(0.2)
        self.assertEqual(self.app.cfg.get("hotkeys.play"), "f2")

    def test_narrow_window_uses_horizontal_option_strip(self):
        from tetomidi.ui.gfx import px
        page = self.app.pages["piano"]
        root = self.app.root
        # The app's minsize (780 px) keeps the page at ~694 px, above the 640 px "narrow" threshold, so lower
        # the minimum for this test and restore it afterwards. The compact sidebar leaves the page about
        # (window - 86) px wide, so 700 px is clearly below the threshold on every OS / DPI scale.
        old_min = root.minsize()
        root.minsize(px(500), px(540))
        self.addCleanup(root.minsize, *old_min)
        root.geometry(f"{px(700)}x{px(560)}")
        self.pump(0.5)
        self.assertLess(page.winfo_width(), px(640))
        self.assertTrue(page._narrow)
        root.geometry(f"{px(1060)}x{px(620)}")
        self.pump(0.5)
        self.assertGreaterEqual(page.winfo_width(), px(640))
        self.assertFalse(page._narrow)


if __name__ == "__main__":
    unittest.main()
