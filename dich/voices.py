# Finding the stretch of a recording to clone a voice from: 3–8s of one speaker talking, without music.
#
# Timing alone (subtitle cues) often lands on a stretch that is half silence or under music, and the clone
# comes out poor. So every candidate window is scored on the audio itself: how much of it is voiced (a
# level-based VAD against the track's own loud end), how steady the level is, how close its length is to
# IDEAL_SECONDS, and, for the best CHECKED of them, how much of its energy is in the speech band (music and
# noise spread wider). Without cues the windows come from the VAD: runs of speech, split at their quietest
# moment when longer than the clone limit, so a window never starts or ends inside a word.
import hashlib
from functools import lru_cache
from pathlib import Path

from dich.media import load_audio, loudness

REF_SECONDS = (3.0, 8.0)   # VieNeu clones from a 3–8s clip
REF_MAX_GAP = 0.4          # cues further apart than this are not one run of speech
REF_SAMPLE_RATE = 24000
VAD_HOP = 0.02             # seconds per level frame
VAD_FLOOR = -50.0          # dBFS: quieter than this is never speech
VAD_RANGE = 35.0           # dB below the loud end of the track still counted as speech
VAD_MIN_SECONDS = 0.3      # a trimmed span shorter than this is not trusted: keep the cue timing
WORD_GAP = 0.15            # a pause shorter than this is inside a phrase
PHRASE_GAP = 0.5           # phrases further apart than this are not one window
IDEAL_SECONDS = 6.0
INTRO = 0.1                # the first 10% (often music) scores less
CHECKED = 20               # windows whose spectrum is checked
BAND = (100.0, 3800.0)     # Hz: the speech band (voice fundamentals to the upper formants)
BAND_RATE = 16000


@lru_cache(maxsize=8)
def _levels(path, _mtime):
    return loudness(path, VAD_HOP)


def levels_of(path):
    """RMS level (dBFS) of every VAD_HOP of path; the last few files are kept (a preview, then the dub)."""
    path = Path(path).resolve()
    return _levels(str(path), path.stat().st_mtime_ns)


class Voiced:
    """Which VAD_HOP frames of a speech-only track hold voice, by level against the track's own loud end."""

    def __init__(self, levels):
        import numpy as np

        levels = np.asarray(levels, dtype=np.float32)
        threshold = max(VAD_FLOOR, float(np.percentile(levels, 95)) - VAD_RANGE) if len(levels) else 0.0
        self.levels = levels
        self.frames = levels > threshold

    @classmethod
    def of(cls, path):
        return cls(levels_of(path))

    def trim(self, start, end):
        """(start, end) narrowed to the first and last voiced frame inside it; unchanged when that leaves
        less than VAD_MIN_SECONDS (silence, or a blip: the timing is safer than the guess)."""
        import numpy as np

        first, last = int(start / VAD_HOP), min(len(self.frames), int(np.ceil(end / VAD_HOP)))
        voiced = np.flatnonzero(self.frames[first:last]) if last > first else []
        if len(voiced) == 0:
            return start, end
        span = (max(start, (first + voiced[0]) * VAD_HOP), min(end, (first + voiced[-1] + 1) * VAD_HOP))
        return span if span[1] - span[0] >= VAD_MIN_SECONDS else (start, end)

    def segments(self, max_seconds=REF_SECONDS[1]):
        """[(start, end)] seconds of runs of speech (pauses under WORD_GAP kept inside), each split at its
        quietest frame until no longer than max_seconds."""
        import numpy as np

        voiced = np.flatnonzero(self.frames)
        if len(voiced) == 0:
            return []
        breaks = np.flatnonzero(np.diff(voiced) > WORD_GAP / VAD_HOP + 1)
        runs = zip(np.concatenate([[voiced[0]], voiced[breaks + 1]]), np.concatenate([voiced[breaks], [voiced[-1]]]) + 1)
        limit, low = int(max_seconds / VAD_HOP), int(REF_SECONDS[0] * 0.75 / VAD_HOP)
        out = []
        for first, last in runs:
            while last - first > limit:
                cut = first + low + int(np.argmin(self.levels[first + low:first + limit]))
                out.append((first, cut))
                first = cut
            out.append((first, last))
        return [(a * VAD_HOP, b * VAD_HOP) for a, b in out]


