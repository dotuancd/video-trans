# The transcript every stage reads and writes: JSON, not SRT (SRT/VTT are exports).
#
#   {"lang": "en", "cues": [{"id": "c_0007", "start": 12.34, "end": 15.10, "text": "...", "speaker": null,
#                            "words": [{"w": "Hello", "s": 12.34, "e": 12.70}], "src_id": null}]}
#
# Cue ids stay the same through the stages: translate writes src_id = the id of the cue it translated,
# so the editor pairs a translation with its original and later stages know which cue changed.
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

import srt


@dataclass
class Cue:
    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list | None = None     # [{"w", "s", "e"}] as spoken, None once the text was rewritten
    src_id: str | None = None


@dataclass
class Transcript:
    lang: str | None
    cues: list = field(default_factory=list)

    def to_dict(self):
        return {"lang": self.lang, "cues": [asdict(c) for c in self.cues]}

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("lang"), [Cue(**{k: v for k, v in c.items() if k in Cue.__dataclass_fields__})
                                      for c in data.get("cues", [])])

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        return path

    def by_id(self):
        return {c.id: c for c in self.cues}


def cue_id(index):
    return f"c_{index:04d}"


def new_cue_id():
    """For cues made by the editor (split): never clashes with the c_NNNN of a stage."""
    return "c_" + secrets.token_hex(4)


def ensure_ids(cues):
    seen = set()
    for cue in cues:
        if not cue.id or cue.id in seen:
            cue.id = new_cue_id()
        seen.add(cue.id)
    return cues


# --- SRT / VTT ---

def clock(seconds, sep=","):
    return srt.timedelta_to_srt_timestamp(timedelta(seconds=seconds)).replace(",", sep)


def to_srt(transcript):
    return srt.compose([srt.Subtitle(index=i + 1, start=timedelta(seconds=c.start), end=timedelta(seconds=c.end),
                                     content=c.text) for i, c in enumerate(transcript.cues)], reindex=False)


def to_vtt(transcript):
    lines = ["WEBVTT", ""]
    for cue in transcript.cues:
        lines += [f"{clock(cue.start, '.')} --> {clock(cue.end, '.')}", cue.text, ""]
    return "\n".join(lines)


def from_srt(text, lang=None):
    subtitles = list(srt.parse(text))
    return Transcript(lang, [Cue(cue_id(i + 1), s.start.total_seconds(), s.end.total_seconds(), s.content.strip())
                             for i, s in enumerate(subtitles)])


def read_srt(path, lang=None):
    return from_srt(Path(path).read_text(encoding="utf-8-sig"), lang)


# --- cues from timed words (ASR) ---
# A word is {"word", "start", "end"} (seconds), as needle returns them with word_timestamps=True.

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


def from_words(words, lang=None, **options):
    """A transcript whose cues group the timed words, each cue keeping its words."""
    return Transcript(lang, [
        Cue(cue_id(i + 1), round(group[0]["start"], 3), round(group[-1]["end"], 3), text_of(group),
            words=[{"w": w["word"].strip(), "s": round(w["start"], 3), "e": round(w["end"], 3)} for w in group])
        for i, group in enumerate(group_words(words, **options))])
