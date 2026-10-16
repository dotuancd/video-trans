# Results of the finished pieces of a task (translated chunks…), kept across its attempts so a run that was
# stopped, crashed or failed goes on from where it was instead of starting over. Lives in the task folder
# (<job>/<task_key>/checkpoint/), next to the attempt folders:
#
#   fingerprint     the attempt fingerprint it belongs to: other params or inputs → start over
#   pieces.jsonl    {"key": …, "value": …} per finished piece, appended as they finish
#
# Removed when the task is done (its result is then the artifact, reused through the fingerprint cache).
import json
import shutil
import threading
from pathlib import Path

PIECES = "pieces.jsonl"


class Checkpoint:
    def __init__(self, folder, fingerprint):
        self.dir = Path(folder)
        self.fingerprint = fingerprint or ""
        self.lock = threading.Lock()
        self.pieces = {}
        marker = self.dir / "fingerprint"
        try:
            same = marker.read_text(encoding="utf-8") == self.fingerprint
        except OSError:
            same = False
        if same:
            self._load()
        else:
            shutil.rmtree(self.dir, ignore_errors=True)
        self.resumed = len(self.pieces)   # pieces found from an earlier attempt

    def _load(self):
        try:
            lines = (self.dir / PIECES).read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                row = json.loads(line)
                self.pieces[row["key"]] = row["value"]
            except (ValueError, KeyError, TypeError):
                continue   # the last line of a run killed mid-write

    def get(self, key):
        with self.lock:
            return self.pieces.get(key)

    def put(self, key, value):
        with self.lock:
            if not self.dir.exists():
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / "fingerprint").write_text(self.fingerprint, encoding="utf-8")
            with (self.dir / PIECES).open("a", encoding="utf-8") as file:
                file.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")
            self.pieces[key] = value

    def __len__(self):
        return len(self.pieces)

    def clear(self):
        with self.lock:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.pieces.clear()
