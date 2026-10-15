# Vietnamese dubbing with VieNeu-TTS v3 Turbo (local, ONNX on CPU, 48 kHz, preset voices or instant voice
# cloning from a 3–8s clip).
#
# Cues are joined into sentences (reads more naturally than cue fragments), each sentence is synthesized
# once and placed at its first cue's start. Vietnamese speech is often longer than the English slot, so a
# sentence may run into the silence before the next one, then is sped up (atempo, up to max_speed), and
# only as a last resort pushes the next sentence later. A sentence much shorter than the original speech
# it replaces is slowed down (down to min_speed) to fill it, never into the silence after it.
#
# The default voice ("source") is cloned from the video itself: a 3–8s run of speech picked from the
# cue timings is cut out and used as the reference clip.
import hashlib
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from dich import settings
from dich.drivers import Driver, UserError, module_missing, register
from dich.drivers.translate.common import clean, group_sentences
from dich.formats.transcript import Transcript
from dich.media import load_audio

SAMPLE_RATE = 48000
DEFAULT_VOICE = settings.VOICE   # preset used when there is nothing to clone from
SOURCE_VOICE = "source"          # voice= value: clone the speaker of the video
REF_SECONDS = (3.0, 8.0)         # VieNeu clones from a 3–8s clip
REF_MAX_GAP = 0.4                # cues further apart than this are not one run of speech
REF_SAMPLE_RATE = 24000
# Cloning from the English original: True also feeds the clip's acoustic codes to the model,
# False the speaker embedding only (less of the English prosody carried over).
REF_USE_CODES = True
MAX_SENTENCE_CHARS = 200   # shorter than translation's 400 so long runs keep closer to the video
MAX_GAP = 1.0              # a pause this long (seconds) also ends a sentence
FILL_RATIO = 0.9           # a sentence filling less than this of its original speech is slowed down
CACHE_DIR = settings.CACHE_DIR / "tts"
REF_DIR = settings.CACHE_DIR / "ref"
INFER_LOCK = threading.Lock()


def group_cues(cues):
    """[(start_seconds, end_seconds, text)] for runs of cues that form one sentence."""
    groups = []
    for group in group_sentences(cues, MAX_SENTENCE_CHARS, MAX_GAP):
        text = clean(" ".join(cues[i].text for i in group))
        if text:
            groups.append((cues[group[0]].start, cues[group[-1]].end, text))
    return groups


def pick_reference(cues, min_seconds=REF_SECONDS[0], max_seconds=REF_SECONDS[1], max_gap=REF_MAX_GAP):
    """(start, end) seconds of the longest run of back-to-back cues that fits in max_seconds, at least
    min_seconds long; ties go to the earliest run past the first 10% (intros are often music). None if none."""
    spans = [(c.start, c.end) for c in cues if c.end > c.start]
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
    import numpy as np
    import soundfile as sf

    window = pick_reference(cues)
    if window is None:
        return None
    media = Path(media)
    start, end = window
    key = hashlib.sha1(f"{media.resolve()}\n{media.stat().st_mtime_ns}\n{start:.3f}\n{end:.3f}".encode()).hexdigest()
    clip = REF_DIR / f"{media.stem[:40]}-{key[:12]}.wav"
    if not clip.exists():
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
    """Change the tempo of audio by `speed` (>1 faster, <1 slower) without changing pitch (ffmpeg atempo via PyAV)."""
    import av
    import numpy as np

    if abs(speed - 1) <= 0.001:
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
        import numpy as np
        import soundfile as sf

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

    def dub(self, cues, max_speed=1.3, min_speed=0.85, progress=None):
        """Returns (track as float32 48 kHz mono, stats dict)."""
        import numpy as np

        groups = group_cues(cues)
        clips, cursor = [], 0.0
        sped_up, slowed, late, max_late = 0, 0, 0, 0.0
        for index, (start, end, text) in enumerate(groups):
            audio = self.synthesize(text)
            begin = max(start, cursor)
            next_start = groups[index + 1][0] if index + 1 < len(groups) else float("inf")
            available = next_start - begin
            duration = len(audio) / SAMPLE_RATE
            if duration > available > 0:
                speed = min(max_speed, duration / available)
                audio = change_speed(audio, speed)
                sped_up += 1
            elif min_speed < 1 and begin <= start + 0.05 and duration < (end - begin) * FILL_RATIO:
                audio = change_speed(audio, max(min_speed, duration / (end - begin)))
                slowed += 1
            clips.append((begin, audio))
            cursor = begin + len(audio) / SAMPLE_RATE
            if begin > start + 0.05:
                late += 1
                max_late = max(max_late, begin - start)
            if progress:
                progress(index + 1, len(groups))

        length = max((begin + len(audio) / SAMPLE_RATE for begin, audio in clips), default=0.0)
        track = np.zeros(int(length * SAMPLE_RATE) + SAMPLE_RATE // 2, dtype=np.float32)
        for begin, audio in clips:
            offset = int(begin * SAMPLE_RATE)
            track[offset:offset + len(audio)] += audio
        np.clip(track, -1.0, 1.0, out=track)
        return track, {"sentences": len(groups), "sped_up": sped_up, "slowed": slowed, "late": late, "max_late": max_late}


def save_wav(track, path):
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, track, SAMPLE_RATE)
    return path


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


class Params(BaseModel):
    voice: str = Field(SOURCE_VOICE, title="Giọng đọc", description="'source' = clone giọng người nói trong video, hoặc giọng mẫu")
    ref_audio: str | None = Field(None, title="Clip giọng mẫu", description="Clone giọng từ clip 3–8 giây này")
    int8: bool = Field(False, title="Model int8", description="Model int8: nhanh hơn, có thể kém hơn")
    max_speed: float = Field(1.3, title="Tăng tốc tối đa", ge=1.0, le=2.0, description="Tăng tốc tối đa để vừa khung câu")
    min_speed: float = Field(0.85, title="Giảm tốc tối đa", ge=0.5, le=1.0,
                             description="Đọc chậm lại khi câu ngắn hơn đoạn nói gốc; 1 = tắt")


