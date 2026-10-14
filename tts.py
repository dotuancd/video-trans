# Vietnamese dubbing: turn .vi.srt cues into one audio track with VieNeu-TTS v3 Turbo
# (local, ONNX on CPU, 48 kHz, preset voices or instant voice cloning from a 3–8s clip).
#
# Cues are joined into sentences (reads more naturally than cue fragments), each sentence
# is synthesized once and placed at its first cue's start. Vietnamese speech is often
# longer than the English slot, so a sentence may run into the silence before the next
# one, then is sped up (atempo, up to max_speed), and only as a last resort pushes the
# next sentence later.
import hashlib
from pathlib import Path

import av
import numpy as np
import soundfile as sf

from translation import SENTENCE_END, clean

SAMPLE_RATE = 48000
DEFAULT_VOICE = "Phạm Tuyên"
MAX_SENTENCE_CHARS = 200   # shorter than translation's 400 so long runs keep closer to the video
MAX_GAP = 1.0              # a pause this long (seconds) also ends a sentence
CACHE_DIR = Path.home() / ".cache" / "dich-video" / "tts"


def group_cues(subtitles):
    """[(start_seconds, end_seconds, text)] for runs of cues that form one sentence."""
    groups, current = [], []

    def flush():
        if current:
            text = clean(" ".join(cue.content for cue in current))
            if text:
                groups.append((current[0].start.total_seconds(), current[-1].end.total_seconds(), text))
            current.clear()

    for cue in subtitles:
        if current and (cue.start - current[-1].end).total_seconds() > MAX_GAP:
            flush()
        current.append(cue)
        text = clean(cue.content)
        if text.endswith(SENTENCE_END) or sum(len(c.content) for c in current) > MAX_SENTENCE_CHARS:
            flush()
    flush()
    return groups


def change_speed(audio, speed):
    """Speed audio up by `speed` without changing pitch (ffmpeg atempo via PyAV)."""
    if speed <= 1.001:
        return audio
    graph = av.filter.Graph()
    source = graph.add_abuffer(format="flt", sample_rate=SAMPLE_RATE, layout="mono")
    tempo = graph.add("atempo", str(speed))
    sink = graph.add("abuffersink")
    source.link_to(tempo)
    tempo.link_to(sink)
    graph.configure()

    frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1).astype(np.float32), format="flt", layout="mono")
    frame.sample_rate = SAMPLE_RATE
    frame.pts = 0
    graph.push(frame)
    graph.push(None)
    chunks = []
    while True:
        try:
            chunks.append(graph.pull().to_ndarray().ravel())
        except (av.BlockingIOError, av.EOFError):
            break
    return np.concatenate(chunks) if chunks else audio


class Dubber:
    def __init__(self, voice=DEFAULT_VOICE, ref_audio=None, precision="fp32"):
        from vieneu import Vieneu

        self.tts = Vieneu(precision=precision)
        self.voice = voice
        self.voice_key = voice
        if ref_audio:
            ref_audio = Path(ref_audio)
            self.voice = self.tts.add_voice(f"clone:{ref_audio.stem}", ref_audio)
            self.voice_key = hashlib.sha1(ref_audio.read_bytes()).hexdigest()

    def synthesize(self, text):
        key = hashlib.sha1(f"{self.voice_key}\n{text}".encode()).hexdigest()
        cached = CACHE_DIR / f"{key}.wav"
        if cached.exists():
            audio, _ = sf.read(cached, dtype="float32")
            return audio
        audio = self.tts.infer(text, voice=self.voice).astype(np.float32)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sf.write(cached, audio, SAMPLE_RATE)
        return audio

    def dub(self, subtitles, max_speed=1.3, progress=None):
        """Returns (track as float32 48 kHz mono, stats dict)."""
        groups = group_cues(subtitles)
        clips, cursor = [], 0.0
        sped_up, late, max_late = 0, 0, 0.0
        for index, (start, _end, text) in enumerate(groups):
            audio = self.synthesize(text)
            begin = max(start, cursor)
            next_start = groups[index + 1][0] if index + 1 < len(groups) else float("inf")
            available = next_start - begin
            duration = len(audio) / SAMPLE_RATE
            if duration > available > 0:
                speed = min(max_speed, duration / available)
                audio = change_speed(audio, speed)
                sped_up += 1
            clips.append((begin, audio))
            cursor = begin + len(audio) / SAMPLE_RATE
            if begin > start + 0.05:
                late += 1
                max_late = max(max_late, begin - start)
            if progress:
                progress(index + 1, len(groups))

        end = max((begin + len(audio) / SAMPLE_RATE for begin, audio in clips), default=0.0)
        track = np.zeros(int(end * SAMPLE_RATE) + SAMPLE_RATE // 2, dtype=np.float32)
        for begin, audio in clips:
            offset = int(begin * SAMPLE_RATE)
            track[offset:offset + len(audio)] += audio
        np.clip(track, -1.0, 1.0, out=track)
        return track, {"sentences": len(groups), "sped_up": sped_up, "late": late, "max_late": max_late}


def save_wav(track, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, track, SAMPLE_RATE)
