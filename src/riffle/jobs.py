"""Background jobs (indexing, export) with progress exposed to the API."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import Config

log = logging.getLogger(__name__)


class _Bar:
    """Minimal tqdm stand-in: iterate or call update(), reporting into the job."""

    def __init__(self, job: BackgroundJob, iterable=None, total=None, desc=""):
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


class BackgroundJob:
    """Runs ``run(cfg, progress=..., report=..., **kwargs)`` in a thread.

    With ``queue=True`` (indexing), starting while running schedules one more run
    afterwards; otherwise (export) a second start is refused."""

    def __init__(self, cfg: Config, run: Callable, queue: bool = False):
        self.cfg = cfg
        self._run = run
        self.queue = queue
        self.kwargs: dict = {}
        self.result = None
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

    def start(self, **kwargs) -> bool:
        """Start a run; returns False if one is running and was not queued."""
        with self.lock:
            if self.running:
                if self.queue:
                    self.pending = True
                return self.queue
            self.running = True
            self.pending = False
            self.kwargs = kwargs
            self.result = None
            self.step, self.done, self.total = "starting", 0, None
            self.lines, self.error = [], None
            self.started_at, self.finished_at = time.time(), None
        self.thread = threading.Thread(target=self._work, name="riffle-job", daemon=True)
        self.thread.start()
        return True

    def _report(self, line: str) -> None:
        log.info("index: %s", line)
        with self.lock:
            self.lines.append(line)
            self.step, self.done, self.total = line, 0, None

    def _work(self) -> None:
        while True:
            try:
                self.result = self._run(
                    self.cfg,
                    progress=lambda iterable=None, **kw: _Bar(self, iterable, **kw),
                    report=self._report,
                    **self.kwargs,
                )
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
                "result": self.result,
            }


def index_job(cfg: Config, run: Callable | None = None) -> BackgroundJob:
    if run is None:
        from .index import run_index as run
    return BackgroundJob(cfg, run, queue=True)
