# Video translation CLI: media → English .srt (needle-whistle) → Vietnamese .vi.srt → dubbed .vi.wav / .dub.<ext>
#
#   uv run dich <file | folder>               # same as `dich run`: .srt + .vi.srt next to each video/audio
#   uv run dich <folder> -r --dub --mux       # + .vi.wav voice track and a dubbed <name>.dub.<ext> copy
#   uv run dich sub <file | folder>           # .srt only
#   uv run dich translate <file.srt | folder> -e openai|claude|local
#   uv run dich dub <file.vi.srt | folder> [--voice NAME | --ref-audio clip.wav] [--mux]
#   uv run dich voices                        # list preset dub voices
#   uv run dich ui                            # web UI on http://127.0.0.1:7860
#
# Every command skips outputs that already exist (unless --overwrite), so a stopped run can be resumed.
# Audio is decoded in-process by PyAV (bundles the FFmpeg libraries) straight to 16 kHz mono float32,
# what whistle expects. --mux needs the ffmpeg binary.
import argparse
import logging
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import srt

import settings

log = logging.getLogger("dich")

ASR_SAMPLE_RATE = 16000
# looked up in this order when --mux searches the video for a .vi.srt
MEDIA_EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi",
                    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
SUBTITLE_CODECS = {".mp4": "mov_text", ".mov": "mov_text", ".m4v": "mov_text", ".mkv": "srt"}
DUB_CODECS = {".webm": "libopus", ".avi": "libmp3lame"}   # codec of the --add-track dub, aac otherwise
SINGLE_AUDIO = {".wav", ".mp3", ".aac", ".flac"}   # containers that hold one audio track only
LANGUAGES = {"vi": ("vie", "Vietnamese"), "en": ("eng", "English"), "ja": ("jpn", "Japanese"),
             "ko": ("kor", "Korean"), "zh": ("zho", "Chinese"), "fr": ("fra", "French"),
             "de": ("deu", "German"), "es": ("spa", "Spanish")}
COMMANDS = ("run", "sub", "translate", "dub", "voices", "ui")


class DichError(Exception):
    """Bad input (missing path, no files): the CLI exits with the message."""


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


def read_srt(path):
    return list(srt.parse(path.read_text(encoding="utf-8-sig")))


def find_files(path, recursive, accept):
    if path.is_file():
        return [path]
    files = path.rglob("*") if recursive else path.glob("*")
    return sorted(f for f in files if f.is_file() and accept(f.name.lower()))


def is_media(name):
    # skip our own outputs: <name>.vi.wav and <name>.dub.<ext>
    return name.endswith(MEDIA_EXTENSIONS) and Path(name).with_suffix("").suffix not in (".vi", ".dub")


def output_path(source, root, out, suffix):
    """source with its last extension replaced by suffix, under out (mirroring root) when given."""
    target = source.with_suffix(suffix)
    return target if out is None else out / target.relative_to(root)


def is_source_srt(name):
    """An original-language .srt, not one of our .vi.srt translations."""
    return name.endswith(".srt") and not name.endswith(".vi.srt")


def find_media(subtitle):
    """The video/audio next to <stem>.srt or <stem>.vi.srt."""
    stem = subtitle.name.removesuffix(".srt").removesuffix(".vi")
    for extension in MEDIA_EXTENSIONS:
        media = subtitle.with_name(stem + extension)
        if media.is_file():
            return media
    return None


def find_subtitles(folder, stem):
    """<stem>.srt and <stem>.<lang>.srt in folder as (lang, path), Vietnamese first."""
    found = []
    for file in folder.glob("*.srt"):
        if file.name.startswith(stem + "."):
            lang = file.name[len(stem) + 1:-len(".srt")]
            if "." not in lang:
                found.append((lang, file))
    return sorted(found, key=lambda item: (item[0] != "vi", item[0]))


