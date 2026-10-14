# Dub Vietnamese .vi.srt subtitles into a voice track with VieNeu-TTS (tts.py), locally on CPU.
#
#   .venv/bin/python dub-subtitle.py <file.vi.srt | folder> [--recursive] [--out DIR] [--overwrite]
#   .venv/bin/python dub-subtitle.py <...> --voice "Ngọc Huyền"        # preset voice (--list-voices)
#   .venv/bin/python dub-subtitle.py <...> --ref-audio my_voice.wav    # clone a voice from a 3–8s clip
#   .venv/bin/python dub-subtitle.py <...> --mux [--original-volume 0.15] [--add-track]
#
# Writes <name>.vi.wav next to each .vi.srt (or under --out). With --mux, the video/audio file with
# the same name (<name>.mp4, .mkv, ...) gets a dubbed copy <name>.dub.<ext>: the voice mixed over
# the original audio at --original-volume (0 = voice only). The dub replaces the original audio, or
# with --add-track is added as a new (default) audio track next to it. Every <name>.srt and
# <name>.<lang>.srt found next to the video is embedded as a subtitle track (.vi.srt default).
import argparse
import subprocess
import sys
import time
from pathlib import Path

import srt

MEDIA_EXTENSIONS = [".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi", ".wav", ".mp3", ".m4a"]
SUBTITLE_CODECS = {".mp4": "mov_text", ".mov": "mov_text", ".m4v": "mov_text", ".mkv": "srt"}
DUB_CODECS = {".webm": "libopus", ".avi": "libmp3lame"}   # codec of the --add-track dub, aac otherwise
SINGLE_AUDIO = {".wav", ".mp3"}                           # containers that hold one audio track only
LANGUAGES = {"vi": ("vie", "Vietnamese"), "en": ("eng", "English"), "ja": ("jpn", "Japanese"),
             "ko": ("kor", "Korean"), "zh": ("zho", "Chinese"), "fr": ("fra", "French"),
             "de": ("deu", "German"), "es": ("spa", "Spanish")}


def find_vi_srt(path, recursive):
    if path.is_file():
        return [path]
    files = path.rglob("*.vi.srt") if recursive else path.glob("*.vi.srt")
    return sorted(f for f in files if f.is_file())


def wav_path_for(source, root, out):
    target = source.with_suffix(".wav")   # name.vi.srt -> name.vi.wav
    if out is None:
        return target
    return out / target.relative_to(root)


def find_media(source):
    stem = source.name.removesuffix(".vi.srt")
    for extension in MEDIA_EXTENSIONS:
        media = source.with_name(stem + extension)
        if media.is_file():
            return media
    return None


def find_subtitles(media):
    """<name>.srt and <name>.<lang>.srt next to the media as (lang, path), Vietnamese first."""
    found = []
    for file in media.parent.iterdir():
        name = file.name
        if not (name.startswith(media.stem + ".") and name.endswith(".srt") and file.is_file()):
            continue
        lang = name[len(media.stem) + 1:-len(".srt")]
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
    subprocess.run(command, check=True)
    return subtitles


def main():
    parser = argparse.ArgumentParser(description="Dub Vietnamese SRT subtitles with VieNeu-TTS")
    parser.add_argument("path", type=Path, nargs="?", help=".vi.srt file or folder")
    parser.add_argument("--recursive", "-r", action="store_true", help="also scan sub folders")
    parser.add_argument("--out", type=Path, help="write .vi.wav files here instead of next to the source")
    parser.add_argument("--voice", help="preset voice name (default: Phạm Tuyên)")
    parser.add_argument("--ref-audio", type=Path, help="clone the voice in this 3–8s clip instead of a preset")
    parser.add_argument("--list-voices", action="store_true", help="print preset voices and exit")
    parser.add_argument("--int8", action="store_true", help="faster int8 model (may sound worse)")
    parser.add_argument("--max-speed", type=float, default=1.3, help="max speed-up to fit a cue slot (≤ 2)")
    parser.add_argument("--mux", action="store_true", help="also write <name>.dub.<ext> from the matching video")
    parser.add_argument("--original-volume", type=float, default=0.15, help="original audio level in --mux, 0 = drop")
    parser.add_argument("--add-track", default=True, action="store_true",
                        help="--mux keeps the original audio and adds the dub as a new audio track")
    parser.add_argument("--overwrite", action="store_true", help="recreate files that already exist")
    args = parser.parse_args()

    from tts import DEFAULT_VOICE, Dubber, save_wav

    if args.list_voices:
        from vieneu import Vieneu
        for description, _name in Vieneu().list_preset_voices():
            print(description)
        return
    if args.path is None:
        parser.error("path is required")
    if args.ref_audio and not args.ref_audio.is_file():
        sys.exit(f"Not found: {args.ref_audio}")

    path = args.path.resolve()
    if not path.exists():
        sys.exit(f"Not found: {path}")
    root = path.parent if path.is_file() else path
    out = args.out.resolve() if args.out else None
    max_speed = min(max(args.max_speed, 1.0), 2.0)

    sources = find_vi_srt(path, args.recursive)
    if not sources:
        sys.exit(f"No .vi.srt files found in {path}")

    dubber = None
    done, skipped, failed = 0, 0, 0
    for index, source in enumerate(sources, 1):
        target = wav_path_for(source, root, out)
        media = find_media(source) if args.mux else None
        dubbed = target.with_name(source.name.removesuffix(".vi.srt") + ".dub" + media.suffix) if media else None
        prefix = f"[{index}/{len(sources)}] {source.relative_to(root)}"
        if args.mux and media is None:
            print(f"{prefix} — no matching video/audio for --mux, writing .vi.wav only")
        need_wav = args.overwrite or not target.exists()
        need_mux = dubbed is not None and (args.overwrite or not dubbed.exists())
        if not need_wav and not need_mux:
            print(f"{prefix} — skipped, {target.name} exists")
            skipped += 1
            continue

        started = time.perf_counter()
        try:
            if need_wav:
                dubber = dubber or Dubber(voice=args.voice or DEFAULT_VOICE, ref_audio=args.ref_audio,
                                          precision="int8" if args.int8 else "fp32")
                subtitles = list(srt.parse(source.read_text(encoding="utf-8-sig")))
                track, stats = dubber.dub(subtitles, max_speed=max_speed, progress=lambda i, n: print(
                    f"\r{prefix} — sentence {i}/{n}", end="", flush=True))
                save_wav(track, target)
                print(f"\r{prefix} — {stats['sentences']} sentences, {len(track) / 48000 / 60:.1f} min audio, "
                      f"{stats['sped_up']} sped up, {stats['late']} late (max {stats['max_late']:.1f}s), "
                      f"took {time.perf_counter() - started:.1f}s")
            if need_mux:
                add_track = args.add_track and media.suffix.lower() not in SINGLE_AUDIO
                subtitles = mux(media, target, find_subtitles(media), dubbed, args.original_volume, add_track)
                print(f"{prefix} — wrote {dubbed.name}, {'added' if add_track else 'replaced'} audio track, "
                      f"{len(subtitles)} subtitles")
        except Exception as error:
            print(f"\n{prefix} — failed: {error}")
            failed += 1
            continue
        done += 1

    print(f"Done: {done} dubbed, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
