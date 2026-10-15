# Shared helpers to build SRT subtitles from word timestamps.
# A word is a dict with "word", "start" and "end" (seconds), as returned by both
# needle (word_timestamps=True) and moondream (timestamps="word").
from datetime import timedelta
from srt import Subtitle, compose

MAX_GAP = 0.8        # seconds of silence that starts a new cue
MAX_CHARS = 84       # about 2 lines of 42 characters
MAX_DURATION = 7.0   # seconds


def group_words(words, max_gap=MAX_GAP, max_chars=MAX_CHARS, max_duration=MAX_DURATION):
    cues = []
    current = []
    for word in words:
        text = word["word"].strip()
        if not text:
            continue
        if current:
            previous = current[-1]
            length = len(" ".join(w["word"].strip() for w in current)) + 1 + len(text)
            if (previous["word"].strip().endswith((".", "?", "!"))
                    or word["start"] - previous["end"] > max_gap
                    or length > max_chars
                    or word["end"] - current[0]["start"] > max_duration):
                cues.append(current)
                current = []
        current.append(word)
    if current:
        cues.append(current)
    return cues


def words_to_subtitles(words, **options):
    return [
        Subtitle(index=index + 1,
                 start=timedelta(seconds=cue[0]["start"]),
                 end=timedelta(seconds=cue[-1]["end"]),
                 content=" ".join(w["word"].strip() for w in cue))
        for index, cue in enumerate(group_words(words, **options))
    ]


def write_srt(words, path, **options):
    subtitles = words_to_subtitles(words, **options)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(compose(subtitles))
    return subtitles
