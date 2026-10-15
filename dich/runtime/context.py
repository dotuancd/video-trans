# What a driver gets besides its inputs: where to write, the pipeline log, progress.
import shutil
import time
from pathlib import Path

from dich.logging.events import EventLog

PROGRESS_EVERY = 0.5   # seconds between progress events


class TaskContext:
    def __init__(self, store, task, attempt, echo=None):
        self.store = store
        self.task = task
        self.attempt = attempt
        self.lang = task["lang"]
        self.job_id = task["job_id"]
        self.span_id = attempt["span_id"]
        self.dir = Path(attempt["dir"])
        self.outdir = self.dir / "outputs"
        self._tmpdir = None
        self._last_progress = 0.0
        self.echo = echo

        def emit(level, type_, message, data):
            store.event(task["job_id"], type_, message, data, level, task["id"], attempt["id"])
            if echo and type_ == "log":
                echo(task, level, message)

        self.log = EventLog(self.dir / "events.jsonl", emit)

    @property
    def tmpdir(self):
        if self._tmpdir is None:
            self._tmpdir = self.dir / "tmp"
            self._tmpdir.mkdir(parents=True, exist_ok=True)
        return self._tmpdir

    def output(self, filename):
        self.outdir.mkdir(parents=True, exist_ok=True)
        return self.outdir / filename

    def progress(self, done, total):
        now = time.monotonic()
        if now - self._last_progress < PROGRESS_EVERY and done != total:
            return
        self._last_progress = now
        self.store.progress(self.task, self.attempt["id"], done, total)
        if self.echo:
            self.echo(self.task, "progress", f"{done}/{total}")

    def set_job_title(self, title):
        self.store.set_title(self.job_id, title)

    def cleanup(self):
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
