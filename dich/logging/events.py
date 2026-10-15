# The pipeline log, for the user: structured events ("Đang tải model", "Dịch 42/300 câu") written by
# drivers through ctx.log / ctx.progress. It goes to events.jsonl of the attempt and to the events table
# (what the UI streams). Tracebacks and library output never go here: see system.py.
import json
import time


class UserError(Exception):
    """An error the user can fix (bad URL, missing file): shown as is. Any other exception is shown as a
    generic message with the trace code, its traceback goes to the system log."""


class EventLog:
    def __init__(self, path=None, emit=None):
        """emit(level, type, message, data) also gets every event (the store, the CLI)."""
        self.path = path
        self.emit = emit

    def _write(self, level, type_, message, data):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"ts": time.time(), "level": level, "type": type_, "message": message,
                                       **({"data": data} if data else {})}, ensure_ascii=False) + "\n")
        if self.emit:
            self.emit(level, type_, message, data or None)

    def info(self, message, **data):
        self._write("info", "log", message, data)

    def warn(self, message, **data):
        self._write("warn", "log", message, data)

    def error(self, message, **data):
        self._write("error", "log", message, data)

    def progress(self, done, total):
        self._write("info", "progress", None, {"done": done, "total": total})


def read_events(path, limit=2000):
    if not path or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in lines if line.strip()]
