"""Everything that differs between Windows and Linux lives here, so the UI code stays platform-neutral.

* ``process_setup()``   - DPI awareness, 1 ms timer, taskbar identity (Windows); nothing to do on Linux.
* ``make_frameless()``  - removes the window title bar (used by the mini player).
* ``show_in_taskbar()`` - gives a frameless window a taskbar button again (Windows).
* ``session_type()``    - "x11", "wayland" or "" (used for a clear message when key injection cannot work).

Every function is best effort: a failure is logged and the app keeps working with the normal window frame.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
APP_USER_MODEL_ID = "tetoMIDI.Player"


def session_type() -> str:
    """"x11", "wayland" (native Wayland session) or "" when unknown."""
    kind = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if kind in ("x11", "wayland"):
        return kind
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11" if os.environ.get("DISPLAY") else ""


def process_setup() -> Callable[[], None] | None:
    """Call once before Tk starts.  Returns a function to call on exit (or None)."""
    if not IS_WINDOWS:
        return None
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on high-DPI screens
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:   # own taskbar identity: correct icon and grouping, also for the portable exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    try:   # 1 ms timer resolution for accurate note timing
        ctypes.windll.winmm.timeBeginPeriod(1)
        return lambda: ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        return None


def make_frameless(win) -> bool:
    """Remove the title bar and borders of a Toplevel that is still hidden.  True when it worked.

    Windows: ``overrideredirect``.  Linux/X11: the ``_MOTIF_WM_HINTS`` property (understood by GNOME, KDE, Xfce,
    i3, Openbox ...) instead of ``overrideredirect``, because the window manager then still manages the window and
    keeps ``-topmost`` working (an unmanaged window would sink behind the game as soon as the game gets focus).
    """
    if IS_WINDOWS:
        try:
            win.overrideredirect(True)
            return True
        except Exception:
            log.debug("overrideredirect failed", exc_info=True)
            return False
    if IS_LINUX:
        try:
            return _x11_no_decorations(win)
        except Exception:
            log.debug("could not remove the window frame", exc_info=True)
    return False


def _x11_no_decorations(win) -> bool:
    from Xlib import display   # python-xlib is installed together with pynput on Linux

    win.update_idletasks()   # makes sure the X window exists
    d = display.Display()
    try:
        inner = d.create_resource_object("window", win.winfo_id())
        ids = {inner.id}
        parent = inner.query_tree().parent   # Tk's wrapper window is the one the window manager reads
        if parent is not None and parent.id != d.screen().root.id:
            ids.add(parent.id)
        atom = d.intern_atom("_MOTIF_WM_HINTS")
        for wid in ids:   # flags = MWM_HINTS_DECORATIONS (2), decorations = 0
            d.create_resource_object("window", wid).change_property(atom, atom, 32, [2, 0, 0, 0, 0])
        d.sync()
    finally:
        d.close()
    return True


def show_in_taskbar(win) -> None:
    """A frameless window has no taskbar button on Windows; ask for one (the window blinks once)."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        gwl_exstyle, ws_ex_appwindow, ws_ex_toolwindow = -20, 0x00040000, 0x00000080
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        user32.SetWindowLongW(hwnd, gwl_exstyle, (style & ~ws_ex_toolwindow) | ws_ex_appwindow)
        win.withdraw()
        win.after(10, win.deiconify)
    except Exception:
        log.debug("taskbar button not set", exc_info=True)


def round_corners(win, radius: int) -> bool:
    """Round the four corners of a frameless window (``radius`` in pixels).  True when it worked.

    Windows 11: the system's own smooth rounding (DWM).  Older Windows: a rounded window region.
    Linux/X11: the Shape extension (clips the window; edges are not anti-aliased).
    """
    try:
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        if w < 4 or h < 4:   # a window that was never shown reports 1 x 1: use the size it was asked to have
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            geo = win.geometry().split("+")[0].split("x")
            if len(geo) == 2 and geo[0].isdigit() and geo[1].isdigit() and int(geo[0]) > 4:
                w, h = int(geo[0]), int(geo[1])
        if w < 4 or h < 4:
            return False
        if IS_WINDOWS:
            return _win_round(win, w, h, radius)
        if IS_LINUX:
            return _x11_round(win, w, h, radius)
    except Exception:
        log.debug("rounded corners not set", exc_info=True)
    return False


def _win_round(win, w: int, h: int, radius: int) -> bool:
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    from ctypes import wintypes
    rect = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):   # the real outer size in device pixels
        w, h = rect.right - rect.left, rect.bottom - rect.top
    if getattr(sys, "getwindowsversion", None) and sys.getwindowsversion().build >= 22000:
        pref = ctypes.c_int(2)   # DWMWCP_ROUND
        try:
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4) == 0:
                return True
        except Exception:
            pass
    region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
    user32.SetWindowRgn(hwnd, region, True)   # the system owns the region from here on
    return True


def _x11_round(win, w: int, h: int, radius: int) -> bool:
    from Xlib import display
    from Xlib.ext import shape

    d = display.Display()
    try:
        if not d.has_extension("SHAPE"):
            return False
        inner = d.create_resource_object("window", win.winfo_id())
        targets = [inner]
        parent = inner.query_tree().parent
        if parent is not None and parent.id != d.screen().root.id:
            targets.append(parent)
        r = max(1, min(radius, w // 2, h // 2))
        mask = d.screen().root.create_pixmap(w, h, 1)
        gc = mask.create_gc(foreground=0, background=0)
        mask.poly_fill_rectangle(gc, [(0, 0, w, h)])   # everything outside
        gc.change(foreground=1)
        mask.poly_fill_rectangle(gc, [(r, 0, w - 2 * r, h), (0, r, w, h - 2 * r)])
        full = 360 * 64
        mask.poly_fill_arc(gc, [(0, 0, 2 * r, 2 * r, 0, full), (w - 2 * r, 0, 2 * r, 2 * r, 0, full),
                                (0, h - 2 * r, 2 * r, 2 * r, 0, full), (w - 2 * r, h - 2 * r, 2 * r, 2 * r, 0, full)])
        for target in targets:
            target.shape_mask(shape.SO.Set, shape.SK.Bounding, 0, 0, mask)
        d.sync()
        mask.free()
    finally:
        d.close()
    return True
