import os
import unittest
from unittest import mock

from tetomidi import __version__, VERSION_LABEL, platform_tweaks


class PlatformTests(unittest.TestCase):
    def test_version_matches_label(self):
        self.assertEqual(VERSION_LABEL, "v" + ".".join(__version__.split(".")[:2]))

    def test_session_type(self):
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            self.assertEqual(platform_tweaks.session_type(), "wayland")
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertEqual(platform_tweaks.session_type(), "x11")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(platform_tweaks.session_type(), "")

    def test_frameless_failure_is_not_fatal(self):
        class Broken:
            def overrideredirect(self, *_):
                raise RuntimeError("no")

            def update_idletasks(self):
                raise RuntimeError("no")

        self.assertFalse(platform_tweaks.make_frameless(Broken()))


if __name__ == "__main__":
    unittest.main()
