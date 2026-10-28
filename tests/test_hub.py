"""MIDI Hub: the client module (always) and the API server (when Flask is installed).  No display needed."""

import io
import os
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from tetomidi.hub import (HubClient, HubError, PAGE_SIZE, http_link, link_host, parse_song, safe_filename,
                          unique_path)

try:
    import flask  # noqa: F401
    from werkzeug.serving import make_server
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False

# a tiny but valid MIDI file: one note
MIDI = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
        b"MTrk\x00\x00\x00\x0d\x00\x90\x3c\x40\x83\x60\x80\x3c\x00\x00\xff\x2f\x00")


def png(w=1200, h=800, color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


class HelperTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('a<b>:"c/d\\e|f?g*'), "abcdefg")
        self.assertEqual(safe_filename("  song" + "." * 3 + "  "), "song")
        self.assertEqual(safe_filename(""), "song")
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("Bản tình ca"), "Bản tình ca")
        self.assertLessEqual(len(safe_filename("x" * 300)), 80)

    def test_links(self):
        self.assertEqual(http_link("https://youtu.be/abc"), "https://youtu.be/abc")
        self.assertEqual(http_link("javascript:alert(1)"), "")
        self.assertEqual(http_link("file:///etc/passwd"), "")
        self.assertEqual(http_link(""), "")
        self.assertEqual(link_host("https://www.youtube.com/watch?v=1"), "youtube.com")

    def test_unique_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            first = unique_path(Path(d), "song", ".mid")
            first.write_bytes(b"x")
            second = unique_path(Path(d), "song", ".mid")
            self.assertEqual((first.name, second.name), ("song.mid", "song (2).mid"))

    def test_parse_song(self):
        base = "https://hub.example.com"
        song = parse_song({"id": 3, "name": "  Hello \n world ", "source_url": "https://youtu.be/x",
                           "thumbnail_url": "/api/songs/3/thumbnail", "size": 99, "uploader": "me"}, base)
        self.assertEqual((song.id, song.name, song.size), (3, "Hello world", 99))
        self.assertEqual(song.thumbnail_url, "https://hub.example.com/api/songs/3/thumbnail")
        self.assertEqual(song.midi_url, "https://hub.example.com/api/songs/3/midi")
        self.assertIsNone(parse_song({"name": "no id"}, base))
        self.assertIsNone(parse_song({"id": 1, "name": "   "}, base))
        bad = parse_song({"id": 4, "name": "x", "source_url": "javascript:alert(1)"}, base)
        self.assertEqual(bad.source_url, "")

    def test_client_without_address(self):
        client = HubClient("", cache_dir=tempfile.mkdtemp())
        self.assertFalse(client.configured)
        with self.assertRaises(HubError):
            client.list_songs()

    def test_address_order(self):
        class Cfg:
            def __init__(self, url):
                self.url = url

            def get(self, path, default=None):
                return self.url

        old = os.environ.pop("TETOMIDI_HUB_URL", None)
        try:
            self.assertEqual(HubClient.from_config(Cfg("https://a.example/")).base, "https://a.example")
            os.environ["TETOMIDI_HUB_URL"] = "https://env.example"
            self.assertEqual(HubClient.from_config(Cfg("https://a.example")).base, "https://env.example")
        finally:
            os.environ.pop("TETOMIDI_HUB_URL", None)
            if old is not None:
                os.environ["TETOMIDI_HUB_URL"] = old

    def test_unreachable_server_gives_a_friendly_error(self):
        client = HubClient("http://127.0.0.1:9", cache_dir=tempfile.mkdtemp())   # nothing listens on port 9
        with self.assertRaises(HubError) as ctx:
            client.list_songs()
        self.assertIn("Could not reach", str(ctx.exception))


