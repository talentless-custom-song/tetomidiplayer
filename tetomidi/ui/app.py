"""Main window: wires config, engine, hotkeys and pages together."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox

from PIL import ImageTk

from .. import APP_NAME
from ..config import Config
from ..console import KeyConsole
from ..engine.dispatchers import create_dispatcher
from ..engine.transport import Transport
from ..fakesound import FakeKeySound
from ..hotkeys import HotkeyManager
from ..keysim import KeySim, ScopedKeys
from ..paths import resource_path
from . import gfx, theme
from .gfx import px
from .hub_page import HubPage
from .player_page import PlayerPage
from .settings_page import SettingsPage
from .sidebar import Sidebar

log = logging.getLogger(__name__)
MODES = ("piano", "drums", "guitar")
TICK_MS = 16
IDLE_TICK_MS = 50   # nothing is on screen (minimized / hidden): hotkeys are still polled, drawing is not needed
MIN_OPACITY = 0.4


class TetoApp:
    def __init__(self, config: Config | None = None, keys: KeySim | None = None, intro: bool = False):
        self.root = tk.Tk(className=APP_NAME)   # WM_CLASS "TetoMIDI": launchers and docks group the windows
        if intro:   # invisible from the very first moment, so the main window never flashes before the intro
            try:
                self.root.attributes("-alpha", 0.0)
            except tk.TclError:
                intro = False
        gfx.init_scale(self.root)
        self.root.title(APP_NAME)
        self.root.configure(bg=theme.PAGE_BG)
        self.root.geometry(f"{px(1060)}x{px(620)}")
        self.root.minsize(px(780), px(540))
        self._set_icon()

        self.cfg = config or Config()
        self.keys = keys or KeySim()
        self.consoles = {m: KeyConsole() for m in MODES}   # key history per instrument (mini player console)
        self.keys.add_listener(self._on_key_event)
        self.fake_sound = FakeKeySound(self.cfg)
        self.keys.add_listener(self.fake_sound.on_key)
        self.fake_sound.apply()
        self.transports = {m: Transport(create_dispatcher(m, ScopedKeys(self.keys, m), self.cfg), self.cfg)
                           for m in MODES}
        self.speed = {m: 1.0 for m in MODES}
        self.hotkeys = HotkeyManager()
        self.hotkeys.start()
        self.current = None
        self.midi = None   # MIDI player window, created on first use

        card = tk.Frame(self.root, bg=theme.SIDEBAR_BG)
        card.pack(fill="both", expand=True)   # no outer margin: the dark UI fills the whole window
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(card, self.show_page, width=px(262))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.content = tk.Frame(card, bg=theme.MAIN_BG)
        self.content.grid(row=0, column=1, sticky="nsew", pady=(px(14), 0))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, tk.Frame] = {m: PlayerPage(self.content, self, m) for m in MODES}
        self.pages["hub"] = HubPage(self.content, self)
        self.pages["settings"] = SettingsPage(self.content, self)
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.cfg.on_change(self._cfg_changed)
        self._bind_hotkeys()
        self.set_topmost(bool(self.cfg.get("app.topmost", False)), save=False)
        self.show_page("piano")
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(TICK_MS, self._tick)
        self.splash = None
        self.mini = None
        self.last_player = "piano"
        if intro:
            from .splash import Splash
            self.root.update_idletasks()
            self.splash = Splash(self.root, target_alpha=self.opacity(), on_done=self._intro_done)
        else:
            self._apply_opacity()

    # ------------------------------------------------------------ window
    def opacity(self) -> float:
        try:
            return min(max(float(self.cfg.get("app.opacity", 1.0)), MIN_OPACITY), 1.0)
        except (TypeError, ValueError):
            return 1.0

    def set_opacity(self, value: float):
        self.cfg.set("app.opacity", round(min(max(float(value), MIN_OPACITY), 1.0), 2))

    def _apply_opacity(self):
        if self.splash is not None:   # the intro still owns the window's alpha
            return
        for win in (self.root, self.mini, self.midi):
            if win is not None:
                try:
                    win.attributes("-alpha", self.opacity())
                except tk.TclError:   # platform without window transparency
                    pass

    def _intro_done(self):
        self.splash = None
        self._apply_opacity()

    # ------------------------------------------------------------ mini player
    def player_page(self) -> PlayerPage | None:
        page = self.pages.get(self.current)
        return page if isinstance(page, PlayerPage) else self.pages.get(self.last_player)

    def show_mini(self, mode: str | None = None):
        if mode in MODES and self.current != mode:
            self.show_page(mode)
        if not isinstance(self.page, PlayerPage):
            self.show_page(self.last_player)
        from .mini_player import MiniPlayer
        if self.mini is None:
            self.mini = MiniPlayer(self)
        self.mini.place_near_root()
        self.root.withdraw()
        self.mini.deiconify()
        self.mini.lift()
        self._apply_opacity()
        self.mini.update_view()
        self.mini.after(80, self.mini.ensure_taskbar_button)

    def hide_mini(self):
        if self.mini is not None:
            self.mini.withdraw()
        self.root.deiconify()
        self.root.lift()

    def mini_visible(self) -> bool:
        return self.mini is not None and bool(self.mini.winfo_viewable())

    def _on_key_event(self, source, action: str, label: str):
        console = self.consoles.get(source)
        if console is not None:
            console.add(f"{action}: {label}")

    # ------------------------------------------------------- MIDI player window
    def midi_window(self):
        """The MIDI player window, created on first use (hidden until it is opened)."""
        if self.midi is None:
            from .midi_player_window import MidiPlayerWindow
            self.midi = MidiPlayerWindow(self)
            self.midi.withdraw()
        return self.midi

    def show_midi_player(self):
        self.midi_window()
        self.midi.deiconify()
        self.midi.lift()
        self._apply_opacity()
        self.midi.update_view()

    def midi_visible(self) -> bool:
        return self.midi is not None and bool(self.midi.winfo_viewable())

    def midi_owns(self, mode: str) -> bool:
        """True while the MIDI player window is open on this instrument (its page must keep playing)."""
        return self.midi_visible() and self.midi.mode == mode

    def song_finished(self, mode: str):
        # the playlist also drives the mini player, so it continues there even with the MIDI window closed
        if self.midi is not None and self.midi.mode == mode and (self.midi_visible() or self.mini_visible()):
            self.midi.on_finished()

    def _set_icon(self):
        try:
            from PIL import Image
            img = Image.open(resource_path("assets/icons/app.png")).convert("RGBA").resize((64, 64), Image.LANCZOS)
            self._icon = ImageTk.PhotoImage(img)
            self.root.iconphoto(True, self._icon)
        except Exception:
            log.debug("window icon not set", exc_info=True)

    def set_topmost(self, value: bool, save: bool = True):
        self.root.attributes("-topmost", bool(value))
        if getattr(self, "midi", None) is not None:
            self.midi.attributes("-topmost", bool(value))
        if save:
            self.cfg.set("app.topmost", bool(value))

    def show_page(self, name: str):
        if name == self.current:
            return
        old = self.pages.get(self.current)
        if isinstance(old, (PlayerPage, HubPage)):
            old.leave()  # never keep sending keys (or playing a preview) for a page that is no longer shown
        self.current = name
        if name in MODES:
            self.last_player = name
        page = self.pages[name]
        page.tkraise()
        self.sidebar.set_active(name)
        if isinstance(page, SettingsPage):
            page.refresh()
        else:
            page.update_view()
        if isinstance(page, HubPage):
            page.on_show()

    def _on_root_configure(self, e):
        if e.widget is self.root:
            self.sidebar.set_compact(e.width < px(900))

    @property
    def page(self):
        return self.pages.get(self.current)

    # ------------------------------------------------------------- speed
    def set_speed(self, mode: str, value: float):
        value = min(max(float(value), 0.1), 5.0)
        self.speed[mode] = value
        self.transports[mode].set_speed(value)

    # ----------------------------------------------------------- hotkeys
    def _bind_hotkeys(self):
        def current_player():   # the hub page answers the same hotkeys for its preview
            return self.page if isinstance(self.page, (PlayerPage, HubPage)) else None

        def call(method, *args):
            def run():
                if self.midi_visible():   # while the MIDI player window is open the hotkeys drive it
                    self.midi.hotkey(method, *args)
                    return
                p = current_player()
                if p:
                    getattr(p, method)(*args)
            return run

        hk = self.cfg.data["hotkeys"]
        self.hotkeys.set_bindings({
            hk["play"]: call("on_hotkey_play"),
            hk["pause"]: call("on_pause"),
            hk["stop"]: call("on_stop"),
            hk["speedup"]: call("change_speed", 1),
            hk["slowdown"]: call("change_speed", -1),
        })

    def rebind_hotkey(self, name: str):
        """Wait for the next key press and bind it to ``name`` (Esc cancels)."""
        if not self.hotkeys.capturing and self.hotkeys._listener is None:
            messagebox.showinfo("Hotkeys unavailable",
                                f"Global hotkeys cannot start on this system.\n{self.hotkeys.error}", parent=self.root)
            return
        for page in self.pages.values():
            chips = getattr(page, "_hot_chips", None)
            if chips and name in chips:
                chips[name].set_text("…")
        if "settings" in self.pages:
            self.pages["settings"].show_chip_capturing(name)

        def done(key: str):
            current = self.cfg.get(f"hotkeys.{name}")
            if key == "esc":
                key = current
            clash = [n for n, k in self.cfg.data["hotkeys"].items() if k == key and n != name]
            if clash:
                messagebox.showinfo("Keybind conflict", f"“{key}” is already used for {clash[0]}.", parent=self.root)
                key = current
            self.cfg.set(f"hotkeys.{name}", key)
            self._cfg_changed("hotkeys." + name)
            for page in self.pages.values():  # restore chip text if nothing changed
                chips = getattr(page, "_hot_chips", None)
                if chips:
                    chips[name].set_text(self.cfg.get(f"hotkeys.{name}", ""))
            self.pages["settings"]._cfg_changed("hotkeys.")

        self.hotkeys.capture_next(done)

    def _cfg_changed(self, path: str):
        if path.startswith("hotkeys.") or path == "*":
            self._bind_hotkeys()
        if path in ("app.opacity", "*"):
            self._apply_opacity()
        if path.startswith("fakeSound.") or path == "*":
            if path != "fakeSound.volume":   # the volume is read on every click, no need to reopen the streams
                self.fake_sound.apply()

    # ------------------------------------------------------------- misc
    def reset_settings(self):
        for t in self.transports.values():
            t.stop()
        self.cfg.reset()
        self.speed = {m: 1.0 for m in MODES}
        for t in self.transports.values():
            t.set_speed(1.0)
        old = self.pages["settings"]
        old.destroy()
        self.pages["settings"] = SettingsPage(self.content, self)
        self.pages["settings"].grid(row=0, column=0, sticky="nsew")
        self.pages["settings"].tkraise()
        self.set_topmost(False, save=False)
        if self.midi is not None:
            self.midi.on_settings_reset()

    def _tick(self):
        delay = TICK_MS
        try:
            self.hotkeys.drain()
            page = self.page
            if isinstance(page, (PlayerPage, HubPage)):
                page.update_view()
            if self.mini is not None and self.mini.winfo_viewable():
                self.mini.update_view()
            if self.midi_visible():
                self.midi.update_view()
            if not (self.root.winfo_viewable() or self.mini_visible() or self.midi_visible()):
                delay = IDLE_TICK_MS
        except Exception:
            log.exception("tick failed")
        self.root.after(delay, self._tick)

    def quit(self):
        for t in self.transports.values():
            try:
                t.stop()
            except Exception:
                log.exception("stopping transport")
        self.keys.release_all()
        self.pages["hub"].shutdown()
        self.fake_sound.close()
        self.cfg.save()
        self.hotkeys.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
