"""`riffle` without arguments: start (or find) the server and open the browser.

A small ``server.json`` in the data folder records the running instance, so a
second launch just opens another browser tab. It is only trusted if that URL
answers ``/api/health`` as Riffle, for the same config.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .config import Config

HOST = "127.0.0.1"
PREFERRED_PORT = 8000


def runtime_file(cfg: Config) -> Path:
    return cfg.data_dir / "server.json"


def health(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == "riffle" else None


def running_url(cfg: Config) -> str | None:
    """The URL of a Riffle already serving this config, if any."""
    try:
        url = json.loads(runtime_file(cfg).read_text())["url"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    info = health(url)
    return url if info and info.get("config") == str(cfg.path) else None


def free_port(preferred: int = PREFERRED_PORT) -> int:
    """``preferred`` if it is free, else any free port."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise OSError("no free port")


def open_when_ready(url: str, timeout: float = 180.0) -> None:
    """Open the browser once the server answers (the model loads first)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(url):
            webbrowser.open(url)
            return
        time.sleep(0.3)
    print(f"Riffle did not start within {timeout:.0f} s; open {url} yourself once it is up.")


def launch(cfg: Config, open_browser: bool = True) -> None:
    url = running_url(cfg)
    if url:
        print(f"Riffle is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return

    import uvicorn

    port = free_port()
    url = f"http://{HOST}:{port}"
    rt = runtime_file(cfg)
    rt.parent.mkdir(parents=True, exist_ok=True)
    rt.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    os.environ["RIFFLE_CONFIG"] = str(cfg.path)
    if open_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    print(f"Riffle is starting at {url} (the browser opens when it is ready). Press Ctrl+C to stop.")
    try:
        uvicorn.run("riffle.server:create_app", factory=True, host=HOST, port=port, log_level="warning")
    finally:
        try:
            if json.loads(rt.read_text()).get("pid") == os.getpid():
                rt.unlink()
        except (OSError, ValueError):
            pass
