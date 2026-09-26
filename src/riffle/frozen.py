"""Running as the standalone app (a PyInstaller build without a console window).

There is no terminal to print to: ``sys.stdout`` and ``sys.stderr`` are ``None``
(uvicorn's logging would crash on that), so everything goes to a log file in the
cache folder instead. Errors that stop the app are shown in a native message box.
On Linux, PyInstaller points ``LD_LIBRARY_PATH`` at the bundled libraries; the
browser opened by ``webbrowser`` must not inherit that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

LOG_NAME = "riffle.log"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def log_path() -> Path | None:
    """The app's log file, when running as the standalone app."""
    if not is_frozen():
        return None
    from .paths import cache_dir

    return cache_dir() / LOG_NAME


def setup() -> None:
    """Prepare the process for running without a console. No-op outside the app."""
    if not is_frozen():
        return
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.replace(path, path.with_name(LOG_NAME + ".1"))  # keep the previous run
        log = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        log = open(os.devnull, "w")
    sys.stdout = sys.stderr = log

    if sys.platform.startswith("linux"):
        # Child processes (xdg-open, the browser) get the system libraries back.
        original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is not None:
            os.environ["LD_LIBRARY_PATH"] = original
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)


def show_error(title: str, text: str) -> None:
    """A native message box, as far as the platform offers one (best effort)."""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
        elif sys.platform == "darwin":
            quoted = text.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display dialog "{quoted}" with title "{title}" buttons {{"OK"}} with icon stop'
            subprocess.run(["osascript", "-e", script], check=False, timeout=600)
        else:
            for cmd in (
                ["zenity", "--error", f"--title={title}", f"--text={text}"],
                ["kdialog", "--title", title, "--error", text],
                ["notify-send", "-u", "critical", title, text],
            ):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, check=False, timeout=600)
                    break
    except Exception:  # noqa: BLE001 - the log has the details anyway
        pass
