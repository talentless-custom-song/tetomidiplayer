"""Filesystem locations (resources bundled by PyInstaller, per-user config/log dirs)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> str:
    """Absolute path of a bundled resource, both from source and inside a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    return str(Path(base) / relative) if base else str(PROJECT_ROOT / relative)


def config_dir() -> Path:
    """Per-user writable directory (created on demand)."""
    override = os.environ.get("TETOMIDI_CONFIG_DIR")
    if override:
        path = Path(override)
    elif sys.platform.startswith("win"):
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path.home()
    return path


def log_dir() -> Path:
    path = config_dir() / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return config_dir()
    return path
