# Shared by the translate drivers: sentence grouping over cues and splitting a translation back.
import re

SENTENCE_END = (".", "?", "!", "…", '."', '?"', '!"')
MAX_SENTENCE_CHARS = 400


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def group_sentences(cues, max_chars=MAX_SENTENCE_CHARS, max_gap=None):
    """Indexes of consecutive cues (anything with .start/.end seconds and .text) that together form one
    sentence. With max_gap, a pause longer than that many seconds also ends a sentence."""
    groups, current, length = [], [], 0
    for index, cue in enumerate(cues):
        if current and max_gap is not None and cue.start - cues[current[-1]].end > max_gap:
            groups.append(current)
            current, length = [], 0
        current.append(index)
        length += len(cue.text)
        if clean(cue.text).endswith(SENTENCE_END) or length > max_chars:
            groups.append(current)
            current, length = [], 0
    if current:
        groups.append(current)
    return groups


def split_like(text, sources):
    """Split text into len(sources) parts at word boundaries, sized like the sources."""
    words = text.split()
    if len(sources) == 1 or not words:
        return [text] + [""] * (len(sources) - 1)
    total = sum(len(s) for s in sources) or 1
    ends, position = [], 0
    for word in words:
        position += len(word) + 1
        ends.append(position)

    cuts, start, covered = [], 0, 0
    for k, source in enumerate(sources[:-1]):
        covered += len(source)
        target = ends[-1] * covered / total
        # keep at least one word for this part and for each part after it
        low = min(start + 1, len(words))
        high = max(low, len(words) - (len(sources) - k - 1))
        start = min(range(low, high + 1), key=lambda i: abs(ends[i - 1] - target))
        cuts.append(start)
    bounds = [0] + cuts + [len(words)]
    return [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:])]


def translated(source, texts, lang):
    """The target transcript: one cue per source cue, same timing, src_id → the source cue."""
    from dich.formats.transcript import Cue, Transcript
    return Transcript(lang, [Cue(c.id, c.start, c.end, t, speaker=c.speaker, src_id=c.id)
                             for c, t in zip(source.cues, texts)])