def mux(media, voice_wav, subtitles, target, original_volume, add_track):
    subtitle_codec = SUBTITLE_CODECS.get(target.suffix.lower())
    subtitles = subtitles if subtitle_codec else []
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-i", str(voice_wav)]
    for _lang, subtitle in subtitles:
        command += ["-i", str(subtitle)]
    if original_volume > 0:
        command += ["-filter_complex",
                    f"[0:a]volume={original_volume}[bg];[bg][1:a]amix=inputs=2:duration=longest:normalize=0[a]",
                    "-map", "0:v?", "-map", "[a]"]
    else:
        command += ["-map", "0:v?", "-map", "1:a"]
    if add_track:   # dub as audio track 0 (default), original tracks copied after it
        command += ["-map", "0:a?", "-c:a", "copy", "-c:a:0", DUB_CODECS.get(target.suffix.lower(), "aac"),
                    "-metadata:s:a", "title=Original", "-disposition:a", "0", "-disposition:a:0", "default"]
    command += ["-metadata:s:a:0", "language=vie", "-metadata:s:a:0", "title=Vietnamese"]
    for index, (lang, _subtitle) in enumerate(subtitles):
        code, title = LANGUAGES.get(lang, (None, lang)) if lang else (None, "Original")
        command += ["-map", str(index + 2), f"-metadata:s:s:{index}", f"title={title}",
                    f"-disposition:s:{index}", "default" if index == 0 else "0"]
        if code:
            command += [f"-metadata:s:s:{index}", f"language={code}"]
    if subtitles:
        command += ["-c:s", subtitle_codec]
    command += ["-c:v", "copy", str(target)]
    log.info("%s", shlex.join(command))
    result = subprocess.run(command, stderr=subprocess.PIPE, text=True)
    sys.stderr.write(result.stderr)     # through Python, so the step log gets it
    result.check_returncode()
    return subtitles


# Options of each step and their defaults: the CLI flags, and what the web UI stores per step.
STEP_OPTIONS = {
    "transcribe": {"language": None},
    "translate": {"engine": settings.ENGINE, "model": settings.LOCAL_MODEL, "beam": 4, "openai_model": None, "claude_model": None,
                  "workers": 4},
    "dub": {"voice": "source", "ref_audio": None, "int8": False, "max_speed": 1.3},
    "mux": {"original_volume": 0.15, "add_track": False},
}
STEP_NAMES = tuple(STEP_OPTIONS)
STARTS = ("media", "srt", "vi_srt")   # what a pipeline starts from: video/audio, .srt or .vi.srt


@dataclass(frozen=True)
class StepSpec:
    """One step of one file: inputs → output. Run it with Steps.run."""
    name: str
    inputs: tuple
    output: Path


def plan_steps(source, start, root, out, translate=True, dub=False, mux=False, warn=None):
    """The steps that take source (a start file) to the wanted outputs, in order.
    Outputs go next to source, or under out mirroring root."""
    specs = []
    if start == "media":
        srt_path = output_path(source, root, out, ".srt")
        vi_path = srt_path.with_suffix(".vi.srt")
        specs.append(StepSpec("transcribe", (source,), srt_path))
    elif start == "srt":
        srt_path, vi_path = source, output_path(source, root, out, ".vi.srt")
    else:
        srt_path, vi_path = None, source
    if srt_path is not None and (translate or dub):
        specs.append(StepSpec("translate", (srt_path,), vi_path))
    if dub:
        wav = output_path(source, root, out, ".wav") if start == "vi_srt" else vi_path.with_suffix(".wav")
        media = source if start == "media" else find_media(source)
        # the video is also where dub clones the original speaker's voice from
        specs.append(StepSpec("dub", (vi_path, media) if media else (vi_path,), wav))
        if mux:
            if media is None:
                if warn:
                    warn("no matching video/audio for --mux, writing .vi.wav only")
            else:
                specs.append(StepSpec("mux", (media, wav), wav.with_name(media.stem + ".dub" + media.suffix)))
    return specs


def step_options(name, options):
    """options (dict or namespace) reduced to the fields of step `name`, defaults filled in."""
    get = options.get if isinstance(options, dict) else partial(getattr, options)
    return {key: get(key, default) for key, default in STEP_OPTIONS[name].items()}


