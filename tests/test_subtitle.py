import json
from pathlib import Path

from subtitle import group_words, text_of, words_path, write_words


def words(text, gap_after=None, step=0.3):
    """One word every `step` seconds; `gap_after` maps a word index to extra silence after it."""
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append({"word": w, "start": t, "end": t + step * 0.8})
        t += step + (gap_after or {}).get(i, 0)
    return out


def texts(cues):
    return [text_of(c) for c in cues]


def test_sentence_end_and_pause_break():
    assert texts(group_words(words("Hello there. How are you doing today?"))) == [
        "Hello there.", "How are you doing today?"]
    assert texts(group_words(words("we stopped here and then we went on", {2: 1.0}))) == [
        "we stopped here", "and then we went on"]


def test_long_cue_cut_at_last_comma():
    text = "When we arrived at the old station near the river, nobody was there to meet us so we walked home alone."
    cues = texts(group_words(words(text), max_duration=60))
    assert cues[0] == "When we arrived at the old station near the river,"
    assert cues[1].startswith("nobody")


def test_orphan_tail_joins_previous_cue():
    text = "this is a rather long sentence without any punctuation that keeps going on and on until it ends."
    cues = texts(group_words(words(text), max_duration=60))
    assert cues == [text]


def test_short_sentences_stay_alone():
    assert texts(group_words(words("Yes. No. Maybe."))) == ["Yes.", "No.", "Maybe."]


def test_words_saved_next_to_srt(tmp_path):
    assert words_path(Path("x/a.b.srt")) == Path("x/a.b.words.json")
    path = tmp_path / "a.words.json"
    write_words([{"word": " Chào", "start": 0.12345, "end": 0.5}, {"word": " ", "start": 0.5, "end": 0.6}], path)
    assert json.loads(path.read_text(encoding="utf-8")) == [{"word": "Chào", "start": 0.123, "end": 0.5}]
