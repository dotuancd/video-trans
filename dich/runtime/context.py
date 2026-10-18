# What a driver gets besides its inputs: where to write, the pipeline log, progress.
import json
import shutil
import time
from pathlib import Path

from dich.logging.events import EventLog
from dich.storage.checkpoint import Checkpoint

PROGRESS_EVERY = 0.5   # seconds between progress events
CHECKPOINT_DIR = "checkpoint"   # in the task folder, shared by its attempts


class TaskContext:
    def __init__(self, store, task, attempt, echo=None, fresh=False):
        self.store = store
        self.task = task
        self.attempt = attempt
        self.lang = task["lang"]
        self.job_id = task["job_id"]
        self.span_id = attempt["span_id"]
        self.dir = Path(attempt["dir"])
        self.outdir = self.dir / "outputs"
        self._tmpdir = None
        self._checkpoint = None
        self._last_progress = 0.0
        self.echo = echo
        self.fresh = fresh   # drivers skip their own caches too (TTS sentences…)

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

    @property
    def checkpoint(self):
        """Pieces finished by earlier attempts of this task with the same fingerprint (see storage/checkpoint)."""
        if self._checkpoint is None:
            self._checkpoint = Checkpoint(self.dir.parent / CHECKPOINT_DIR, self.attempt.get("fingerprint"))
        return self._checkpoint

    def drop_checkpoint(self):
        shutil.rmtree(self.dir.parent / CHECKPOINT_DIR, ignore_errors=True)

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

    def job_info(self):
        """The job's title and target languages: context for a driver that writes about the whole video."""
        row = self.store.job_row(self.job_id)
        return {"title": row["title"], "langs": json.loads(row["langs"])} if row else {"title": None, "langs": []}

    def set_job_title(self, title):
        self.store.set_auto_title(self.job_id, title)

    def cleanup(self):
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