class Steps:
    """One method per pipeline step; each writes its output file and returns a status message.
    Models load on first use and stay loaded, one per distinct set of options."""

    def __init__(self):
        self._translators = {}
        self._tts = {}       # int8 → Vieneu model, shared by every voice
        self._dubbers = {}

    def run(self, spec, options, progress=None):
        """Run spec with options (dict or namespace, missing fields → defaults).
        progress(done, total) is called as translate and dub go along."""
        o = SimpleNamespace(**step_options(spec.name, options))
        if spec.name == "mux":
            return self.mux(*spec.inputs, spec.output, o)
        if spec.name == "dub":   # jobs planned before dub took the video have the .vi.srt only
            source, *media = spec.inputs
            return self.dub(source, spec.output, o, progress, media[0] if media else find_media(source))
        return getattr(self, spec.name)(*spec.inputs, spec.output, o, progress)

    def transcribe(self, media, target, o, progress=None):
        """One needle call, no progress: it runs ~70× real time, and splitting the audio changes the
        transcript (3–9% of words, sentence-ending punctuation most) because the model loses context."""
        import needle
        from subtitle import write_srt

        audio = load_audio(media)
        log.info("Nhận dạng %.1f phút audio…", len(audio) / 4 / ASR_SAMPLE_RATE / 60)
        transcript = needle.transcribe(audio, language=o.language or None, word_timestamps=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        cues = write_srt(transcript["words"], target)
        return f"{len(cues)} cues, audio {len(audio) / 4 / ASR_SAMPLE_RATE / 60:.1f} min"

    def translate(self, source, target, o, progress=None):
        key = (o.engine, o.model, o.beam, o.openai_model, o.claude_model, o.workers)
        if key not in self._translators:
            from translation import make_translator
            log.info("Khởi tạo bộ dịch %s…", o.engine)
            self._translators[key] = make_translator(o.engine, model=o.model, beam=o.beam,
                                                     openai_model=o.openai_model or None,
                                                     claude_model=o.claude_model or None, workers=o.workers)
        subtitles = read_srt(source)
        log.info("Dịch %d câu", len(subtitles))
        translated = self._translators[key].translate_subtitles(subtitles, progress)
        assert [(s.start, s.end) for s in translated] == [(s.start, s.end) for s in subtitles]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(srt.compose(translated, reindex=False), encoding="utf-8")
        return f"translated {len(translated)} cues to {target.name}"

    def dubber(self, voice=None, ref_audio=None, int8=False):
        from tts import DEFAULT_VOICE, SOURCE_VOICE, Dubber

        if voice == SOURCE_VOICE:   # resolved to a ref_audio clip by the caller; without one, the preset
            voice = None
        key = (voice or DEFAULT_VOICE, str(ref_audio) if ref_audio else None, bool(int8))
        if key not in self._dubbers:
            log.info("Tải model giọng đọc (%s%s)…", key[1] or key[0], ", int8" if key[2] else "")
            dubber = Dubber(voice=key[0], ref_audio=key[1], precision="int8" if int8 else "fp32",
                            tts=self._tts.get(key[2]))
            self._tts[key[2]] = dubber.tts
            self._dubbers[key] = dubber
        return self._dubbers[key]

    def dub(self, source, target, o, progress=None, media=None):
        from tts import DEFAULT_VOICE, SAMPLE_RATE, SOURCE_VOICE, reference_clip, save_wav

        if o.ref_audio and not Path(o.ref_audio).is_file():
            raise DichError(f"Not found: {o.ref_audio}")
        subtitles = read_srt(source)
        ref_audio, voice_note = o.ref_audio, ""
        if not ref_audio and o.voice == SOURCE_VOICE:
            clip = reference_clip(media, subtitles) if media else None
            if clip:
                ref_audio, (start, end) = clip
                voice_note = f", original voice {clock(start)}–{clock(end)}"
            else:
                voice_note = (f", {DEFAULT_VOICE} voice: " +
                              ("no video to clone the voice from" if not media else "no 3s+ stretch of speech"))
        if voice_note:
            log.info("Giọng: %s", voice_note.removeprefix(", "))
        log.info("Lồng tiếng %d câu", len(subtitles))
        dubber = self.dubber(o.voice, ref_audio, o.int8)
        max_speed = min(max(float(o.max_speed), 1.0), 2.0)
        track, stats = dubber.dub(subtitles, max_speed=max_speed, progress=progress)
        save_wav(track, target)
        return (f"dubbed to {target.name}: {stats['sentences']} sentences, "
                f"{len(track) / SAMPLE_RATE / 60:.1f} min, {stats['sped_up']} sped up, "
                f"{stats['late']} late (max {stats['max_late']:.1f}s){voice_note}")

    def mux(self, media, voice_wav, target, o):
        add_track = o.add_track and media.suffix.lower() not in SINGLE_AUDIO
        subtitles = find_subtitles(voice_wav.parent, media.stem)
        subtitles = mux(media, voice_wav, subtitles, target, o.original_volume, add_track)
        return f"wrote {target.name}, {'added' if add_track else 'replaced'} audio track, {len(subtitles)} subtitles"


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def process(sources, root, overwrite, plan, log=print, cancel=None):
    """Run plan(source, prefix) → [(output, step)] for every source, skipping outputs that already exist.
    Stops before the next source once cancel (a threading.Event) is set."""
    done, skipped, failed = 0, 0, 0
    for index, source in enumerate(sources, 1):
        if cancel is not None and cancel.is_set():
            log(f"Stopped, {len(sources) - index + 1} files left")
            break
        prefix = f"[{index}/{len(sources)}] {source.relative_to(root)}"
        steps = [(output, step) for output, step in plan(source, prefix) if overwrite or not output.exists()]
        if not steps:
            log(f"{prefix} — skipped, outputs exist")
            skipped += 1
            continue
        try:
            for _output, step in steps:
                started = time.perf_counter()
                message = step()
                log(f"\r\033[K{prefix} — {message}, took {time.perf_counter() - started:.1f}s")
        except Exception as error:
            log(f"\r\033[K{prefix} — failed: {error}")
            failed += 1
            continue
        done += 1
    log(f"Done: {done} processed, {skipped} skipped, {failed} failed")


def build_parser():
    T, D = STEP_OPTIONS["translate"], STEP_OPTIONS["dub"]
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", type=Path, help="file or folder")
    common.add_argument("--recursive", "-r", action="store_true", help="also scan sub folders")
    common.add_argument("--out", type=Path, help="write outputs here (mirroring the folder) instead of next to the source")
    common.add_argument("--overwrite", action="store_true", help="recreate outputs that already exist")

    asr = argparse.ArgumentParser(add_help=False)
    asr.add_argument("--language", help="spoken language, e.g. en; detected automatically when left out")

    translate = argparse.ArgumentParser(add_help=False)
    translate.add_argument("--engine", "-e", choices=["local", "openai", "claude"], default=T["engine"],
                           help=f"translation engine (default: {T['engine']}, $DICH_ENGINE)")
    translate.add_argument("--model", choices=["vinai", "opus"], default=T["model"], help=f"local model (default: {T['model']}, $DICH_LOCAL_MODEL)")
    translate.add_argument("--beam", type=int, default=T["beam"], help="local beam size, 1 is fastest")
    translate.add_argument("--openai-model", help=f"default: {settings.OPENAI_MODEL} ($OPENAI_MODEL)")
    translate.add_argument("--claude-model", help=f"sonnet, haiku or opus (default: {settings.CLAUDE_MODEL}, $DICH_CLAUDE_MODEL)")
    translate.add_argument("--workers", type=int, default=T["workers"], help="parallel LLM requests per file (claude: max 3)")

    dub = argparse.ArgumentParser(add_help=False)
    dub.add_argument("--voice", default=D["voice"],
                     help=f"'source' (default) clones the speaker of the video, or a preset voice "
                          f"(used when there is no video: {settings.VOICE}, $DICH_VOICE; see `dich voices`)")
    dub.add_argument("--ref-audio", type=Path, help="clone the voice in this 3–8s clip instead of a preset")
    dub.add_argument("--int8", action="store_true", help="faster int8 TTS model (may sound worse)")
    dub.add_argument("--max-speed", type=float, default=D["max_speed"], help="max speed-up to fit a sentence slot (1–2)")
    dub.add_argument("--mux", action="store_true", help="also write <name>.dub.<ext> from the matching video")
    dub.add_argument("--original-volume", type=float, default=STEP_OPTIONS["mux"]["original_volume"], help="original audio level in --mux, 0 = drop")
    dub.add_argument("--add-track", action="store_true",
                     help="--mux keeps the original audio and adds the dub as a new audio track")

    parser = argparse.ArgumentParser(prog="dich", description="Subtitle, translate (EN→VI) and dub videos. "
                                     "`dich <path>` is short for `dich run <path>`.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", parents=[common, asr, translate, dub],
                              help="video/audio → .srt → .vi.srt (→ .vi.wav with --dub)")
    run.add_argument("--dub", action="store_true", help="also write a Vietnamese voice track .vi.wav")
    commands.add_parser("sub", parents=[common, asr], help="video/audio → .srt")
    commands.add_parser("translate", parents=[common, translate], help=".srt → .vi.srt")
    commands.add_parser("dub", parents=[common, dub], help=".vi.srt → .vi.wav (→ .dub.<ext> with --mux)")
    commands.add_parser("voices", help="list preset dub voices")
    ui = commands.add_parser("ui", help="web UI in the browser (needs the ui extra)")
    ui.add_argument("--root", type=Path, default=settings.UI_ROOT,
                    help=f"folder the UI may read and write (default: {settings.UI_ROOT}, $DICH_UI_ROOT)")
    ui.add_argument("--port", type=int, default=settings.UI_PORT, help="default: $DICH_UI_PORT or 7860")
    ui.add_argument("--no-browser", action="store_true", help="do not open the browser")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)

    if args.command == "voices":
        from tts import SOURCE_VOICE, preset_voices
        print(f"{SOURCE_VOICE}  (default: clone the speaker of the video)")
        for label, _name in preset_voices():
            print(label)
        return
    if args.command == "ui":
        from dich_server.api import launch
        launch(args.root, args.port, open_browser=not args.no_browser)
        return
    try:
        execute(args)
    except DichError as error:
        sys.exit(str(error))


