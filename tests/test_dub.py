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

    def dub(self, cues, max_speed, min_speed, progress, voiced=None):
        return np.zeros(vieneu.SAMPLE_RATE, dtype=np.float32), {"sentences": 1, "sped_up": 0, "slowed": 0, "late": 0, "max_late": 0}

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


class FakeTTS:
    def infer(self, text, voice=None):
        return np.sin(np.arange(vieneu.SAMPLE_RATE, dtype=np.float32) / 10)   # 1s of tone


def dub_lengths(tmp_path, monkeypatch, spans, **kwargs):
    monkeypatch.setattr(vieneu, "CACHE_DIR", tmp_path / "tts")
    dubber = vieneu.Dubber(tts=FakeTTS())
    seconds = []
    monkeypatch.setattr(vieneu, "change_speed",
                        lambda audio, speed: seconds.append(len(audio) / speed / vieneu.SAMPLE_RATE) or audio[:int(len(audio) / speed)])
    _track, stats = dubber.dub(cues(*spans), **kwargs)
    return stats, seconds


def test_dub_slows_short_sentences_to_fill_the_original(tmp_path, monkeypatch):
    stats, seconds = dub_lengths(tmp_path, monkeypatch, [(0, 1.1), (5, 7), (10, 11.05)])
    assert stats["slowed"] == 1 and stats["sped_up"] == 0
    assert seconds == [pytest.approx(1 / 0.85)]          # capped at min_speed, not stretched to 2s
    stats, _ = dub_lengths(tmp_path, monkeypatch, [(0, 1.1)], min_speed=0.5)
    assert stats["slowed"] == 0                          # 1s of 1.1s is full enough


def test_dub_slowing_can_be_disabled(tmp_path, monkeypatch):
    stats, seconds = dub_lengths(tmp_path, monkeypatch, [(0, 3)], min_speed=1.0)
    assert stats["slowed"] == 0 and seconds == []


def test_change_speed_slows_down():
    audio = np.sin(np.arange(vieneu.SAMPLE_RATE, dtype=np.float32) / 10)
    assert len(vieneu.change_speed(audio, 0.8)) == pytest.approx(vieneu.SAMPLE_RATE / 0.8, rel=0.02)


def test_voiced_trims_padding_around_speech():
    hop = vieneu.VAD_HOP
    levels = np.full(int(10 / hop), -80.0)       # 10s of silence…
    levels[int(2.5 / hop):int(4 / hop)] = -20     # …speech 2.5–4s
    levels[int(7 / hop):int(7.1 / hop)] = -20     # …a 0.1s blip at 7s
    voiced = vieneu.Voiced(levels)
    assert voiced.trim(2, 5) == pytest.approx((2.5, 4))
    assert voiced.trim(3, 5) == pytest.approx((3, 4))      # never wider than the cue
    assert voiced.trim(6, 8) == (6, 8)                     # a blip is not trusted
    assert voiced.trim(8, 9) == (8, 9)                     # silence: keep the timing


def test_dub_slows_to_the_voiced_span_not_the_cue(tmp_path, monkeypatch):
    hop = vieneu.VAD_HOP
    levels = np.full(int(10 / hop), -80.0)
    levels[int(0.5 / hop):int(1.6 / hop)] = -20   # cue 0–3 but speech only 0.5–1.6
    stats, seconds = dub_lengths(tmp_path, monkeypatch, [(0, 3)], voiced=vieneu.Voiced(levels))
    assert stats["slowed"] == 0 and seconds == []   # 1s fills 1.1s of speech: no stretching to 3s


def test_loudness(wav):
    from dich.media import loudness
    levels = loudness(wav, 0.5)
    assert len(levels) == 40 and levels[-1] > levels[1]


