"""Entry point of the standalone app (PyInstaller): `riffle` without a console.

Double-clicked, it runs the one-click launch (server + browser, stops when the
last tab is closed). Arguments work as for the `riffle` command, but output goes
to the log file (see riffle.frozen).
"""

import multiprocessing
import sys
import traceback

# First: spawned worker processes (thumbnails) re-run this executable.
multiprocessing.freeze_support()

from riffle import frozen  # noqa: E402

frozen.setup()

# Older macOS versions pass a process serial number when started from Finder.
sys.argv = [a for a in sys.argv if not a.startswith("-psn_")]

try:
    from riffle.cli import main

    main()
except SystemExit as e:
    if e.code not in (None, 0):
        message = e.code if isinstance(e.code, str) else f"Riffle stopped with exit code {e.code}."
        print(message, file=sys.stderr)
        frozen.show_error("Riffle", f"{message}\n\nDetails: {frozen.log_path()}")
    raise
except KeyboardInterrupt:
    pass
except Exception as e:
    traceback.print_exc()
    frozen.show_error("Riffle could not start", f"{type(e).__name__}: {e}\n\nDetails: {frozen.log_path()}")
    sys.exit(1)
