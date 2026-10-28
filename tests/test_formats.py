from dich.formats.transcript import Cue, Transcript, from_srt, from_words, group_words, text_of, to_srt, to_vtt


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
    assert texts(group_words(words(text), max_duration=60)) == [text]


def test_short_sentences_stay_alone():
    assert texts(group_words(words("Yes. No. Maybe."))) == ["Yes.", "No.", "Maybe."]


def test_transcript_keeps_words_and_ids(tmp_path):
    t = from_words(words(" Hello there. How are you?") + [{"word": " ", "start": 9, "end": 9.1}], "en")
    assert [c.id for c in t.cues] == ["c_0001", "c_0002"]
    assert t.cues[0].words[0] == {"w": "Hello", "s": 0.0, "e": 0.24}
    path = t.save(tmp_path / "t.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"lang": "en"}' and len(lines) == 3
    assert Transcript.load(path) == t


def test_empty_transcript_round_trips(tmp_path):
    t = Transcript("vi", [])
    assert Transcript.load(t.save(tmp_path / "t.jsonl")) == t


def test_srt_and_vtt():
    t = Transcript("vi", [Cue("c_0001", 1.5, 3.25, "Xin chào")])
    srt_text = to_srt(t)
    assert "00:00:01,500 --> 00:00:03,250" in srt_text
    assert from_srt(srt_text, "vi").cues[0].text == "Xin chào"
    assert "00:00:01.500 --> 00:00:03.250" in to_vtt(t)


def test_mindmap_as_markdown():
    from dich.formats.mindmap import to_markdown
    root = {"label": "Bài giảng", "note": "Nói về  mây.", "start": 0, "end": 3725, "children": [
        {"label": "Mở đầu", "start": 0, "end": 75, "children": [
            {"label": "Chào", "note": "Ai nói", "start": 15, "children": [{"label": "Tên", "start": 40}]}]},
        {"label": "Kết", "note": "Tóm lại.", "start": 3600}]}
    assert to_markdown({"lang": "vi", "root": root}) == (
        "# Bài giảng\n\nNói về mây.\n\n"
        "## 1. Mở đầu [0:00 – 1:15]\n\n- [0:15] Chào — Ai nói\n  - [0:40] Tên\n\n"
        "## 2. Kết [1:00:00 – 1:02:05]\n\nTóm lại.\n")
