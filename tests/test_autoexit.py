"""The one-click launch stops once no browser tab is connected (server.AutoExit)."""

import threading
import time

import httpx
import pytest
import uvicorn

from riffle.launch import free_port, health
from riffle.server import AutoExit, create_app
from conftest import FakeClip


def run_server(cfg, auto_exit=None, index_runner=None):
    port = free_port(0)
    app = create_app(cfg, text_encoder=FakeClip().encode_text, index_runner=index_runner, auto_exit=auto_exit)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    if auto_exit:
        auto_exit.stop = lambda: setattr(server, "should_exit", True)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if health(url):
            break
        time.sleep(0.05)
    return server, thread, url


class Tab:
    """A browser tab: an /api/events stream read continuously in the background,
    like EventSource does (a stream nobody reads is closed by httpx)."""

    def __init__(self, url):
        self.client = httpx.Client(timeout=30)
        self.lines = []
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._read, args=(url,), daemon=True)
        self.thread.start()
        assert self.ready.wait(5), "the events stream did not start"

    def _read(self, url):
        try:
            with self.client.stream("GET", f"{url}/api/events") as r:
                for line in r.iter_lines():
                    self.lines.append(line)
                    self.ready.set()
        except httpx.HTTPError:
            pass  # closed

    def close(self):
        self.client.close()


def wait_stopped(thread, timeout):
    thread.join(timeout)
    return not thread.is_alive()


def test_stops_after_the_last_tab_closes(indexed):
    server, thread, url = run_server(indexed, AutoExit(stop=None, idle_seconds=0.8, first_connect_seconds=60))
    tab = Tab(url)
    assert tab.lines[0] == "retry: 2000"
    assert not wait_stopped(thread, 3.0)  # a tab is open: keeps running
    tab.close()
    assert wait_stopped(thread, 6.0)  # tab closed: noticed within ~2 s, then the grace period


def test_a_reload_within_the_grace_period_keeps_it_running(indexed):
    server, thread, url = run_server(indexed, AutoExit(stop=None, idle_seconds=4, first_connect_seconds=60))
    Tab(url).close()  # "reload": the old page goes away...
    tab = Tab(url)  # ...and the new one connects right after
    assert not wait_stopped(thread, 6.0)
    tab.close()
    assert wait_stopped(thread, 10.0)


def test_a_tab_that_closes_quickly_still_counts_as_seen(indexed):
    # Otherwise the server would wait the long "never connected" limit instead.
    server, thread, url = run_server(indexed, AutoExit(stop=None, idle_seconds=0.5, first_connect_seconds=60))
    Tab(url).close()
    assert wait_stopped(thread, 6.0)


def test_stops_if_no_tab_ever_connects(indexed):
    server, thread, url = run_server(indexed, AutoExit(stop=None, idle_seconds=60, first_connect_seconds=0.8))
    assert wait_stopped(thread, 5.0)


def test_waits_for_a_running_job(indexed):
    def slow_index(cfg, progress=None, report=print):
        time.sleep(2.5)

    server, thread, url = run_server(
        indexed, AutoExit(stop=None, idle_seconds=0.3, first_connect_seconds=0.3), index_runner=slow_index
    )
    httpx.post(f"{url}/api/index", json={})
    assert not wait_stopped(thread, 1.5)  # indexing: not stopped although no tab is open
    assert wait_stopped(thread, 5.0)


def test_serve_mode_never_stops(indexed):
    server, thread, url = run_server(indexed)
    tab = Tab(url)
    time.sleep(0.2)
    assert tab.lines[1] == 'data: {"auto_exit": false}'
    tab.close()
    assert not wait_stopped(thread, 3.0)
    server.should_exit = True
    assert wait_stopped(thread, 5.0)