@unittest.skipUnless(HAVE_FLASK, "needs Flask (pip install -r hub_server/requirements.txt)")
class ServerTests(unittest.TestCase):
    def setUp(self):
        from hub_server.app import create_app
        self.data = tempfile.mkdtemp(prefix="hub-data-")
        self.app = create_app({"HUB_API_KEY": "secret", "HUB_DATA_DIR": self.data, "TESTING": True})
        self.http = self.app.test_client()
        self.auth = {"Authorization": "Bearer secret"}

    def upload(self, name="Song", midi=MIDI, link="https://youtu.be/abc", thumb=True, headers=None, **extra):
        data = {"name": name, "source_url": link, "midi": (io.BytesIO(midi), "x.mid"), **extra}
        if thumb:
            data["thumbnail"] = (io.BytesIO(png()), "t.png")
        return self.http.post("/api/songs", data=data, headers=self.auth if headers is None else headers,
                              content_type="multipart/form-data")

    def test_health(self):
        self.assertEqual(self.http.get("/api/health").get_json(), {"ok": True, "songs": 0})

    def test_upload_needs_the_key(self):
        self.assertEqual(self.upload(headers={}).status_code, 401)
        self.assertEqual(self.upload(headers={"Authorization": "Bearer nope"}).status_code, 401)
        from hub_server.app import create_app
        keyless = create_app({"HUB_API_KEY": "", "HUB_DATA_DIR": tempfile.mkdtemp()}).test_client()
        resp = keyless.post("/api/songs", data={"name": "x", "midi": (io.BytesIO(MIDI), "x.mid")},
                            headers=self.auth, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 503)

    def test_upload_and_read_back(self):
        resp = self.upload(name="My song", uploader="teto#1")
        self.assertEqual(resp.status_code, 201)
        song = resp.get_json()
        self.assertEqual((song["name"], song["source_url"], song["uploader"]), ("My song", "https://youtu.be/abc", "teto#1"))
        self.assertEqual(song["thumbnail_url"], f"/api/songs/{song['id']}/thumbnail")
        self.assertEqual(self.http.get(song["midi_url"]).data, MIDI)
        thumb = self.http.get(song["thumbnail_url"])
        self.assertEqual(thumb.mimetype, "image/jpeg")
        with Image.open(io.BytesIO(thumb.data)) as img:
            self.assertLessEqual(max(img.size), 640)
        self.assertEqual(self.http.get(f"/api/songs/{song['id']}").get_json()["name"], "My song")

    def test_bad_uploads_are_refused(self):
        self.assertEqual(self.upload(midi=b"not midi").status_code, 400)
        self.assertEqual(self.upload(link="javascript:alert(1)").status_code, 400)
        self.assertEqual(self.upload(name="  ").status_code, 400)
        bad_thumb = self.http.post("/api/songs", headers=self.auth, content_type="multipart/form-data",
                                   data={"name": "x", "midi": (io.BytesIO(MIDI), "x.mid"),
                                         "thumbnail": (io.BytesIO(b"nope"), "t.png")})
        self.assertEqual(bad_thumb.status_code, 400)
        self.assertEqual(self.http.get("/api/health").get_json()["songs"], 0)

    def test_duplicate_midi_is_refused_with_the_existing_id(self):
        first = self.upload(name="One").get_json()
        resp = self.upload(name="Two")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["id"], first["id"])

    def test_thumbnail_and_link_are_optional(self):
        resp = self.upload(thumb=False, link="")
        self.assertEqual(resp.status_code, 201)
        song = resp.get_json()
        self.assertIsNone(song["thumbnail_url"])
        self.assertEqual(self.http.get(f"/api/songs/{song['id']}/thumbnail").status_code, 404)

    def test_search_ignores_case_and_accents(self):
        self.upload(name="Bản tình ca", midi=MIDI + b"\x01")
        self.upload(name="Đêm trăng", midi=MIDI + b"\x02")
        self.upload(name="Other 100% song_x", midi=MIDI + b"\x03")

        def names(q):
            return [s["name"] for s in self.http.get("/api/songs", query_string={"q": q}).get_json()["songs"]]

        self.assertEqual(names("ban TINH"), ["Bản tình ca"])
        self.assertEqual(names("dem"), ["Đêm trăng"])
        self.assertEqual(names("100%"), ["Other 100% song_x"])   # % and _ are plain characters
        self.assertEqual(names("%"), ["Other 100% song_x"])
        self.assertEqual(names("zzz"), [])
        self.assertEqual(len(names("")), 3)

    def test_list_is_newest_first_and_paged(self):
        for i in range(5):
            self.upload(name=f"Song {i}", midi=MIDI + bytes([i]))
        body = self.http.get("/api/songs", query_string={"limit": 2, "offset": 1}).get_json()
        self.assertEqual((body["total"], [s["name"] for s in body["songs"]]), (5, ["Song 3", "Song 2"]))
        self.assertEqual(self.http.get("/api/songs", query_string={"limit": "x"}).status_code, 400)

    def test_delete_needs_the_key_and_removes_the_files(self):
        song = self.upload().get_json()
        self.assertEqual(self.http.delete(f"/api/songs/{song['id']}").status_code, 401)
        self.assertEqual(self.http.delete(f"/api/songs/{song['id']}", headers=self.auth).status_code, 200)
        self.assertEqual(self.http.get(f"/api/songs/{song['id']}").status_code, 404)
        self.assertEqual(list(Path(self.data, "midi").iterdir()) + list(Path(self.data, "thumbs").iterdir()), [])

    def test_reads_are_open_to_other_sites(self):
        self.assertEqual(self.http.get("/api/health").headers["Access-Control-Allow-Origin"], "*")


