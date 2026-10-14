# Translate English .srt subtitles to Vietnamese with a local model (translation.py)
# the OpenAI API (openai_translation.py, needs OPENAI_API_KEY) or Claude Code (claude_translation.py,
# uses the Claude plan's limits).
#
#   .venv/bin/python translate-subtitle.py <file.srt | folder> [--recursive] [--out DIR] [--model vinai|opus] [--overwrite]
#   .venv/bin/python translate-subtitle.py <file.srt | folder> --engine openai [--openai-model gpt-5-mini]
#   .venv/bin/python translate-subtitle.py <file.srt | folder> --engine claude [--claude-model sonnet]
#
# Writes <name>.vi.srt next to each file (or under --out). Files ending in .vi.srt are skipped.
import argparse
import sys
import time
from pathlib import Path

import srt
from translation import DEFAULT_MODEL, MODELS, make_translator


def find_srt(path, recursive):
    if path.is_file():
        return [path]
    files = path.rglob("*.srt") if recursive else path.glob("*.srt")
    return sorted(f for f in files if f.is_file() and not f.name.endswith(".vi.srt"))


def vi_path_for(source, root, out):
    target = source.with_suffix(".vi.srt")
    if out is None:
        return target
    return out / target.relative_to(root)


def translate_file(translator, source, target):
    subtitles = list(srt.parse(source.read_text(encoding="utf-8-sig")))
    translated = translator.translate_subtitles(subtitles)
    assert [(s.start, s.end) for s in translated] == [(s.start, s.end) for s in subtitles]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(srt.compose(translated, reindex=False), encoding="utf-8")
    return translated


def main():
    parser = argparse.ArgumentParser(description="Translate English SRT subtitles to Vietnamese")
    parser.add_argument("path", type=Path, help=".srt file or folder")
    parser.add_argument("--recursive", "-r", action="store_true", help="also scan sub folders")
    parser.add_argument("--out", type=Path, help="write .vi.srt files here instead of next to the source")
    parser.add_argument("--engine", choices=["local", "openai", "claude"], default="local")
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL, help="local model")
    parser.add_argument("--beam", type=int, default=4, help="local beam size, 1 is fastest")
    parser.add_argument("--openai-model", help="default: $OPENAI_MODEL or gpt-5-mini")
    parser.add_argument("--claude-model", help="sonnet (default), haiku or opus")
    parser.add_argument("--workers", type=int, default=4, help="parallel LLM requests per file (claude: max 3)")
    parser.add_argument("--overwrite", action="store_true", help="recreate .vi.srt files that already exist")
    args = parser.parse_args()

    path = args.path.resolve()
    if not path.exists():
        sys.exit(f"Not found: {path}")
    root = path.parent if path.is_file() else path
    out = args.out.resolve() if args.out else None

    sources = find_srt(path, args.recursive)
    if not sources:
        sys.exit(f"No .srt files found in {path}")

    translator = None
    done, skipped, failed = 0, 0, 0
    for index, source in enumerate(sources, 1):
        target = vi_path_for(source, root, out)
        prefix = f"[{index}/{len(sources)}] {source.relative_to(root)}"
        if target.exists() and not args.overwrite:
            print(f"{prefix} — skipped, {target.name} exists")
            skipped += 1
            continue

        translator = translator or make_translator(args.engine, args.model, args.beam, args.openai_model, args.workers,
                                                 args.claude_model)
        started = time.perf_counter()
        try:
            subtitles = translate_file(translator, source, target)
        except Exception as error:
            print(f"{prefix} — failed: {error}")
            failed += 1
            continue
        print(f"{prefix} — {len(subtitles)} cues, took {time.perf_counter() - started:.1f}s")
        done += 1

    print(f"Done: {done} translated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
