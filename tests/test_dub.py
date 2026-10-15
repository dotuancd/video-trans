import numpy as np
import pytest
import soundfile as sf

from dich.drivers.dub import vieneu
from dich.formats.transcript import Cue
from dich.media import load_audio


def cues(*spans):
    return [Cue(f"c{i}", a, b, f"Câu số {i + 1}.") for i, (a, b) in enumerate(spans)]


def test_pick_reference_longest_run_within_limit():
    # 0–2 alone; 10–13.2 + 13.4–16 is one run (gap 0.2s) of 6s; 30–31 too short
    assert vieneu.pick_reference(cues((0, 2), (10, 13.2), (13.4, 16), (30, 31), (100, 101))) == (10, 16)


def test_pick_reference_stops_at_gaps_and_max_length():
    assert vieneu.pick_reference(cues((0, 2), (3, 5))) is None
    assert vieneu.pick_reference(cues((50, 70), (90, 91))) == (50, 58)


def test_pick_reference_prefers_after_intro():
    assert vieneu.pick_reference(cues((1, 6), (40, 45), (99, 100))) == (40, 45)


def test_overlapping_text():
    subtitles = cues((0, 2), (10, 13), (20, 23))
    assert vieneu.overlapping_text(subtitles, (19, 24)) == "Câu số 3."
    assert vieneu.overlapping_text(subtitles, (50, 55)) is None


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "a.wav"
    rate = 16000
    sf.write(path, np.arange(rate * 20, dtype=np.float32) / (rate * 20), rate)   # ramp: value ~ time
    return path


def test_load_audio_window(wav):
    audio = np.frombuffer(load_audio(wav, 16000, start=10, duration=4), dtype=np.float32)
    assert abs(len(audio) - 4 * 16000) < 200
    assert abs(audio[len(audio) // 2] - 12 / 20) < 0.01


def test_reference_clip_is_cut_once(wav, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "REF_DIR", tmp_path / "ref")
    clip, window = vieneu.reference_clip(wav, cues((5, 9), (9.2, 11)))
    assert window == (5, 11)
    assert abs(sf.info(clip).duration - 6) < 0.05
    assert vieneu.reference_clip(wav, cues((5, 9), (9.2, 11)))[0] == clip


class FakeDubber:
    def __init__(self):
        self.read = []

    def dub(self, cues, max_speed, progress):
        return np.zeros(vieneu.SAMPLE_RATE, dtype=np.float32), {"sentences": 1, "sped_up": 0, "late": 0, "max_late": 0}

    def synthesize(self, text):
        self.read.append(text)
        return np.zeros(100, dtype=np.float32)


def test_voice_clones_the_video_or_falls_back(wav, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "REF_DIR", tmp_path / "ref")
    driver, logged = vieneu.VieneuDub(), []
    params = vieneu.Params()
    ref, window = driver.voice_for(params, wav, cues((5, 9), (9.2, 11)), logged.append)
    assert window == (5, 11) and "0:05–0:11" in logged[-1]
    assert driver.voice_for(params, None, cues((5, 9)), logged.append) == (None, None)
    assert "không có video" in logged[-1]


def test_preview_reads_the_cloned_moment(wav, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "REF_DIR", tmp_path / "ref")
    driver, fake = vieneu.VieneuDub(), FakeDubber()
    monkeypatch.setattr(driver, "dubber", lambda *args: fake)
    spans = [[0, 1, "Câu một."], [5, 9, "Câu hai."], [9.2, 11, "Câu ba."]]
    with pytest.raises(vieneu.UserError):
        driver.preview({"voice": "source"}, tmp_path)
    result = driver.preview({"voice": "source", "media": str(wav), "timing": spans, "cues": spans}, tmp_path)
    assert result["text"] == "Câu hai."
    result = driver.preview({"voice": "source", "media": str(wav), "timing": spans, "cues": []}, tmp_path)
    assert result["text"] == vieneu.SAMPLE_TEXT
