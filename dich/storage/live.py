# Audio a task makes available while it still runs (the dub, sentence by sentence), so the UI can play
# the result before it is done. Kept in <attempt dir>/live/: numbered back-to-back WAV pieces and an
# index.json the API serves:
#
#   {"chunks": [{"n": 0, "start": 0.0, "end": 8.4}, …], "ready_until": 8.4, "duration": 312.0, "finished": false}
#
# duration is the expected length (end of the last cue), for "ready up to 1:20 of 5:12".
import json
from pathlib import Path

LIVE_DIR = "live"


def chunk_name(n):
    return f"{n:04d}.wav"


class LiveAudio:
    def __init__(self, folder, sample_rate, duration=None):
        self.dir = Path(folder)
        self.sample_rate = sample_rate
        self.index = {"chunks": [], "ready_until": 0.0, "duration": duration, "finished": False}
        self.dir.mkdir(parents=True, exist_ok=True)
        for old in self.dir.glob("*.wav"):   # a rerun in the same attempt dir
            old.unlink()
        self._save()

    def add(self, start, audio):
        import soundfile as sf

        n = len(self.index["chunks"])
        end = start + len(audio) / self.sample_rate
        sf.write(self.dir / chunk_name(n), audio, self.sample_rate, subtype="PCM_16")
        self.index["chunks"].append({"n": n, "start": round(start, 3), "end": round(end, 3)})
        self.index["ready_until"] = round(end, 3)
        self._save()

    def finish(self):
        self.index["finished"] = True
        self._save()

    def _save(self):
        tmp = self.dir / "index.json.tmp"
        tmp.write_text(json.dumps(self.index), encoding="utf-8")
        tmp.replace(self.dir / "index.json")   # readers never see a half-written index


def read_live(attempt_dir):
    """The index of an attempt's live audio, or None."""
    path = Path(attempt_dir) / LIVE_DIR / "index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def live_chunk(attempt_dir, n):
    """Path of chunk n, or None."""
    path = Path(attempt_dir) / LIVE_DIR / chunk_name(n)
    return path if n >= 0 and path.is_file() else None
