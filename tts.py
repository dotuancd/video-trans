# Vietnamese dubbing: turn .vi.srt cues into one audio track with VieNeu-TTS v3 Turbo
# (local, ONNX on CPU, 48 kHz, preset voices or instant voice cloning from a 3–8s clip).
#
# Cues are joined into sentences (reads more naturally than cue fragments), each sentence
# is synthesized once and placed at its first cue's start. Vietnamese speech is often
# longer than the English slot, so a sentence may run into the silence before the next
# one, then is sped up (atempo, up to max_speed), and only as a last resort pushes the
# next sentence later.
#
# The default voice ("source") is cloned from the video itself: a 3–8s run of speech picked
# from the subtitle timings is cut out and used as the reference clip.
import hashlib
import threading
from pathlib import Path

import av
import numpy as np
import soundfile as sf

import settings
from translation import clean, group_sentences

SAMPLE_RATE = 48000
DEFAULT_VOICE = settings.VOICE   # preset used when there is no video to clone from
SOURCE_VOICE = "source"          # voice= value: clone the speaker of the video
REF_SECONDS = (3.0, 8.0)         # VieNeu clones from a 3–8s clip
REF_MAX_GAP = 0.4                # cues further apart than this are not one run of speech
REF_SAMPLE_RATE = 24000
# Cloning from the English original: True also feeds the clip's acoustic codes to the model,
# False the speaker embedding only (less of the English prosody carried over).
REF_USE_CODES = True
MAX_SENTENCE_CHARS = 200   # shorter than translation's 400 so long runs keep closer to the video
MAX_GAP = 1.0              # a pause this long (seconds) also ends a sentence
CACHE_DIR = settings.CACHE_DIR / "tts"
REF_DIR = settings.CACHE_DIR / "ref"
INFER_LOCK = threading.Lock()   # the web UI previews voices while a dub may be running


def group_cues(subtitles):
    """[(start_seconds, end_seconds, text)] for runs of cues that form one sentence."""
    groups = []
    for group in group_sentences(subtitles, MAX_SENTENCE_CHARS, MAX_GAP):
        text = clean(" ".join(subtitles[i].content for i in group))
        if text:
            groups.append((subtitles[group[0]].start.total_seconds(), subtitles[group[-1]].end.total_seconds(), text))
    return groups


def pick_reference(cues, min_seconds=REF_SECONDS[0], max_seconds=REF_SECONDS[1], max_gap=REF_MAX_GAP):
    """(start, end) seconds of the longest run of back-to-back cues that fits in max_seconds, at least
    min_seconds long; ties go to the earliest run past the first 10% (intros are often music). None if none."""
    spans = [(c.start.total_seconds(), c.end.total_seconds()) for c in cues if c.end > c.start]
    if not spans:
        return None
    skip = spans[-1][1] * 0.1
    best, best_key = None, None
    for first in range(len(spans)):
        start, end = spans[first][0], spans[first][1]
        for last in range(first, len(spans)):
            if last > first and spans[last][0] - spans[last - 1][1] > max_gap:
                break
            if spans[last][1] - start > max_seconds:
                break
            end = spans[last][1]
        length = min(end - start, max_seconds)   # a single cue may be longer: cut it
        if length < min_seconds:
            continue
        key = (round(length, 1), start >= skip, -start)
        if best_key is None or key > best_key:
            best, best_key = (start, start + length), key
    return best


def reference_clip(media, cues):
    """(wav path, (start, end)) of the speech in media to clone the voice from, or None.
    Cut once and kept in REF_DIR."""
    window = pick_reference(cues)
    if window is None:
        return None
    media = Path(media)
    start, end = window
    key = hashlib.sha1(f"{media.resolve()}\n{media.stat().st_mtime_ns}\n{start:.3f}\n{end:.3f}".encode()).hexdigest()
    clip = REF_DIR / f"{media.stem[:40]}-{key[:12]}.wav"
    if not clip.exists():
        from dich import load_audio
        audio = np.frombuffer(load_audio(media, REF_SAMPLE_RATE, start, end - start), dtype=np.float32)
        REF_DIR.mkdir(parents=True, exist_ok=True)
        sf.write(clip, audio, REF_SAMPLE_RATE)
    return clip, window


def overlapping_text(cues, window):
    """The sentence (as group_cues joins them) that overlaps window most, for a preview that reads
    the same moment the voice was cloned from; None when nothing overlaps."""
    best, best_overlap = None, 0.0
    for start, end, text in group_cues(cues):
        overlap = min(end, window[1]) - max(start, window[0])
        if overlap > best_overlap:
            best, best_overlap = text, overlap
    return best


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
    def __init__(self, voice=DEFAULT_VOICE, ref_audio=None, precision="fp32", tts=None, use_ref_codes=REF_USE_CODES):
        if tts is None:
            from vieneu import Vieneu
            tts = Vieneu(precision=precision)
        self.tts = tts
        self.voice = voice
        self.voice_key = voice
        if ref_audio:
            ref_audio = Path(ref_audio)
            self.voice = self.tts.add_voice(f"clone:{ref_audio.stem}", ref_audio, use_ref_codes=use_ref_codes)
            self.voice_key = hashlib.sha1(ref_audio.read_bytes() + (b"" if use_ref_codes else b"emb")).hexdigest()

    def synthesize(self, text):
        key = hashlib.sha1(f"{self.voice_key}\n{text}".encode()).hexdigest()
        cached = CACHE_DIR / f"{key}.wav"
        if cached.exists():
            audio, _ = sf.read(cached, dtype="float32")
            return audio
        with INFER_LOCK:
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


def preset_voices(tts=None):
    """[(label, name)] of the preset voices; pass name as Dubber(voice=...)."""
    if tts is None:
        from vieneu import Vieneu
        tts = Vieneu()
    return tts.list_preset_voices()
