"""MIDI Hub client: lists, searches and downloads the songs the bot uploaded (server: ``hub_server/``).

Standard library only, so it adds no dependency to the app.  Every method blocks; the UI calls them from
worker threads.  Downloaded files are cached in ``<config dir>/hub``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, HUB_API_URL, __version__
from .paths import config_dir

log = logging.getLogger(__name__)

TIMEOUT = 15
PAGE_SIZE = 30
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MIDI_BYTES = 4 * 1024 * 1024
MAX_THUMB_BYTES = 2 * 1024 * 1024
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class HubError(Exception):
    """Raised with a message that is safe to show to the user."""


@dataclass(frozen=True)
class HubSong:
    id: int
    name: str
    source_url: str = ""
    thumbnail_url: str = ""
    midi_url: str = ""
    size: int = 0
    uploader: str = ""
    created_at: str = ""


def _text(value, limit: int) -> str:
    value = "" if value is None else str(value)
    value = "".join(ch for ch in value if ch == " " or ord(ch) >= 32 and ch != "\x7f")
    return re.sub(r"\s+", " ", value).strip()[:limit]


def http_link(url: str) -> str:
    """The link when it is a plain http(s) address (the only kind the app ever opens), else ''."""
    url = (url or "").strip()
    parts = urllib.parse.urlparse(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def parse_song(raw: dict, base: str) -> HubSong | None:
    """Song from one API record; None when the record is unusable."""
    try:
        sid = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None
    name = _text(raw.get("name"), 200)
    if not name:
        return None

    def url(key: str) -> str:
        value = raw.get(key)
        return http_link(urllib.parse.urljoin(base + "/", value)) if isinstance(value, str) and value else ""

    try:
        size = max(int(raw.get("size") or 0), 0)
    except (TypeError, ValueError):
        size = 0
    return HubSong(id=sid, name=name, source_url=http_link(_text(raw.get("source_url"), 500)),
                   thumbnail_url=url("thumbnail_url"), midi_url=url("midi_url") or http_link(f"{base}/api/songs/{sid}/midi"),
                   size=size, uploader=_text(raw.get("uploader"), 64), created_at=_text(raw.get("created_at"), 40))


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].strip(" .")
    if not name:
        return "song"
    return "_" + name if name.lower().split(".")[0] in _RESERVED else name


def link_host(url: str) -> str:
    """Short text for a link (``youtube.com``)."""
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def downloads_dir() -> Path:
    """The user's Downloads folder (Windows known folder, XDG on Linux, else ``~/Downloads``)."""
    home = Path.home()
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            guid = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7D))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path.is_dir():
                    return path
        else:
            cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
            match = re.search(r'^XDG_DOWNLOAD_DIR="?([^"\n]+)"?', cfg.read_text(encoding="utf-8"), re.M)
            if match:
                path = Path(match.group(1).replace("$HOME", str(home)))
                if path.is_dir():
                    return path
    except Exception:   # unusual systems: fall back to the usual place
        log.debug("downloads folder lookup failed", exc_info=True)
    return home / "Downloads"


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    path = folder / (stem + suffix)
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stem} ({n}){suffix}"
    return path