def windows(spans, min_seconds=REF_SECONDS[0], max_seconds=REF_SECONDS[1], max_gap=REF_MAX_GAP):
    """[(start, end)]: for each span, the longest run of back-to-back spans from it that fits in
    max_seconds (a single longer span is cut), when at least min_seconds long."""
    spans = [s for s in spans if s[1] > s[0]]
    out = []
    for first in range(len(spans)):
        start, end = spans[first]
        for last in range(first, len(spans)):
            if last > first and spans[last][0] - spans[last - 1][1] > max_gap:
                break
            if spans[last][1] - start > max_seconds:
                break
            end = spans[last][1]
        length = min(end - start, max_seconds)
        if length >= min_seconds:
            out.append((start, start + length))
    return out


def band_ratio(media, start, end):
    """Share of the window's energy in the speech BAND, 0–1 (0.5 when it can't be read)."""
    import numpy as np

    try:
        audio = np.frombuffer(load_audio(media, BAND_RATE, start, end - start), dtype=np.float32)
    except Exception:
        return 0.5
    size = 512
    count = len(audio) // size
    if count == 0:
        return 0.5
    power = np.abs(np.fft.rfft(audio[:count * size].reshape(count, size) * np.hanning(size), axis=1)) ** 2
    freqs = np.fft.rfftfreq(size, 1 / BAND_RATE)
    total = float(power.sum())
    return float(power[:, (freqs >= BAND[0]) & (freqs <= BAND[1])].sum()) / total if total > 0 else 0.0


def score(voiced, start, end, duration):
    """0–1 from the levels alone: voiced share first, then steadiness and length; less in the intro."""
    import numpy as np

    first, last = int(start / VAD_HOP), max(int(start / VAD_HOP) + 1, int(end / VAD_HOP))
    frames = voiced.frames[first:last]
    if len(frames) == 0:
        return 0.0
    share = float(frames.mean())
    loud = voiced.levels[first:last][frames]
    steady = float(np.clip(1 - np.std(loud) / 20, 0, 1)) if len(loud) > 1 else 0.0
    length = float(np.clip(1 - abs((end - start) - IDEAL_SECONDS) / IDEAL_SECONDS, 0, 1))
    value = 0.6 * share + 0.2 * steady + 0.2 * length
    return value * (0.8 if start < duration * INTRO else 1.0)


def candidates(media, cues=None, n=5, levels=None):
    """[{"start", "end", "score", "speech"}] of the n best windows of media to clone a voice from, not
    overlapping, best first. cues: [(start, end, …)] or objects with .start/.end to take windows from
    (else from the VAD). A recording 3–8s long is one window, all of it."""
    voiced = Voiced(levels_of(media) if levels is None else levels)
    duration = len(voiced.frames) * VAD_HOP
    if duration < REF_SECONDS[0]:
        return []
    if duration <= REF_SECONDS[1]:
        spans = [(0.0, duration)]
    elif cues:
        spans = windows([(c.start, c.end) if hasattr(c, "start") else (c[0], c[1]) for c in cues])
    else:
        spans = windows(voiced.segments(), max_gap=PHRASE_GAP)
    scored = sorted(((score(voiced, a, b, duration), a, b) for a, b in spans), key=lambda s: -s[0])[:CHECKED]
    ranked = []
    for value, start, end in scored:
        speech = band_ratio(media, start, end)
        ranked.append({"start": round(float(start), 2), "end": round(float(end), 2),
                       "score": round(value * (0.5 + 0.5 * speech), 3),
                       "speech": round(speech, 3)})
    ranked.sort(key=lambda c: -c["score"])
    picked = []
    for candidate in ranked:
        if all(candidate["end"] <= p["start"] or candidate["start"] >= p["end"] for p in picked):
            picked.append(candidate)
            if len(picked) == n:
                break
    return picked


def cut(media, start, end, dest):
    """Writes start–end of media to dest as a REF_SAMPLE_RATE wav; returns dest."""
    import numpy as np
    import soundfile as sf

    audio = np.frombuffer(load_audio(media, REF_SAMPLE_RATE, start, end - start), dtype=np.float32)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dest, audio, REF_SAMPLE_RATE)
    return dest


def clip_name(media, start, end):
    """A file name for start–end of media, the same while the file is unchanged."""
    media = Path(media)
    key = hashlib.sha1(f"{media.resolve()}\n{media.stat().st_mtime_ns}\n{start:.3f}\n{end:.3f}".encode()).hexdigest()
    return f"{media.stem[:40]}-{key[:12]}.wav"
