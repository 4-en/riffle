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
    deadline = time.time() + 30  # a Windows CI runner can take several seconds
    while not health(url):
        assert time.time() < deadline, "the test server did not start"
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
    runtime_file(cfg).write_text(json.dumps({"url": url, "pid": 1}), encoding="utf-8")
    assert running_url(cfg) == url
    other = type(cfg)(**{**cfg.__dict__, "path": tmp_path / "other.yaml"})
    assert running_url(other) is None  # a different library: start another instance


def test_stale_runtime_file_is_ignored(indexed):
    runtime_file(indexed).parent.mkdir(parents=True, exist_ok=True)
    runtime_file(indexed).write_text(json.dumps({"url": "http://127.0.0.1:1", "pid": 1}), encoding="utf-8")
    assert running_url(indexed) is None
    runtime_file(indexed).write_text("garbage", encoding="utf-8")
    assert running_url(indexed) is None


def test_free_port_falls_back_when_taken():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        port = free_port(taken)
        assert port != taken and port > 0


# ---- the AI model loads in the background -------------------------------------


def test_model_loads_in_the_background(indexed, monkeypatch):
    from fastapi.testclient import TestClient

    import riffle.embed

    release = threading.Event()

    class SlowClip:
        def __init__(self, mcfg, text_only=False):
            release.wait(10)  # e.g. the first-start download
            self.encode_text = FakeClip().encode_text

    monkeypatch.setattr(riffle.embed, "Clip", SlowClip)
    with TestClient(create_app(indexed)) as c:
        info = c.get("/api/health").json()
        assert info["model"] == "loading" and info["log"] is None  # not the standalone app
        assert c.get("/api/photos").status_code == 200  # browsing works meanwhile
        res = c.get("/api/search/text", params={"q": "red"})
        assert res.status_code == 503 and "still loading" in res.json()["detail"]
        assert c.get("/api/tags").json()["text_search"] is False
        release.set()
        for _ in range(100):
            if c.get("/api/health").json()["model"] != "loading":
                break
            time.sleep(0.05)
        assert c.get("/api/health").json()["model"] == "ready"
        assert c.get("/api/search/text", params={"q": "red"}).status_code == 200


def test_model_failure_is_reported(indexed, monkeypatch):
    from fastapi.testclient import TestClient

    import riffle.embed

    def broken(*args, **kwargs):
        raise OSError("no internet connection")

    monkeypatch.setattr(riffle.embed, "Clip", broken)
    with TestClient(create_app(indexed)) as c:
        for _ in range(100):
            info = c.get("/api/health").json()
            if info["model"] != "loading":
                break
            time.sleep(0.05)
        assert info["model"] == "failed" and info["model_error"] == "no internet connection"
        assert c.get("/api/photos").status_code == 200