def test_dub_live_chunks_add_up_to_the_track(tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "CACHE_DIR", tmp_path / "tts")
    monkeypatch.setattr(vieneu, "LIVE_FIRST", 2.0)
    monkeypatch.setattr(vieneu, "LIVE_CHUNK", 3.0)
    chunks = []
    track, _stats = vieneu.Dubber(tts=FakeTTS()).dub(
        cues(*[(i * 1.5, i * 1.5 + 1) for i in range(8)]), on_chunk=lambda start, audio: chunks.append((start, audio)))
    assert len(chunks) > 2 and chunks[0][0] == 0
    position = 0
    for start, audio in chunks:   # back to back
        assert round(start * vieneu.SAMPLE_RATE) == position
        position += len(audio)
    assert len(chunks[0][1]) / vieneu.SAMPLE_RATE <= 2.0 + 1.5
    assert np.array_equal(np.concatenate([audio for _start, audio in chunks]), track)


def test_dub_live_chunks_survive_rounding_at_the_cursor(tmp_path, monkeypatch):
    # 1.3s + 1s of audio: int(2.3 * 48000) is one sample before where the first clip ends
    monkeypatch.setattr(vieneu, "CACHE_DIR", tmp_path / "tts")
    monkeypatch.setattr(vieneu, "LIVE_FIRST", 1.0)
    chunks = []
    track, _stats = vieneu.Dubber(tts=FakeTTS()).dub(
        cues((1.3, 2.3), (1.5, 2.5), (5, 6)), max_speed=1.0, min_speed=1.0,
        on_chunk=lambda start, audio: chunks.append(audio))
    assert len(chunks) > 1
    assert np.array_equal(np.concatenate(chunks), track)


def test_tts_threads_setting_wins(monkeypatch):
    monkeypatch.setattr(vieneu.settings, "TTS_THREADS", 3)
    assert vieneu.tts_threads() == 3


def test_tts_threads_default_off_mac(monkeypatch):
    monkeypatch.setattr(vieneu.settings, "TTS_THREADS", 0)
    monkeypatch.setattr(vieneu.sys, "platform", "linux")
    assert vieneu.tts_threads() == 0


def test_trim_edges_cuts_the_voice_models_silence():
    rate = vieneu.SAMPLE_RATE
    tone = np.sin(np.arange(rate, dtype=np.float32) / 10)
    audio = np.concatenate([np.zeros(rate // 5, dtype=np.float32), tone, np.zeros(rate // 5, dtype=np.float32)])
    trimmed = vieneu.trim_edges(audio)
    assert len(trimmed) / rate == pytest.approx(1 + 2 * vieneu.TRIM_PAD, abs=vieneu.VAD_HOP)
    assert np.array_equal(vieneu.trim_edges(np.zeros(rate, dtype=np.float32)), np.zeros(rate, dtype=np.float32))


def dub_starts(tmp_path, monkeypatch, spans, **kwargs):
    """(start seconds of every sentence in the dub, stats): where each run of tone begins."""
    monkeypatch.setattr(vieneu, "CACHE_DIR", tmp_path / "tts")
    track, stats = vieneu.Dubber(tts=FakeTTS()).dub(cues(*spans), **kwargs)
    loud = np.flatnonzero(np.abs(track) > 1e-3)
    breaks = np.flatnonzero(np.diff(loud) > vieneu.SAMPLE_RATE // 100)
    return [loud[0] / vieneu.SAMPLE_RATE] + [loud[b + 1] / vieneu.SAMPLE_RATE for b in breaks], stats


def test_dub_keeps_a_breath_between_sentences_running_late(tmp_path, monkeypatch):
    # 1s of speech into a 0.5s slot, then a 0.6s pause in the original: the second sentence still waits
    # MAX_BREATH after the first instead of following it straight away
    starts, stats = dub_starts(tmp_path, monkeypatch, [(0, 0.5), (1.1, 2.1)], max_speed=1.0, min_speed=1.0)
    assert starts[1] == pytest.approx(1.0 + vieneu.MAX_BREATH, abs=0.01)
    assert stats["no_breath"] == 0


def test_dub_on_time_sentences_keep_their_start(tmp_path, monkeypatch):
    starts, stats = dub_starts(tmp_path, monkeypatch, [(0, 1), (2, 3)], max_speed=1.0, min_speed=1.0)
    assert starts == pytest.approx([0, 2], abs=0.01) and stats["late"] == 0
    assert stats["pause_err"] == pytest.approx(0, abs=0.01)
