"""`riffle` without arguments: finding a running instance, ports, health."""

import json
import socket
import threading
import time

import pytest
import uvicorn

from riffle.launch import free_port, health, running_url, runtime_file
from riffle.server import create_app
from conftest import FakeClip


@pytest.fixture
def server(indexed):
    """A real Riffle server on a free port (fake encoder), stopped afterwards."""
    indexed.path = indexed.root / "config.yaml"  # the identity the launcher compares
    port = free_port(0)
    srv = uvicorn.Server(uvicorn.Config(create_app(indexed, text_encoder=FakeClip().encode_text), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if health(url):
            break
        time.sleep(0.05)
    yield indexed, url
    srv.should_exit = True
    thread.join(timeout=5)


def test_health_identifies_riffle_and_its_config(server):
    cfg, url = server
    info = health(url)
    assert info["app"] == "riffle" and info["config"] == str(cfg.path)
    assert health("http://127.0.0.1:1") is None  # nothing there


def test_running_instance_is_reused_only_for_the_same_config(server, tmp_path):
    cfg, url = server
    assert running_url(cfg) is None  # no runtime file yet
    runtime_file(cfg).write_text(json.dumps({"url": url, "pid": 1}))
    assert running_url(cfg) == url
    other = type(cfg)(**{**cfg.__dict__, "path": tmp_path / "other.yaml"})
    assert running_url(other) is None  # a different library: start another instance


def test_stale_runtime_file_is_ignored(indexed):
    runtime_file(indexed).parent.mkdir(parents=True, exist_ok=True)
    runtime_file(indexed).write_text(json.dumps({"url": "http://127.0.0.1:1", "pid": 1}))
    assert running_url(indexed) is None
    runtime_file(indexed).write_text("garbage")
    assert running_url(indexed) is None


def test_free_port_falls_back_when_taken():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        port = free_port(taken)
        assert port != taken and port > 0