def _ssl_context() -> ssl.SSLContext:
    """TLS context for the hub: the system certificates plus the ``certifi`` bundle (when installed).

    Python only sees the roots already present in the system store.  On Windows the browser fetches missing roots
    on demand, Python does not, so a perfectly valid site can fail to verify.  Adding certifi covers that case.
    Certificates are always verified."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        log.debug("certifi bundle unavailable", exc_info=True)
    return ctx


def _network_reason(exc: BaseException) -> str:
    """Short, user-safe reason for a failed request (the raw exception goes to the log)."""
    cause = getattr(exc, "reason", exc)
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "SSL certificate could not be verified"
    if isinstance(cause, ssl.SSLError):
        return "SSL error"
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(cause, socket.gaierror):
        return "server name not found"
    if isinstance(cause, ConnectionRefusedError):
        return "connection refused"
    if isinstance(cause, ConnectionResetError):
        return "connection reset"
    if isinstance(cause, ValueError):
        return "invalid address"
    return "network error"


class HubClient:
    def __init__(self, base_url: str = "", cache_dir: Path | str | None = None):
        self.base = (base_url or "").strip().rstrip("/")
        self.cache = Path(cache_dir) if cache_dir else config_dir() / "hub"

    @classmethod
    def from_config(cls, cfg) -> "HubClient":
        """Address order: environment TETOMIDI_HUB_URL, config ``hub.apiUrl``, the built-in ``HUB_API_URL``."""
        url = os.environ.get("TETOMIDI_HUB_URL") or cfg.get("hub.apiUrl", "") or HUB_API_URL
        return cls(url)

    @property
    def configured(self) -> bool:
        return bool(http_link(self.base))

    # ------------------------------------------------------------ http
    def _get(self, url: str, limit: int) -> bytes:
        if not http_link(url):
            raise HubError("The MIDI Hub address is not valid.")
        req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as resp:
                data = resp.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise HubError("That song is no longer in the hub." if exc.code == 404
                           else f"The MIDI Hub answered with an error ({exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            reason = _network_reason(exc)
            log.warning("MIDI Hub request to %s failed: %r", url, exc)
            raise HubError(f"Could not reach the MIDI Hub ({reason}). Check your internet connection.") from exc
        if len(data) > limit:
            raise HubError("The hub sent a file that is too large.")
        return data

    # ------------------------------------------------------------ api
    def list_songs(self, query: str = "", limit: int = PAGE_SIZE, offset: int = 0) -> tuple[list[HubSong], int]:
        """(songs, total matching).  ``query`` is searched in the song names."""
        if not self.configured:
            raise HubError("The MIDI Hub server address is not set.")
        params = {"limit": int(limit), "offset": int(offset)}
        if query.strip():
            params["q"] = query.strip()
        data = self._get(f"{self.base}/api/songs?{urllib.parse.urlencode(params)}", MAX_JSON_BYTES)
        try:
            body = json.loads(data.decode("utf-8"))
            records = body["songs"]
            total = int(body.get("total", len(records)))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HubError("The MIDI Hub sent an answer the app does not understand.") from exc
        songs = [s for s in (parse_song(r, self.base) for r in records if isinstance(r, dict)) if s]
        return songs, max(total, len(songs))

    def thumbnail(self, song: HubSong) -> bytes:
        """The thumbnail image bytes (cached on disk)."""
        if not song.thumbnail_url:
            raise HubError("This song has no thumbnail.")
        path = self.cache / "thumbs" / f"{song.id}.img"
        if path.is_file():
            return path.read_bytes()
        data = self._get(song.thumbnail_url, MAX_THUMB_BYTES)
        self._store(path, data)   # a failed cache write does not matter here: the bytes are in hand
        return data

    def midi_path(self, song: HubSong) -> Path:
        """Local copy of the song's .mid file (downloaded once, then cached)."""
        path = self.cache / "midi" / f"{safe_filename(song.name)}-{song.id}.mid"
        if path.is_file() and path.stat().st_size:
            return path
        data = self._get(song.midi_url, MAX_MIDI_BYTES)
        if not data.startswith(b"MThd"):
            raise HubError("The hub sent a file that is not a MIDI file.")
        if not self._store(path, data):
            raise HubError("Could not write to the app's cache folder.")
        return path

    def download(self, song: HubSong, folder: Path | str | None = None) -> Path:
        """Save the song as a .mid file in the Downloads folder (never overwrites); returns the new path."""
        source = self.midi_path(song)
        folder = Path(folder) if folder else downloads_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = unique_path(folder, safe_filename(song.name), ".mid")
            shutil.copyfile(source, target)
        except OSError as exc:
            raise HubError(f"Could not save the file in {folder}.") from exc
        return target

    @staticmethod
    def _store(path: Path, data: bytes) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            return True
        except OSError:
            log.warning("could not cache %s", path, exc_info=True)
            return False
