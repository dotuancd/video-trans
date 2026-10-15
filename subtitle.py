# Shared helpers to build SRT subtitles from word timestamps.
# A word is a dict with "word", "start" and "end" (seconds), as returned by both
# needle (word_timestamps=True) and moondream (timestamps="word").
import json
from datetime import timedelta
from srt import Subtitle, compose

MAX_GAP = 0.8        # seconds of silence that starts a new cue
MAX_CHARS = 84       # about 2 lines of 42 characters
MAX_DURATION = 7.0   # seconds
MIN_CHARS = 15       # a cue shorter than this joins a neighbour of the same sentence
PAUSE = 0.35         # seconds of silence inside a cue that is a natural place to cut it

SENTENCE_ENDS = (".", "?", "!", "…")
CLAUSE_ENDS = (",", ";", ":", "—", "–")


def text_of(cue):
    return " ".join(w["word"].strip() for w in cue)


def ends_sentence(cue):
    return cue[-1]["word"].strip().endswith(SENTENCE_ENDS)


def cut_point(cue, min_head):
    """Where to cut a cue that grew too long: after its last clause punctuation (or else pause)
    leaving at least `min_head` characters before it; the whole cue when there is none."""
    pause = None
    for i in range(len(cue) - 1, 0, -1):
        if len(text_of(cue[:i])) < min_head:
            break
        if cue[i - 1]["word"].strip().endswith(CLAUSE_ENDS):
            return i
        if pause is None and cue[i]["start"] - cue[i - 1]["end"] > PAUSE:
            pause = i
    return pause or len(cue)


def fits(cue, max_chars, max_duration):
    return len(text_of(cue)) <= max_chars and cue[-1]["end"] - cue[0]["start"] <= max_duration


def merge_short(cues, max_gap, max_chars, max_duration, min_chars):
    """Joins a too-short cue (an orphan left by a length cut, e.g. "of it.") to the cue of the
    same sentence next to it, allowing that one to overflow the limits by a short cue's worth."""
    max_chars, max_duration = max_chars + min_chars, max_duration + 1.5
    merged = []
    for cue in cues:
        previous = merged[-1] if merged else None
        if (previous and min(len(text_of(previous)), len(text_of(cue))) < min_chars
                and not ends_sentence(previous)
                and cue[0]["start"] - previous[-1]["end"] <= max_gap
                and fits(previous + cue, max_chars, max_duration)):
            merged[-1] = previous + cue
        else:
            merged.append(cue)
    return merged


def group_words(words, max_gap=MAX_GAP, max_chars=MAX_CHARS, max_duration=MAX_DURATION, min_chars=MIN_CHARS):
    cues = []
    current = []
    for word in words:
        text = word["word"].strip()
        if not text:
            continue
        if current:
            if ends_sentence(current) or word["start"] - current[-1]["end"] > max_gap:
                cues.append(current)
                current = []
            elif (len(text_of(current)) + 1 + len(text) > max_chars
                  or word["end"] - current[0]["start"] > max_duration):
                cut = cut_point(current, max(min_chars, max_chars // 3))
                cues.append(current[:cut])
                current = current[cut:]
        current.append(word)
    if current:
        cues.append(current)
    return merge_short(cues, max_gap, max_chars, max_duration, min_chars)


def words_to_subtitles(words, **options):
    return [
        Subtitle(index=index + 1,
                 start=timedelta(seconds=cue[0]["start"]),
                 end=timedelta(seconds=cue[-1]["end"]),
                 content=text_of(cue))
        for index, cue in enumerate(group_words(words, **options))
    ]


def write_srt(words, path, **options):
    subtitles = words_to_subtitles(words, **options)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(compose(subtitles))
    return subtitles


def words_path(srt_path):
    """<stem>.srt → <stem>.words.json: the timed words its cues were grouped from."""
    return srt_path.with_name(srt_path.name.removesuffix(".srt") + ".words.json")


def write_words(words, path):
    """Keeps the words so the editor can cut cues where a word is spoken, not by character share."""
    kept = [{"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in words if w["word"].strip()]
    path.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
