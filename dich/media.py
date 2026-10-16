# Decoding media in-process with PyAV (bundles the FFmpeg libraries): no ffmpeg binary needed to read.
import hashlib
import itertools
import json
from pathlib import Path

ASR_SAMPLE_RATE = 16000
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + AUDIO_EXTENSIONS
SUBTITLE_EXTENSIONS = (".srt", ".vtt")


def is_media(name):
    name = name.lower()
    # skip our own exports: <name>.dub.<lang>.<ext> and <name>.<lang>.wav
    stem = Path(name).with_suffix("")
    return name.endswith(MEDIA_EXTENSIONS) and ".dub" not in stem.suffixes[-2:]


def is_subtitle(name):
    return name.lower().endswith(SUBTITLE_EXTENSIONS)


def load_audio(path, rate=ASR_SAMPLE_RATE, start=None, duration=None):
    """Mono float32 PCM bytes at rate: the whole file, or duration seconds from start."""
    import av
    import numpy as np

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError("no audio stream")
        stream = container.streams.audio[0]
        if start:
            container.seek(int(start * av.time_base))   # lands on a frame at or before start
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        chunks, offset = [], None   # offset: seconds decoded before start, dropped below
        for frame in container.decode(stream):
            if offset is None:
                offset = max(0.0, (start or 0) - (frame.time or 0.0))
            chunks += [out.to_ndarray().ravel() for out in resampler.resample(frame)]
            if duration and sum(map(len, chunks)) >= (offset + duration) * rate:
                break
        chunks += [out.to_ndarray().ravel() for out in resampler.resample(None)]
    if not chunks:
        raise RuntimeError("no audio decoded")
    audio = np.concatenate(chunks).astype(np.float32)
    if start or duration:
        begin = int((offset or 0) * rate)
        audio = audio[begin:begin + int(duration * rate) if duration else None]
    return audio.tobytes()


def loudness(path, hop=0.02, rate=ASR_SAMPLE_RATE):
    """float32 array of the RMS level (dBFS) of every hop seconds of the file, decoded as a stream."""
    import av
    import numpy as np

    size, levels, rest = int(hop * rate), [], np.zeros(0, dtype=np.float32)
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError("no audio stream")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        frames = itertools.chain(container.decode(container.streams.audio[0]), [None])   # None flushes
        for out in (out for frame in frames for out in resampler.resample(frame)):
            rest = np.concatenate([rest, out.to_ndarray().ravel()])
            whole = len(rest) // size * size
            if whole:
                blocks = rest[:whole].reshape(-1, size)
                levels.append(10 * np.log10(np.mean(blocks * blocks, axis=1) + 1e-10))
                rest = rest[whole:]
    return np.concatenate(levels).astype(np.float32) if levels else np.zeros(0, dtype=np.float32)


def has_video(path):
    import av

    try:
        with av.open(str(path)) as container:
            return any(s.type == "video" and not int(s.disposition) & 0x400 for s in container.streams)   # not cover art
    except Exception:
        return False


HASH_CACHE = {}


def sha256(path):
    """Hex sha256 of a file; large files are remembered by (path, size, mtime) for this process."""
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in HASH_CACHE:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1 << 20), b""):
                digest.update(block)
        HASH_CACHE[key] = digest.hexdigest()
    return HASH_CACHE[key]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
