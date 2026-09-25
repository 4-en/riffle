"""Run `archive index` in a background thread and expose its progress to the API."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import Config

log = logging.getLogger(__name__)


class _Bar:
    """Minimal tqdm stand-in: iterate or call update(), reporting into the job."""

    def __init__(self, job: IndexJob, iterable=None, total=None, desc=""):
        self.job = job
        self.iterable = iterable
        if total is None and iterable is not None and hasattr(iterable, "__len__"):
            total = len(iterable)
        with job.lock:
            job.step, job.done, job.total = desc, 0, total

    def __iter__(self):
        for item in self.iterable:
            yield item
            self.update(1)

    def update(self, n: int = 1) -> None:
        with self.job.lock:
            self.job.done += n

    def close(self) -> None:
        pass


class IndexJob:
    def __init__(self, cfg: Config, run: Callable | None = None):
        self.cfg = cfg
        self._run = run
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.pending = False
        self.step = ""
        self.done = 0
        self.total: int | None = None
        self.lines: list[str] = []
        self.error: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def start(self) -> None:
        """Start a run. If one is in progress, another run follows it, so changes
        made meanwhile (e.g. a newly added folder) are picked up."""
        with self.lock:
            if self.running:
                self.pending = True
                return
            self.running = True
            self.pending = False
            self.step, self.done, self.total = "starting", 0, None
            self.lines, self.error = [], None
            self.started_at, self.finished_at = time.time(), None
        self.thread = threading.Thread(target=self._work, name="archive-index", daemon=True)
        self.thread.start()

    def _report(self, line: str) -> None:
        log.info("index: %s", line)
        with self.lock:
            self.lines.append(line)
            self.step, self.done, self.total = line, 0, None

    def _work(self) -> None:
        run = self._run
        if run is None:
            from .index import run_index as run
        while True:
            try:
                run(self.cfg, progress=lambda iterable=None, **kw: _Bar(self, iterable, **kw), report=self._report)
            except Exception as e:  # noqa: BLE001 - surfaced to the UI
                log.exception("index failed")
                with self.lock:
                    self.error = f"{type(e).__name__}: {e}"
            with self.lock:
                if self.pending and not self.error:
                    self.pending = False
                    self.lines.append("re-running for changes made during the last run")
                    continue
                self.running = False
                self.pending = False
                self.step = "failed" if self.error else "done"
                self.finished_at = time.time()
                return

    def status(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "pending": self.pending,
                "step": self.step,
                "done": self.done,
                "total": self.total,
                "lines": list(self.lines),
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
