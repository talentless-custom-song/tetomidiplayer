"""Program entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from .paths import log_dir
from .platform_tweaks import process_setup


def _setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            log_dir() / "tetomidi.log", maxBytes=512_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    if debug and sys.stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers or None)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="tetoMIDI")
    parser.add_argument("--debug", action="store_true", help="log to the console as well")
    args = parser.parse_args(argv)
    _setup_logging(args.debug)
    undo = process_setup()
    from .ui.app import TetoApp  # imported late so DPI awareness is set before Tk starts
    try:
        TetoApp(intro=True).run()
    finally:
        if undo:
            undo()


if __name__ == "__main__":
    main()