def execute(args, log=print, cancel=None):
    """Run a run/sub/translate/dub command from parsed args. log(line) gets every status line;
    cancel (threading.Event) stops before the next file."""
    path = args.path.resolve()
    if not path.exists():
        raise DichError(f"Not found: {path}")
    if getattr(args, "ref_audio", None) and not args.ref_audio.is_file():
        raise DichError(f"Not found: {args.ref_audio}")
    root = path.parent if path.is_file() else path
    out = args.out.resolve() if args.out else None
    steps = Steps()
    start, accept, kind = {
        "run": ("media", is_media, "video/audio"),
        "sub": ("media", is_media, "video/audio"),
        "translate": ("srt", is_source_srt, ".srt"),
        "dub": ("vi_srt", lambda name: name.endswith(".vi.srt"), ".vi.srt"),
    }[args.command]
    wanted = {"translate": args.command in ("run", "translate"),
              "dub": args.command == "dub" or getattr(args, "dub", False),
              "mux": getattr(args, "mux", False)}

    def plan(source, prefix):
        def progress(name, done, total):
            print(f"\r{prefix} — {name} {done}/{total}", end="", flush=True)
        specs = plan_steps(source, start, root, out, **wanted, warn=lambda message: log(f"{prefix} — {message}"))
        return [(spec.output, partial(steps.run, spec, args, partial(progress, spec.name))) for spec in specs]

    sources = find_files(path, args.recursive, accept)
    if not sources:
        raise DichError(f"No {kind} files found in {path}")
    process(sources, root, args.overwrite, plan, log, cancel)


if __name__ == "__main__":
    main()