@register
class VieneuDub(Driver):
    stage = "dub"
    name = "vieneu"
    title = "VieNeu-TTS (local, tiếng Việt)"
    Params = Params
    consumes = {"transcript": "transcript.{lang}", "voice_from": "audio.speech|audio.original?"}
    produces = {"audio": "audio.dub.{lang}"}
    keep = {"audio": "last"}
    langs = {"vi"}

    def __init__(self):
        self.tts = {}        # int8 → Vieneu model, shared by every voice
        self.dubbers = {}

    @classmethod
    def available(cls):
        return module_missing("vieneu") and "Chưa cài lồng tiếng: uv sync --extra tts"

    def dubber(self, voice=None, ref_audio=None, int8=False, log=print):
        if voice == SOURCE_VOICE:   # resolved to a ref_audio clip by the caller; without one, the preset
            voice = None
        key = (voice or DEFAULT_VOICE, str(ref_audio) if ref_audio else None, bool(int8))
        if key not in self.dubbers:
            log(f"Tải model giọng đọc ({key[1] and Path(key[1]).name or key[0]}{', int8' if key[2] else ''})…")
            dubber = Dubber(voice=key[0], ref_audio=key[1], precision="int8" if int8 else "fp32",
                            tts=self.tts.get(key[2]))
            self.tts[key[2]] = dubber.tts
            self.dubbers[key] = dubber
        return self.dubbers[key]

    def voice_for(self, params, media, cues, log):
        """(ref_audio or None, window or None) for params; logs which voice is used."""
        if params.ref_audio:
            if not Path(params.ref_audio).is_file():
                raise UserError(f"Không tìm thấy clip giọng {params.ref_audio}")
            return params.ref_audio, None
        if params.voice != SOURCE_VOICE:
            return None, None
        clip = reference_clip(media, cues) if media else None
        if clip:
            log(f"Giọng gốc, đoạn {clock(clip[1][0])}–{clock(clip[1][1])}")
            return clip
        log(f"Giọng mẫu {DEFAULT_VOICE}: " + ("không có video để clone" if not media else "không có đoạn nói liền 3 giây"))
        return None, None

    def run(self, ctx, inputs, params):
        transcript = Transcript.load(inputs["transcript"].path)
        media = inputs["voice_from"].path if inputs.get("voice_from") else None
        ref_audio, _window = self.voice_for(params, media, transcript.cues, ctx.log.info)
        ctx.log.info(f"Lồng tiếng {len(transcript.cues)} câu")
        dubber = self.dubber(params.voice, ref_audio, params.int8, ctx.log.info)
        track, stats = dubber.dub(transcript.cues, max_speed=params.max_speed, min_speed=params.min_speed, progress=ctx.progress)
        ctx.log.info(f"{stats['sentences']} câu, {len(track) / SAMPLE_RATE / 60:.1f} phút, {stats['sped_up']} câu tăng tốc, {stats['slowed']} câu giảm tốc, "
                     f"{stats['late']} câu trễ (tối đa {stats['max_late']:.1f}s)", **stats)
        if stats["late"]:
            ctx.log.warn(f"{stats['late']} câu đọc dài hơn khung thời gian: nên rút gọn bản dịch")
        return {"audio": save_wav(track, ctx.output("dub.wav"))}

    def preview(self, args, outdir):
        """A voice sample: args {voice, ref_audio, int8, text, media, timing (cues), cues (to read)}.
        Returns {"path", "text"}."""
        params = Params(**{k: args[k] for k in ("voice", "ref_audio", "int8") if args.get(k) is not None})
        timing = [_Cue(*c) for c in args.get("timing") or []]
        cues = [_Cue(*c) for c in args.get("cues") or []]
        if params.voice == SOURCE_VOICE and not params.ref_audio and not (args.get("media") and timing):
            raise UserError("Cần video gốc và phụ đề để nghe thử giọng gốc")
        ref_audio, window = self.voice_for(params, args.get("media"), timing, lambda _m: None)
        if params.voice == SOURCE_VOICE and not params.ref_audio and not ref_audio:
            raise UserError("Không tìm thấy đoạn nói liền 3 giây nào để clone giọng")
        text = (args.get("text") or "").strip()
        if not text and cues:   # the moment the voice was cloned from, else a sentence long enough to judge
            sentences = [t for _s, _e, t in group_cues(cues)]
            text = ((overlapping_text(cues, window) if window else None)
                    or next((t for t in sentences if len(t) >= 40), None)
                    or (sentences[0] if sentences else ""))
        text = text or SAMPLE_TEXT
        audio = self.dubber(params.voice, ref_audio, params.int8).synthesize(text)
        return {"path": str(save_wav(audio, outdir / f"preview-{hashlib.sha1(text.encode()).hexdigest()[:8]}.wav")),
                "text": text}

    def voices(self):
        """[{"label", "name"}] of the preset voices; pass name as voice=."""
        tts = next(iter(self.tts.values()), None)
        if tts is None:
            from vieneu import Vieneu
            tts = self.tts[False] = Vieneu()
        return [{"label": label, "name": name} for label, name in tts.list_preset_voices()]


SAMPLE_TEXT = "Xin chào, đây là giọng đọc thử cho video của bạn."


class _Cue:
    __slots__ = ("start", "end", "text")

    def __init__(self, start, end, text=""):
        self.start, self.end, self.text = start, end, text
