# Create SRT subtitles for every video/audio file in a folder with needle-whistle.
#
#   .venv/bin/python batch-subtitle.py <folder> [--recursive] [--out DIR] [--language en] [--overwrite] [--translate]
#
# --translate also writes <name>.vi.srt with the local English→Vietnamese model (translation.py),
# or with --engine openai (openai_translation.py) / --engine claude (claude_translation.py).
# --dub (implies --translate) also writes a Vietnamese voice track <name>.vi.wav with VieNeu-TTS (tts.py).
#
# Audio is decoded in-process by PyAV (bundles the FFmpeg libraries, no ffmpeg binary needed)
# straight to 16 kHz mono float32 (what whistle expects), so no temporary WAV files are written.
import argparse
import sys
import time
from pathlib import Path

import av
import needle
import numpy as np
import srt
from subtitle import write_srt

SAMPLE_RATE = 16000
EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",   # video
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",   # audio
}


def load_audio(path):
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError("no audio stream")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        chunks = [out.to_ndarray().ravel()
                  for frame in container.decode(container.streams.audio[0])
                  for out in resampler.resample(frame)]
        chunks += [out.to_ndarray().ravel() for out in resampler.resample(None)]
    if not chunks:
        raise RuntimeError("no audio stream")
    return np.concatenate(chunks).astype(np.float32).tobytes()


def find_media(folder, recursive):
    files = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(f for f in files if f.is_file() and f.suffix.lower() in EXTENSIONS)


def srt_path_for(media, folder, out):
    if out is None:
        return media.with_suffix(".srt")
    return (out / media.relative_to(folder)).with_suffix(".srt")


def main():
    parser = argparse.ArgumentParser(description="Batch create SRT subtitles with needle-whistle")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--recursive", "-r", action="store_true", help="also scan sub folders")
    parser.add_argument("--out", type=Path, help="write .srt files here instead of next to the media")
    parser.add_argument("--language", help="e.g. en; detected automatically when left out")
    parser.add_argument("--overwrite", action="store_true", help="recreate .srt files that already exist")
    parser.add_argument("--translate", action="store_true", help="also write a Vietnamese .vi.srt")
    parser.add_argument("--dub", action="store_true", help="also write a Vietnamese voice track .vi.wav")
    parser.add_argument("--voice", help="dub preset voice (default: Phạm Tuyên)")
    parser.add_argument("--ref-audio", type=Path, help="dub with the voice cloned from this 3–8s clip")
    parser.add_argument("--engine", choices=["local", "openai", "claude"], default="local", help="translation engine")
    parser.add_argument("--openai-model", help="default: $OPENAI_MODEL or gpt-5-mini")
    parser.add_argument("--claude-model", help="sonnet (default), haiku or opus")
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")
    out = args.out.resolve() if args.out else None

    media_files = find_media(folder, args.recursive)
    args.translate = args.translate or args.dub
    if args.translate:
        from translation import make_translator
    if args.dub:
        from tts import DEFAULT_VOICE, Dubber, save_wav

    if not media_files:
        sys.exit(f"No video/audio files found in {folder}")

    translator, dubber = None, None
    done, skipped, failed = 0, 0, 0
    for index, media in enumerate(media_files, 1):
        srt_path = srt_path_for(media, folder, out)
        vi_path = srt_path.with_suffix(".vi.srt")
        prefix = f"[{index}/{len(media_files)}] {media.relative_to(folder)}"
        need_srt = args.overwrite or not srt_path.exists()
        need_vi = args.translate and (args.overwrite or not vi_path.exists())
        wav_path = vi_path.with_suffix(".wav")
        need_dub = args.dub and (args.overwrite or not wav_path.exists())
        if not need_srt and not need_vi and not need_dub:
            print(f"{prefix} — skipped, {srt_path.name} exists")
            skipped += 1
            continue

        started = time.perf_counter()
        try:
            if need_srt:
                audio = load_audio(media)
                transcript = needle.transcribe(audio, language=args.language, word_timestamps=True)
                srt_path.parent.mkdir(parents=True, exist_ok=True)
                subtitles = write_srt(transcript["words"], srt_path)
                duration = len(audio) / 4 / SAMPLE_RATE
                print(f"{prefix} — {len(subtitles)} cues, audio {duration / 60:.1f} min, "
                      f"took {time.perf_counter() - started:.1f}s")
            if need_vi:
                started = time.perf_counter()
                translator = translator or make_translator(args.engine, openai_model=args.openai_model,
                                                           claude_model=args.claude_model)
                subtitles = list(srt.parse(srt_path.read_text(encoding="utf-8")))
                vi_path.write_text(srt.compose(translator.translate_subtitles(subtitles), reindex=False),
                                   encoding="utf-8")
                print(f"{prefix} — translated to {vi_path.name}, took {time.perf_counter() - started:.1f}s")
            if need_dub:
                started = time.perf_counter()
                dubber = dubber or Dubber(voice=args.voice or DEFAULT_VOICE, ref_audio=args.ref_audio)
                track, stats = dubber.dub(list(srt.parse(vi_path.read_text(encoding="utf-8"))))
                save_wav(track, wav_path)
                print(f"{prefix} — dubbed to {wav_path.name}, {stats['late']} late sentences, "
                      f"took {time.perf_counter() - started:.1f}s")
        except Exception as error:
            print(f"{prefix} — failed: {error}")
            failed += 1
            continue
        done += 1

    print(f"Done: {done} created, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
