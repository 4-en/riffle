"""The one-click launch stops once no browser tab is connected (server.AutoExit)."""

import json
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
    deadline = time.time() + 30  # a Windows CI runner can take several seconds
    while not health(url):
        assert time.time() < deadline, "the test server did not start"
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
    server, thread, url = run_server(indexed, AutoExit(stop=None, min_uptime_seconds=0, idle_seconds=0.8, first_connect_seconds=60))
    tab = Tab(url)
    assert tab.lines[0] == "retry: 2000"
    assert not wait_stopped(thread, 1.5)  # a tab is open: keeps running (past the 0.8 s grace period)
    tab.close()
    assert wait_stopped(thread, 6.0)  # tab closed: noticed within ~2 s, then the grace period


def test_a_reload_within_the_grace_period_keeps_it_running(indexed):
    server, thread, url = run_server(indexed, AutoExit(stop=None, min_uptime_seconds=0, idle_seconds=1.5, first_connect_seconds=60))
    Tab(url).close()  # "reload": the old page goes away...
    tab = Tab(url)  # ...and the new one connects right after
    assert not wait_stopped(thread, 2.5)  # past the grace period: the new tab counts
    tab.close()
    assert wait_stopped(thread, 10.0)


def test_a_tab_that_closes_quickly_still_counts_as_seen(indexed):
    # Otherwise the server would wait the long "never connected" limit instead.
    server, thread, url = run_server(indexed, AutoExit(stop=None, min_uptime_seconds=0, idle_seconds=0.5, first_connect_seconds=60))
    Tab(url).close()
    assert wait_stopped(thread, 6.0)


def test_stops_if_no_tab_ever_connects(indexed):
    server, thread, url = run_server(indexed, AutoExit(stop=None, min_uptime_seconds=0, idle_seconds=60, first_connect_seconds=0.8))
    assert wait_stopped(thread, 5.0)


def test_never_stops_within_the_minimum_uptime(indexed):
    # Measured from before the server starts, so a slow start (a Windows runner) cannot
    # make it look early: the server's own clock starts later still.
    before = time.monotonic()
    server, thread, url = run_server(
        indexed, AutoExit(stop=None, min_uptime_seconds=3, idle_seconds=0.3, first_connect_seconds=0.3)
    )
    Tab(url).close()  # a tab that came and went at once
    assert wait_stopped(thread, 15.0)  # then the usual limits apply…
    assert time.monotonic() - before >= 3  # …but not within the minimum uptime


def test_waits_for_a_running_job(indexed):
    def slow_index(cfg, progress=None, report=print):
        time.sleep(2.5)

    # A tab holds the server open until the job runs (a slow CI machine can take
    # longer to start than any short "never connected" limit).
    server, thread, url = run_server(
        indexed, AutoExit(stop=None, min_uptime_seconds=0, idle_seconds=0.3, first_connect_seconds=60), index_runner=slow_index
    )
    tab = Tab(url)
    assert httpx.post(f"{url}/api/index", json={}).status_code == 200
    tab.close()
    assert not wait_stopped(thread, 1.5)  # indexing: not stopped although no tab is open
    assert wait_stopped(thread, 5.0)


def test_serve_mode_never_stops(indexed):
    server, thread, url = run_server(indexed)
    tab = Tab(url)
    time.sleep(0.2)
    first = json.loads(tab.lines[1].removeprefix("data: "))
    assert first["auto_exit"] is False and first["model"] == "ready"
    tab.close()
    assert not wait_stopped(thread, 1.5)
    server.should_exit = True
    assert wait_stopped(thread, 5.0)


def test_one_ctrl_c_stops_cleanly_with_tabs_open(indexed, caplog):
    """The open event streams end on the first Ctrl+C, so the graceful shutdown
    does not wait for them (well before uvicorn's 3 s safety timeout)."""
    import logging
    import signal

    from riffle.server import run_server

    servers = []
    port = free_port(0)
    app = create_app(indexed, text_encoder=FakeClip().encode_text)
    thread = threading.Thread(
        target=run_server, args=(app, "127.0.0.1", port), kwargs={"on_created": servers.append}, daemon=True
    )
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30  # a Windows CI runner can take several seconds
    while not health(url):
        assert time.time() < deadline, "the test server did not start"
        time.sleep(0.05)
    tabs = [Tab(url), Tab(url)]
    caplog.set_level(logging.ERROR)
    started = time.monotonic()
    servers[0].handle_exit(signal.SIGINT, None)  # what one Ctrl+C does
    assert wait_stopped(thread, 5.0)
    assert time.monotonic() - started < 2.0
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    for tab in tabs:
        tab.close()
