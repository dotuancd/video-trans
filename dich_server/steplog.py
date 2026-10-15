# Per-step log files for the web UI: logs/<job id>/<step id>.log under the data folder, one section
# per attempt. While a worker thread runs a step, what that thread prints (stdout/stderr) and logs
# (logging, warnings) goes to the step's file as well as to the terminal; other threads are untouched,
# so the "cpu" and "net" lanes running side by side keep separate logs.
#
# Not captured: output written below Python (C libraries writing to fd 2) and threads a step starts
# itself (the translators' thread pools) — those still reach the terminal only.
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

MAX_SIZE = 2 << 20      # at the start of an attempt a bigger file keeps its last KEEP bytes
KEEP = 1 << 20
MAX_ATTEMPT = 4 << 20   # an attempt writing more than this stops being logged
MAX_READ = 256 << 10
EMIT_EVERY = 0.3        # seconds between live updates

_writers = {}           # thread ident → Writer of the step that thread runs
_install_lock = threading.Lock()
_installed = False


def _writer():
    return _writers.get(threading.get_ident())


class _Tee:
    """sys.stdout/sys.stderr replacement: the original stream, plus the step log of the calling thread."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        writer = _writer()
        if writer:
            writer.write(text)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):    # isatty, fileno, encoding… as the original
        return getattr(self.stream, name)


class _Handler(logging.Handler):
    """Log records of a step's thread go to its log; others print as Python would without a handler
    (warnings and up, to stderr)."""

    def emit(self, record):
        try:
            text = self.format(record) + "\n"
        except Exception:
            self.handleError(record)
            return
        writer = _writer()
        if writer:
            writer.write(text)
        if writer or record.levelno >= logging.WARNING:
            sys.__stderr__.write(text)


def install():
    """Route output to step logs; idempotent, and again after someone replaced sys.stdout/sys.stderr."""
    global _installed
    with _install_lock:
        if not isinstance(sys.stdout, _Tee):
            sys.stdout = _Tee(sys.stdout)
        if not isinstance(sys.stderr, _Tee):
            sys.stderr = _Tee(sys.stderr)
        if _installed:
            return
        handler = _Handler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger("dich").setLevel(logging.INFO)
        logging.captureWarnings(True)
        _installed = True


def clean(text):
    """Text as it would look in a terminal, line by line: of a line rewritten with \\r, the last version."""
    return "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))


class Writer:
    """Appends to one step's log file; on_text(offset, text) gets each new piece, at most every EMIT_EVERY s."""

    def __init__(self, path, on_text=None):
        self.path = path
        self.on_text = on_text
        self.lock = threading.RLock()
        self.partial = ""           # an unfinished line (e.g. a progress bar redrawn with \r)
        self.pending = ""           # written but not yet sent to on_text
        self.pending_at = 0         # file offset of pending
        self.written = 0
        self.last_emit = 0.0
        self.timer = None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_SIZE:
            data = path.read_bytes()[-KEEP:]
            cut = data.find(b"\n") + 1
            path.write_bytes(b"[...]\n" + data[cut:])
        self.file = open(path, "ab")
        self.offset = self.file.tell()

    def write(self, text):
        with self.lock:
            if self.file is None:
                return
            lines = (self.partial + text).split("\n")
            self.partial = lines.pop()
            if lines:
                self._append(clean("\n".join(lines)) + "\n")

    def line(self, text):
        self.write(f"{text}\n")

    def _append(self, text):
        if self.written > MAX_ATTEMPT:
            return
        data = text.encode("utf-8", "replace")
        self.written += len(data)
        if self.written > MAX_ATTEMPT:
            data = "[... log quá dài, phần sau bị bỏ]\n".encode()
            text = data.decode()
        self.file.write(data)
        self.file.flush()
        if not self.pending:
            self.pending_at = self.offset
        self.pending += text
        self.offset += len(data)
        self._schedule()

    def _schedule(self):
        if not self.on_text or self.timer:
            return
        wait = self.last_emit + EMIT_EVERY - time.monotonic()
        if wait <= 0:
            self._emit()
        else:
            self.timer = threading.Timer(wait, self._emit)
            self.timer.daemon = True
            self.timer.start()

    def _emit(self):
        with self.lock:
            self.timer = None
            if not self.pending:
                return
            offset, text, self.pending = self.pending_at, self.pending, ""
            self.last_emit = time.monotonic()
        self.on_text(offset, text)

    def close(self):
        with self.lock:
            if self.file is None:
                return
            if self.partial:
                self._append(clean(self.partial) + "\n")
                self.partial = ""
            self.file.close()
            self.file = None
            if self.timer:
                self.timer.cancel()
                self.timer = None
        if self.on_text:
            self._emit()


class StepLogs:
    """The log files under folder. on_text(step, offset, text) is called with each new piece of a log."""

    def __init__(self, folder, on_text=None):
        self.folder = Path(folder)
        self.on_text = on_text

    def path(self, job_id, step_id):
        return self.folder / str(job_id) / f"{step_id}.log"

    def open(self, step):
        """A Writer for the step's next attempt, receiving the calling thread's output until close()."""
        install()
        on_text = (lambda offset, text: self.on_text(step, offset, text)) if self.on_text else None
        writer = Writer(self.path(step["job_id"], step["id"]), on_text)
        writer.line(f"── Lần {step['attempt']} · {time.strftime('%Y-%m-%d %H:%M:%S')} ──")
        _writers[threading.get_ident()] = writer
        return writer

    def close(self, writer):
        if _writers.get(threading.get_ident()) is writer:
            del _writers[threading.get_ident()]
        writer.close()

    def read(self, job_id, step_id, offset=0):
        """(text from offset on — at most the last MAX_READ bytes —, offset it starts at, file size)."""
        path = self.path(job_id, step_id)
        if not path.exists():
            return "", 0, 0
        with open(path, "rb") as file:
            size = file.seek(0, 2)
            wanted = min(max(offset, 0), size)
            offset = max(wanted, size - MAX_READ)
            file.seek(offset)
            data = file.read(size - offset)
        if offset > wanted:     # clipped to the end of a long log: drop the partial first line
            cut = data.find(b"\n") + 1
            data, offset = data[cut:], offset + cut
        return data.decode("utf-8", "replace"), offset, size

    def remove(self, job_id):
        shutil.rmtree(self.folder / str(job_id), ignore_errors=True)

    def prune(self, job_ids):
        """Remove the logs of jobs not in job_ids."""
        if not self.folder.is_dir():
            return
        keep = {str(job_id) for job_id in job_ids}
        for folder in self.folder.iterdir():
            if folder.is_dir() and folder.name not in keep:
                shutil.rmtree(folder, ignore_errors=True)
