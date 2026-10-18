import numpy as np
import pytest
import soundfile as sf

from dich import voices
from dich.voices import VAD_HOP

QUIET, SPEECH = -80.0, -20.0


def levels(total, *spans):
    """dB levels of `total` seconds: SPEECH inside spans, QUIET elsewhere."""
    out = np.full(int(total / VAD_HOP), QUIET, dtype=np.float32)
    for start, end in spans:
        out[int(start / VAD_HOP):int(end / VAD_HOP)] = SPEECH
    return out


@pytest.fixture(autouse=True)
def no_spectrum(monkeypatch):
    monkeypatch.setattr(voices, "band_ratio", lambda media, start, end: 1.0)


def test_picks_dense_speech_not_sparse():
    # 20–26: 6s of speech; 40–47.6: one window of short phrases with pauses (0.6s spoken, 0.4s silent)
    sparse = [(40 + i, 40.6 + i) for i in range(8)]
    found = voices.candidates("x", levels=levels(100, (20, 26), *sparse))
    assert (found[0]["start"], found[0]["end"]) == pytest.approx((20, 26), abs=VAD_HOP)
    assert 40 <= found[1]["start"] < 48 and found[1]["score"] < found[0]["score"]


def test_candidates_do_not_overlap_and_fit():
    found = voices.candidates("x", levels=levels(200, (20, 45), (60, 66), (100, 104), (150, 157)))
    assert 1 < len(found) <= 5
    for c in found:
        assert 3 <= c["end"] - c["start"] <= 8 + 1e-6
    ordered = sorted(found, key=lambda c: c["start"])
    assert all(a["end"] <= b["start"] for a, b in zip(ordered, ordered[1:]))
    assert found == sorted(found, key=lambda c: -c["score"])


def test_long_speech_is_split_at_its_quietest_moment():
    track = levels(60, (10, 30))
    track[int(16 / VAD_HOP)] = -30.0   # a dip inside the speech, still voiced
    [first, *_] = voices.Voiced(track).segments()
    assert first == pytest.approx((10, 16), abs=VAD_HOP)


def test_intro_scores_less():
    found = voices.candidates("x", levels=levels(100, (2, 8), (50, 56)))
    assert found[0]["start"] == pytest.approx(50, abs=VAD_HOP)


def test_cues_give_the_windows():
    cue_spans = [(10, 13), (13.2, 16), (60, 66)]
    found = voices.candidates("x", cues=cue_spans, levels=levels(100, (60, 66)))
    assert (found[0]["start"], found[0]["end"]) == (60, 66)   # 10–16 has no voice in the audio


def test_short_recording_is_one_window_too_short_none():
    assert [(c["start"], c["end"]) for c in voices.candidates("x", levels=levels(5, (0, 5)))] == [(0, 5)]
    assert voices.candidates("x", levels=levels(2, (0, 2))) == []


def test_speech_band_beats_noise(tmp_path, monkeypatch):
    monkeypatch.undo()
    rate = 16000
    t = np.arange(rate * 4) / rate
    tone, noise = tmp_path / "tone.wav", tmp_path / "noise.wav"
    sf.write(tone, 0.3 * np.sin(2 * np.pi * 1000 * t).astype(np.float32), rate)
    sf.write(noise, (0.3 * np.random.default_rng(0).standard_normal(len(t))).astype(np.float32), rate)
    assert voices.band_ratio(tone, 0, 4) > 0.9
    assert voices.band_ratio(noise, 0, 4) < 0.6


def test_cut(tmp_path):
    rate = 16000
    src = tmp_path / "a.wav"
    sf.write(src, np.zeros(rate * 10, dtype=np.float32), rate)
    clip = voices.cut(src, 2, 7, tmp_path / "out" / "c.wav")
    assert sf.info(clip).samplerate == voices.REF_SAMPLE_RATE and abs(sf.info(clip).duration - 5) < 0.05
