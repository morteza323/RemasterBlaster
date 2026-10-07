#!/usr/bin/env python3
"""
GUI entry point. Run with:

    python3 -m ui.app

Requires PyQt6 (see requirements.txt -- uncomment the PyQt6 line and
`pip install -r requirements.txt`). Not runnable in this sandbox: no
network access to install PyQt6 and no display to show a window on.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print(
            "UV Reconstruction Studio GUI requires PyQt6, which is not installed.\n\n"
            "Install it with:\n"
            "    pip install PyQt6\n\n"
            "(or: pip install -r requirements.txt, after uncommenting the PyQt6 line)\n\n"
            "The CLI works without PyQt6 -- try `python3 main.py --diagnostics` "
            "or `python3 main.py --help`.",
            file=sys.stderr,
        )
        return 1

    from ui.main_window import MainWindow

    try:
        # A missing/unreachable display (headless, SSH without X11/Wayland
        # forwarding) most often fails right here, in QApplication's
        # platform-plugin init -- not later in MainWindow().
        app = QApplication(sys.argv)
        window = MainWindow()
    except Exception as exc:
        print(
            f"UV Reconstruction Studio GUI failed to start: {exc}\n\n"
            "If you're on a headless machine or over SSH without X11/Wayland "
            "forwarding, a desktop GUI can't be shown here -- use the CLI instead "
            "(`python3 main.py --help`).",
            file=sys.stderr,
        )
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