@unittest.skipUnless(HAVE_FLASK, "needs Flask (pip install -r hub_server/requirements.txt)")
class ClientAgainstServerTests(unittest.TestCase):
    """The app's client talking to the real server over HTTP."""

    @classmethod
    def setUpClass(cls):
        from hub_server.app import create_app
        cls.app = create_app({"HUB_API_KEY": "secret", "HUB_DATA_DIR": tempfile.mkdtemp(), "TESTING": True})
        cls.server = make_server("127.0.0.1", 0, cls.app)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        http = cls.app.test_client()
        for name, extra in (("Bản tình ca", b"\x01"), ("Second song", b"\x02")):
            http.post("/api/songs", headers={"Authorization": "Bearer secret"}, content_type="multipart/form-data",
                      data={"name": name, "source_url": "https://youtu.be/abc", "midi": (io.BytesIO(MIDI + extra), "x.mid"),
                            "thumbnail": (io.BytesIO(png()), "t.png")})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.client = HubClient(self.url, cache_dir=tempfile.mkdtemp(prefix="hub-cache-"))

    def test_list_and_search(self):
        songs, total = self.client.list_songs()
        self.assertEqual((total, [s.name for s in songs]), (2, ["Second song", "Bản tình ca"]))
        self.assertTrue(songs[0].thumbnail_url.startswith(self.url))
        self.assertEqual(songs[0].source_url, "https://youtu.be/abc")
        found, total = self.client.list_songs("ban")
        self.assertEqual([s.name for s in found], ["Bản tình ca"])
        self.assertLessEqual(len(songs), PAGE_SIZE)

    def test_thumbnail_is_cached(self):
        song = self.client.list_songs()[0][0]
        data = self.client.thumbnail(song)
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.format, "JPEG")
        self.assertTrue((self.client.cache / "thumbs" / f"{song.id}.img").is_file())

    def test_midi_is_downloaded_once_and_downloads_never_overwrite(self):
        song = self.client.list_songs()[0][0]
        path = self.client.midi_path(song)
        self.assertEqual(path.read_bytes(), MIDI + b"\x02")
        self.assertEqual(self.client.midi_path(song), path)
        out = Path(tempfile.mkdtemp(prefix="hub-downloads-"))
        a, b = self.client.download(song, out), self.client.download(song, out)
        self.assertEqual((a.name, b.name), ("Second song.mid", "Second song (2).mid"))
        self.assertEqual(a.read_bytes(), MIDI + b"\x02")

    def test_missing_song_gives_a_friendly_error(self):
        http, auth = self.app.test_client(), {"Authorization": "Bearer secret"}
        http.post("/api/songs", headers=auth, content_type="multipart/form-data",   # its own song: the others stay
                  data={"name": "Short lived", "midi": (io.BytesIO(MIDI + b"\x09"), "x.mid")})
        song = self.client.list_songs("short lived")[0][0]
        http.delete(f"/api/songs/{song.id}", headers=auth)
        with self.assertRaises(HubError) as ctx:
            self.client.midi_path(song)
        self.assertIn("no longer", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
