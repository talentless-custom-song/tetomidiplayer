"""MIDI HUB page: the songs the Discord bot uploaded (server: ``hub_server/``).

List with thumbnail, name and a link to the original song, a search box (magnifier) on top.  Choosing a song opens
a preview: the piano roll plays it together with a piano sound (the bundled Piano.sf2 SoundFont, or the built-in
synthesised piano when that is off; on the speakers, whatever the cable settings are), and the song can be
downloaded into the Downloads folder or opened in the Piano player.

The preview presses no real keys: its own dispatcher sends the notes straight to the piano sound.
Network work runs on worker threads; their results are handed back to the Tk thread through a queue.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from ..engine.transport import PAUSED, PLAYING, Transport
from ..hub import PAGE_SIZE, HubClient, HubError, HubSong, link_host
from ..midi_data import MidiLoadError, Song, format_time, load_song
from ..pianosound import PianoPreviewDispatcher, PianoSound
from ..rollmodel import build_roll
from . import gfx, theme
from .gfx import px
from .kit import Badge, IconButton, Label, PillButton, ScrollFrame, Slider, Tooltip
from .player_page import PlayerPage, _FitLabel
from .roll import RollView

log = logging.getLogger(__name__)

ROW_BG, ROW_HOVER = "#171516", "#242223"
FIELD_BG, FIELD_BORDER = "#161516", "#3a3738"
THUMB_W, THUMB_H, THUMB_R = 112, 63, 8      # thumbnails are 16:9 (px at 100% scaling)
NOT_SET_UP = ("The MIDI Hub server is not set up yet.\n"
              "Put its address in HUB_API_URL (tetomidi/__init__.py) or in config.json as hub.apiUrl.")


class SearchBox(tk.Canvas):
    """Pill with a magnifier and a text field.  ``on_search(text)`` runs shortly after typing and on Enter."""

    HINT = "Search songs"
    DELAY_MS = 350
    _IGNORED = {"Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                "Left", "Right", "Up", "Down", "Home", "End"}

    def __init__(self, parent, width, height, on_search, bg=theme.MAIN_BG):
        # NB: never name these ``_w``: tkinter stores the widget path name there.
        self._pw, self._ph = px(width), px(height)
        super().__init__(parent, width=self._pw, height=self._ph, bg=bg, highlightthickness=0, bd=0)
        self.on_search = on_search
        self._job, self._focused, self._hinting, self._sent = None, False, False, ""
        self._refs: list = []
        self.entry = tk.Entry(self, bd=0, highlightthickness=0, relief="flat", bg=FIELD_BG, fg=theme.TEXT,
                              insertbackground=theme.TEXT, selectbackground=theme.ACCENT, selectforeground="#ffffff",
                              font=("TkDefaultFont", 12))
        self._win = self.create_window(px(46), self._ph // 2, window=self.entry, anchor="w", width=self._field_width())
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<KeyRelease>", self._typed)
        self.entry.bind("<<Paste>>", lambda e: self.after_idle(self._typed))
        self.entry.bind("<<Cut>>", lambda e: self.after_idle(self._typed))
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda e: self.clear())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._show_hint()
        self._draw()

    def _field_width(self) -> int:
        return max(self._pw - px(46) - px(16), px(20))

    def _draw(self):
        self.delete("bg")
        self._refs.clear()
        border = theme.ACCENT if self._focused else FIELD_BORDER
        back = gfx.photo(gfx.pill_image(self._pw, self._ph, self._ph // 2, FIELD_BG, border))
        icon = gfx.photo(gfx.control_icon("search", px(20), theme.TEXT_MUTED))
        self._refs += [back, icon]
        self.create_image(0, 0, image=back, anchor="nw", tags="bg")
        self.create_image(px(16), self._ph // 2, image=icon, anchor="w", tags="bg")
        self.tag_lower("bg")

    def set_pixel_width(self, width: int):
        if width != self._pw:
            self._pw = width
            self.configure(width=width)
            self.itemconfigure(self._win, width=self._field_width())
            self._draw()

    # ------------------------------------------------------------- text
    def text(self) -> str:
        return "" if self._hinting else self.entry.get()

    def _show_hint(self):
        if not self._hinting and not self.entry.get() and not self._focused:
            self._hinting = True
            self.entry.insert(0, self.HINT)
            self.entry.configure(fg=theme.TEXT_FAINT)

    def _hide_hint(self):
        if self._hinting:
            self._hinting = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=theme.TEXT)

    def _focus_in(self, _e):
        self._focused = True
        self._hide_hint()
        self._draw()

    def _focus_out(self, _e):
        self._focused = False
        self._show_hint()
        self._draw()

    def clear(self):
        self._hide_hint()
        self.entry.delete(0, "end")
        self._show_hint()
        self._fire(force=False)

    # ----------------------------------------------------------- events
    def _typed(self, event=None):
        if self._hinting or (event is not None and event.keysym in self._IGNORED):
            return
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(self.DELAY_MS, self._fire)

    def _fire(self, force: bool = False):
        self._job = None
        text = self.text().strip()
        if force or text != self._sent:
            self._sent = text
            self.on_search(text)

    def _submit(self, _e):
        if self._job is not None:
            self.after_cancel(self._job)
        self._fire(force=True)


class HubPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.MAIN_BG)
        self.app, self.cfg = app, app.cfg
        self.client = HubClient.from_config(self.cfg)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tetomidi-hub")
        self._results: queue.Queue = queue.Queue()
        self._alive = True
        self._gen = 0                      # newest list request; answers of older ones are dropped
        self.songs: list[HubSong] = []
        self.total = 0
        self.query = ""
        self._loaded, self._loading, self._rendered = False, False, 0
        self._thumbs: dict[int, object] = {}   # song id -> PhotoImage, or False when it could not be loaded
        self._thumb_wait: set[int] = set()
        self._placeholder = gfx.photo(gfx.placeholder_image(px(THUMB_W), px(THUMB_H), px(THUMB_R)))
        self._last: dict = {}
        # detail / preview state
        self.current: HubSong | None = None
        self.song: Song | None = None
        self.local_path = None
        self.preview_sound = PianoSound(self.cfg)
        self.transport = Transport(PianoPreviewDispatcher(self.preview_sound, self.cfg), self.cfg)
        self._sound_open = False

        self._build()
        self.cfg.on_change(self._on_cfg_change)
        self.bind("<Configure>", self._on_resize)
        self.after(40, self._drain)

    # ------------------------------------------------------------ build
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        pad = px(24)

        head = tk.Frame(self, bg=theme.MAIN_BG)
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(px(26), px(14)))
        self.heading = Label(head, "MIDI HUB", size=24, color=theme.TEXT, bg=theme.MAIN_BG)
        self.heading.pack(side="left", padx=(px(100), px(18)))
        self.search = SearchBox(head, 300, 40, self._on_search)
        self.search.pack(side="left")
        Tooltip(self.search, "Search songs by name")
        refresh = IconButton(head, "reset", 26, command=self.refresh, tip="Reload the list", bg=theme.MAIN_BG)
        refresh.pack(side="left", padx=(px(14), 0))
        self.status = _FitLabel(head, size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.status.pack(side="left", fill="x", expand=True, padx=(px(14), 0))

        stack = tk.Frame(self, bg=theme.MAIN_BG)
        stack.grid(row=1, column=0, sticky="nsew", padx=pad)
        stack.grid_columnconfigure(0, weight=1)
        stack.grid_rowconfigure(0, weight=1)
        self.list_view = tk.Frame(stack, bg=theme.MAIN_BG)
        self.detail = tk.Frame(stack, bg=theme.MAIN_BG)
        for view in (self.list_view, self.detail):
            view.grid(row=0, column=0, sticky="nsew")
        self._build_list()
        self._build_detail()
        self.list_view.tkraise()

    def _build_list(self):
        self.list_view.grid_columnconfigure(0, weight=1)
        self.list_view.grid_rowconfigure(0, weight=1)
        self.scroll = ScrollFrame(self.list_view)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.rows = self.scroll.inner
        self.footer = tk.Frame(self.rows, bg=theme.MAIN_BG)
        self.more_btn = PillButton(self.footer, "Load more", 150, 38, size=16, radius=19, command=self._load_more)
        self.more_btn.pack(pady=px(10))
        self.notice = tk.Frame(self.list_view, bg=theme.MAIN_BG)
        self.notice_text = tk.Label(self.notice, text="", bg=theme.MAIN_BG, fg=theme.TEXT_MUTED, justify="center",
                                    font=("TkDefaultFont", 13), wraplength=px(480))
        self.notice_text.pack()
        self.retry_btn = PillButton(self.notice, "Try again", 130, 38, size=16, radius=19, command=self.refresh)

    def _build_detail(self):
        d = self.detail
        d.grid_columnconfigure(0, weight=1)
        d.grid_rowconfigure(1, weight=1)
        top = tk.Frame(d, bg=theme.MAIN_BG)
        top.grid(row=0, column=0, sticky="ew", pady=(0, px(10)))
        PillButton(top, "Back", 96, 36, size=16, radius=18, icon="back", icon_size=18,
                   command=self.show_list).pack(side="left")
        self.d_thumb = tk.Label(top, bg=theme.MAIN_BG, bd=0, image=self._placeholder)
        self.d_thumb.pack(side="left", padx=(px(16), px(14)))
        info = tk.Frame(top, bg=theme.MAIN_BG)
        info.pack(side="left", fill="x", expand=True)
        self.d_title = tk.Label(info, text="", bg=theme.MAIN_BG, fg=theme.TEXT, anchor="w", justify="left",
                                font=("TkDefaultFont", 15, "bold"), wraplength=px(560))
        self.d_title.pack(fill="x")
        self.d_link = tk.Label(info, text="", bg=theme.MAIN_BG, fg=theme.ACCENT, anchor="w",
                               font=("TkDefaultFont", 11, "underline"))
        self.d_link.pack(anchor="w")
        self.d_link.bind("<Button-1>", lambda e: self._open_source())

        self.roll = RollView(d, "piano", self.cfg)
        self.roll.grid(row=1, column=0, sticky="nsew", pady=(0, px(10)))

        bar = tk.Frame(d, bg=theme.MAIN_BG)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(2, weight=1)
        self.play_btn = PillButton(bar, "PLAY", 87, 40, bg=theme.PLAY, hover=theme.PLAY_HOVER, radius=8, size=17,
                                   command=self.on_play)
        self.play_btn.grid(row=0, column=0, padx=(0, px(14)))
        self.stop_btn = PillButton(bar, "STOP", 87, 40, bg=theme.STOP, hover=theme.STOP_HOVER, radius=8, size=17,
                                   command=self.on_stop)
        self.stop_btn.grid(row=0, column=1, padx=(0, px(18)))
        self.slider = Slider(bar, on_drag=self._on_slider_drag, on_commit=self._on_slider_commit)
        self.slider.grid(row=0, column=2, sticky="ew")
        self.time = Badge(bar, "00:00:00/00:00:00", width=190, height=28, size=16)
        self.time.grid(row=0, column=3, padx=(px(14), 0))

        acts = tk.Frame(d, bg=theme.MAIN_BG)
        acts.grid(row=3, column=0, sticky="ew", pady=(px(12), px(18)))
        self.dl_btn = PillButton(acts, "Download", 150, 40, size=17, icon="download", icon_size=20,
                                 command=self.download)
        self.dl_btn.pack(side="left")
        Tooltip(self.dl_btn, "Save the .mid file in your Downloads folder")
        self.use_btn = PillButton(acts, "Open in Piano", 164, 40, size=17, command=self.open_in_piano)
        self.use_btn.pack(side="left", padx=(px(12), 0))
        Tooltip(self.use_btn, "Load the song into the Piano player (then play it with your hotkeys)")
        self.d_status = _FitLabel(acts, size=15, color=theme.TEXT_MUTED, bg=theme.MAIN_BG)
        self.d_status.pack(side="left", fill="x", expand=True, padx=(px(16), 0))
        self._set_actions(False)

    def _on_resize(self, e):
        self.heading.pack_configure(padx=(px(100) if e.width > px(700) else 0, px(18)))
        self.search.set_pixel_width(max(px(180), min(px(360), e.width - px(430))))
        self.d_title.configure(wraplength=max(px(200), e.width - px(300)))

    # ------------------------------------------------------ worker threads
    def _run(self, work, done, fail=None):
        """Run ``work()`` on a worker thread; ``done(result)`` or ``fail(message)`` then run on the Tk thread."""
        def job():
            try:
                result = work()
            except (HubError, MidiLoadError) as exc:
                msg = str(exc)
                self._results.put(lambda: fail(msg) if fail else None)
                return
            except Exception as exc:   # anything unexpected must not kill the worker silently
                log.exception("hub job failed")
                msg = f"Something went wrong ({exc.__class__.__name__})."
                self._results.put(lambda: fail(msg) if fail else None)
                return
            self._results.put(lambda: done(result))

        try:
            self.pool.submit(job)
        except RuntimeError:   # the pool was shut down together with the app
            pass

    def _drain(self):
        while True:
            try:
                fn = self._results.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except tk.TclError:   # a widget went away while its answer was on the way
                pass
            except Exception:
                log.exception("hub callback failed")
        if self._alive:
            self.after(40, self._drain)

    # ------------------------------------------------------------ the list
    def on_show(self):
        if not self._loaded and not self._loading:
            self._load(reset=True)

    def refresh(self):
        self._thumbs = {k: v for k, v in self._thumbs.items() if v}   # try the failed thumbnails again
        self.client = HubClient.from_config(self.cfg)
        self._load(reset=True)

    def _on_search(self, text: str):
        self.query = text
        if self.current is not None:
            self.show_list()
        self._load(reset=True)

    def _load_more(self):
        if not self._loading and len(self.songs) < self.total:
            self._load(reset=False)

    def _load(self, reset: bool):
        if not self.client.configured:
            self._clear_rows()
            self._show_notice(NOT_SET_UP, retry=False)
            self.status.set("")
            return
        self._gen += 1
        gen, query = self._gen, self.query
        offset = 0 if reset else len(self.songs)
        self._loading = True
        self.status.set("Searching" if query else "Loading", theme.TEXT_MUTED)
        self._run(lambda: self.client.list_songs(query, PAGE_SIZE, offset),
                  lambda res: self._on_loaded(gen, reset, res),
                  lambda msg: self._on_load_failed(gen, reset, msg))

    def _on_loaded(self, gen: int, reset: bool, result):
        if gen != self._gen:
            return
        songs, total = result
        self._loading, self._loaded = False, True
        if reset:
            self.songs = []
            self._clear_rows()
        self.songs += songs
        self.total = total
        self._hide_notice()
        self._render_new_rows()
        if not self.songs:
            self._show_notice(f"No songs found for \"{self.query}\"." if self.query else "The hub has no songs yet.", retry=False)
            self.status.set("")
        else:
            self.status.set(f"{len(self.songs)} of {self.total} songs" if self.total > len(self.songs)
                            else f"{self.total} song{'s' if self.total != 1 else ''}", theme.TEXT_MUTED)

    def _on_load_failed(self, gen: int, reset: bool, msg: str):
        if gen != self._gen:
            return
        self._loading = False
        self.status.set("")
        if reset:
            self.songs = []
            self._clear_rows()
            self._show_notice(msg, retry=True)
        else:
            self.status.set(msg, theme.ERROR)
            self._render_new_rows()

    def _show_notice(self, text: str, retry: bool):
        self.notice_text.configure(text=text)
        self.retry_btn.pack_forget()
        if retry:
            self.retry_btn.pack(pady=(px(14), 0))
        self.notice.place(relx=0.5, rely=0.4, anchor="center")
        self.notice.lift()

    def _hide_notice(self):
        self.notice.place_forget()

    def _clear_rows(self):
        self.footer.pack_forget()
        for child in self.rows.winfo_children():
            if child is not self.footer:
                child.destroy()
        self._rendered = 0

    def _render_new_rows(self):
        self.footer.pack_forget()
        for song in self.songs[self._rendered:]:
            self._make_row(song)
        self._rendered = len(self.songs)
        if self.total > len(self.songs):
            self.footer.pack(fill="x")
        self.scroll._sync()

    def _make_row(self, song: HubSong):
        row = tk.Frame(self.rows, bg=ROW_BG, cursor="hand2")
        row.pack(fill="x", pady=(0, px(8)))
        row.grid_columnconfigure(1, weight=1)
        thumb = tk.Label(row, bg=ROW_BG, bd=0, image=self._thumbs.get(song.id) or self._placeholder)
        thumb.grid(row=0, column=0, padx=px(10), pady=px(10))
        info = tk.Frame(row, bg=ROW_BG)
        info.grid(row=0, column=1, sticky="ew", padx=(0, px(14)))
        title = tk.Label(info, text=song.name, bg=ROW_BG, fg=theme.TEXT, anchor="w", justify="left",
                         font=("TkDefaultFont", 13, "bold"), wraplength=px(500))
        title.pack(fill="x")
        link = tk.Label(info, bg=ROW_BG, anchor="w")
        if song.source_url:
            link.configure(text=link_host(song.source_url), fg=theme.ACCENT, cursor="hand2",
                           font=("TkDefaultFont", 11, "underline"))
            link.bind("<Button-1>", lambda e, url=song.source_url: (webbrowser.open_new_tab(url), "break")[1])
        else:
            link.configure(text="No original link", fg=theme.TEXT_FAINT, font=("TkDefaultFont", 11))
        link.pack(anchor="w")
        meta = " · ".join(p for p in (song.uploader, f"{song.size / 1024:.0f} KB" if song.size else "") if p)
        meta_label = tk.Label(info, text=meta, bg=ROW_BG, fg=theme.TEXT_FAINT, anchor="w", font=("TkDefaultFont", 10))
        if meta:
            meta_label.pack(anchor="w")
        everything = (row, thumb, info, title, link, meta_label)
        for w in everything:
            w.bind("<Enter>", lambda e, r=row, ws=everything: self._hover(r, ws, True), add="+")
            w.bind("<Leave>", lambda e, r=row, ws=everything: self._hover(r, ws, False), add="+")
        for w in (row, thumb, info, title, meta_label):   # the link opens the browser instead
            w.bind("<Button-1>", lambda e, s=song: self.open_song(s))
        row.bind("<Configure>", lambda e, t=title: t.configure(wraplength=max(px(160), e.width - px(THUMB_W) - px(60))))
        self.scroll.bind_wheel_recursive(row)
        self._ensure_thumb(song, thumb)

    @staticmethod
    def _hover(row, widgets, on: bool):
        if not on:   # moving between the row's own children must not flicker
            x, y = row.winfo_pointerxy()
            under = row.winfo_containing(x, y)
            if under is not None and str(under).startswith(str(row)):
                return
        color = ROW_HOVER if on else ROW_BG
        for w in widgets:
            try:
                w.configure(bg=color)
            except tk.TclError:
                pass

    # ---------------------------------------------------------- thumbnails
    def _ensure_thumb(self, song: HubSong, label: tk.Label):
        """Put the song's thumbnail on ``label`` (a placeholder shows until it is downloaded)."""
        have = self._thumbs.get(song.id)
        if have:
            label.configure(image=have)
            return
        if have is False or not song.thumbnail_url:
            return
        self._thumb_wait.add(song.id)
        w, h, r = px(THUMB_W), px(THUMB_H), px(THUMB_R)

        def work():
            return gfx.cover_image(self.client.thumbnail(song), w, h, r)

        def done(img):
            self._thumb_wait.discard(song.id)
            self._thumbs[song.id] = gfx.photo(img)
            self._apply_thumb(song, label)

        def fail(_msg):
            self._thumb_wait.discard(song.id)
            self._thumbs[song.id] = False

        self._run(work, done, fail)

    def _apply_thumb(self, song: HubSong, label: tk.Label):
        ph = self._thumbs.get(song.id)
        if ph:
            try:
                label.configure(image=ph)
            except tk.TclError:   # the row was rebuilt in the meantime
                pass
            if self.current is not None and self.current.id == song.id:
                self.d_thumb.configure(image=ph)

    # ------------------------------------------------------------- detail
    @property
    def detail_open(self) -> bool:
        return self.current is not None

    def open_song(self, song: HubSong):
        self._stop_preview()
        self.current, self.song, self.local_path = song, None, None
        self.d_title.configure(text=song.name)
        if song.source_url:
            self.d_link.configure(text=f"Original song: {link_host(song.source_url)}", fg=theme.ACCENT, cursor="hand2")
        else:
            self.d_link.configure(text="No original link", fg=theme.TEXT_FAINT, cursor="arrow")
        self.d_thumb.configure(image=self._thumbs.get(song.id) or self._placeholder)
        if song.id not in self._thumbs:
            self._ensure_thumb(song, self.d_thumb)
        self.roll.set_data(build_roll("piano", None, self.cfg.data["piano"]))
        self.transport.load(None, 0.0)
        self._last.clear()
        self._set_actions(False)
        self._set_status("Loading the song", theme.TEXT_MUTED)
        self.detail.tkraise()

        def work():
            path = self.client.midi_path(song)
            return path, load_song(str(path))

        self._run(work, lambda res: self._on_song_ready(song, *res), lambda msg: self._on_song_failed(song, msg))

    def _on_song_ready(self, song: HubSong, path, loaded: Song):
        if self.current is not song:   # the user already went elsewhere
            return
        self.local_path, self.song = path, loaded
        self.transport.load(loaded.view("piano", bool(self.cfg.get("piano.skipPercussion", True))), loaded.length)
        self.rebuild_roll()
        self._set_actions(True)
        notes = len(self.transport.view.notes) if self.transport.view else 0
        self._set_status(f"{format_time(loaded.length)[3:]}  ·  {notes} notes", theme.TEXT_MUTED)

    def _on_song_failed(self, song: HubSong, msg: str):
        if self.current is song:
            self._set_status(f"⚠ {msg}", theme.ERROR)

    def show_list(self):
        self._stop_preview()
        self._close_sound()
        self.current, self.song = None, None
        self.list_view.tkraise()

    def rebuild_roll(self):
        view = None
        if self.song:
            view = self.song.view("piano", bool(self.cfg.get("piano.skipPercussion", True)))
        self.roll.set_data(build_roll("piano", view, self.cfg.data["piano"]))

    def _set_actions(self, enabled: bool):
        for btn in (self.play_btn, self.stop_btn, self.dl_btn, self.use_btn):
            btn.set_enabled(enabled)
        self.slider.set_enabled(enabled)

    def _set_status(self, text: str, color: str):
        self.d_status.set(text, color)

    def _open_source(self):
        if self.current is not None and self.current.source_url:
            webbrowser.open_new_tab(self.current.source_url)

    # ------------------------------------------------------------ preview
    def _open_sound(self):
        if not self._sound_open:
            self.preview_sound.apply()
            self._sound_open = True
            if self.preview_sound.error:
                self._set_status(f"⚠ No sound: {self.preview_sound.error}", theme.WARN)

    def _close_sound(self):
        if self._sound_open:
            self._sound_open = False
            self.preview_sound.close()

    def _stop_preview(self):
        if self.transport.state != "stopped":
            self.transport.stop()

    def on_play(self):
        if not self.song:
            return
        if self.transport.state == PLAYING:
            self.transport.pause()
        else:
            self._open_sound()
            self.transport.play()
        self.update_view()

    def on_hotkey_play(self):
        if self.detail_open and self.song and self.transport.state != PLAYING:
            self.on_play()

    def on_pause(self):
        if self.transport.state in (PLAYING, PAUSED):
            self.transport.toggle_pause()
        self.update_view()

    def on_stop(self):
        self._stop_preview()
        self.update_view()

    def change_speed(self, _delta):   # the global speed hotkeys only apply to the real players
        pass

    def leave(self):
        """The page is hidden: the preview must not keep playing."""
        self._stop_preview()
        self._close_sound()
        self.update_view()

    def _on_slider_drag(self, v):
        if self.song:
            self._set_text("time", f"{format_time(v * self.song.length)}/{format_time(self.song.length)}", self.time)

    def _on_slider_commit(self, v):
        if self.song:
            self.transport.seek(v * self.song.length)

    def _set_text(self, key, text, widget):
        if self._last.get(key) != text:
            self._last[key] = text
            widget.set(text)

    def update_view(self):
        if not self.detail_open:
            return
        t = self.transport
        pos = t.position()
        t.consume_finished()
        self.roll.render(pos)
        length = t.length if self.song else 0.0
        if not self.slider.dragging:
            self.slider.set(pos / length if length else 0.0)
            self._set_text("time", f"{format_time(pos)}/{format_time(length)}", self.time)
        want = ("PAUSE", theme.PAUSE, theme.PAUSE_HOVER) if t.state == PLAYING else ("PLAY", theme.PLAY, theme.PLAY_HOVER)
        if self._last.get("play") != want:
            self._last["play"] = want
            self.play_btn.set_text(want[0])
            self.play_btn.set_colors(want[1], want[2])

    # ------------------------------------------------------------ actions
    def download(self):
        song = self.current
        if song is None:
            return
        self.dl_btn.set_enabled(False)
        self._set_status("Saving the file", theme.TEXT_MUTED)

        def done(path):
            self.dl_btn.set_enabled(self.song is not None)
            if self.current is song:
                self._set_status(f"Saved: {path}", theme.PLAY)

        def fail(msg):
            self.dl_btn.set_enabled(self.song is not None)
            if self.current is song:
                self._set_status(f"⚠ {msg}", theme.ERROR)

        self._run(lambda: self.client.download(song), done, fail)

    def open_in_piano(self):
        if self.local_path is None:
            return
        piano = self.app.pages["piano"]
        if piano.load_path(str(self.local_path)):
            self.app.show_page("piano")

    # ------------------------------------------------------------- config
    def _on_cfg_change(self, path: str):
        if path == "*" or path == "hub.apiUrl":
            self.client = HubClient.from_config(self.cfg)
            self._loaded = False
        if path == "*" or path.startswith("piano."):
            if path == "*" or path[len("piano."):].split(".")[0] in PlayerPage._ROLL_KEYS:
                self.rebuild_roll()
        elif path.startswith("roll."):
            self.roll.refresh_style()

    def shutdown(self):
        self._alive = False
        try:
            self.transport.stop()
        except Exception:
            log.exception("stopping the preview")
        self.preview_sound.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
