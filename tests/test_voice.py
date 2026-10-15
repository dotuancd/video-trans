from datetime import timedelta
from urllib.parse import unquote

import numpy as np
import pytest
import soundfile as sf
import srt
from fastapi.testclient import TestClient

import tts
from dich import Steps, StepSpec, load_audio, plan_steps
from dich_server import api


def cues(*spans):
    return [srt.Subtitle(i + 1, timedelta(seconds=a), timedelta(seconds=b), f"Câu số {i + 1}.")
            for i, (a, b) in enumerate(spans)]


def test_pick_reference_longest_run_within_limit():
    # 0–2 alone; 10–13.2 + 13.4–16 is one run (gap 0.2s) of 6s; 30–31 too short
    assert tts.pick_reference(cues((0, 2), (10, 13.2), (13.4, 16), (30, 31), (100, 101))) == (10, 16)


def test_pick_reference_stops_at_gaps_and_max_length():
    # gap of 1s splits the runs; a 20s cue is cut to 8s
    assert tts.pick_reference(cues((0, 2), (3, 5))) is None
    assert tts.pick_reference(cues((50, 70), (90, 91))) == (50, 58)


def test_pick_reference_prefers_after_intro():
    # both runs are 5s; the first is inside the first 10% of the video
    assert tts.pick_reference(cues((1, 6), (40, 45), (99, 100))) == (40, 45)


def test_overlapping_text():
    subtitles = cues((0, 2), (10, 13), (20, 23))
    assert tts.overlapping_text(subtitles, (19, 24)) == "Câu số 3."
    assert tts.overlapping_text(subtitles, (50, 55)) is None


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
    monkeypatch.setattr(tts, "REF_DIR", tmp_path / "ref")
    clip, window = tts.reference_clip(wav, cues((5, 9), (9.2, 11)))
    assert window == (5, 11)
    assert abs(sf.info(clip).duration - 6) < 0.05
    assert tts.reference_clip(wav, cues((5, 9), (9.2, 11)))[0] == clip


def test_plan_dub_gets_the_video(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"")
    out = tmp_path / "out"
    dub = next(s for s in plan_steps(media, "media", tmp_path, out, dub=True) if s.name == "dub")
    assert dub.inputs == (out / "a.vi.srt", media)
    vi = tmp_path / "b.vi.srt"
    assert next(s for s in plan_steps(vi, "vi_srt", tmp_path, None, dub=True)).inputs == (vi,)


class FakeDubber:
    def dub(self, subtitles, max_speed, progress):
        return np.zeros(tts.SAMPLE_RATE, dtype=np.float32), {"sentences": 1, "sped_up": 0, "late": 0, "max_late": 0}

    def synthesize(self, text):
        return np.zeros(100, dtype=np.float32)


def test_dub_clones_the_video_voice_or_falls_back(wav, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "REF_DIR", tmp_path / "ref")
    vi = tmp_path / "a.vi.srt"
    vi.write_text(srt.compose(cues((5, 9), (9.2, 11))), encoding="utf-8")
    steps, used = Steps(), []
    monkeypatch.setattr(steps, "dubber", lambda voice, ref, int8: used.append((voice, ref)) or FakeDubber())
    options = {"voice": "source"}
    message = steps.run(StepSpec("dub", (vi, wav), tmp_path / "a.vi.wav"), options)
    assert "original voice 0:05–0:11" in message and used[-1][1].parent == tmp_path / "ref"
    message = steps.run(StepSpec("dub", (vi,), tmp_path / "a.vi.wav"), options)   # old job, a.wav found next to it
    assert used[-1][1] is not None
    (tmp_path / "lone.vi.srt").write_text(vi.read_text(encoding="utf-8"), encoding="utf-8")
    message = steps.run(StepSpec("dub", (tmp_path / "lone.vi.srt",), tmp_path / "lone.wav"), options)
    assert "no video" in message and used[-1] == ("source", None)


def test_preview_reads_the_cloned_moment(wav, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "REF_DIR", tmp_path / "ref")
    vi = tmp_path / "a.vi.srt"
    vi.write_text(srt.compose(cues((0, 1), (5, 9), (9.2, 11))), encoding="utf-8")
    app = api.create_app(tmp_path, ":memory:")
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.pipeline.steps, "dubber", lambda *args: FakeDubber())
        missing = client.post("/api/voices/preview", json={"voice": "source"})
        assert missing.status_code == 400
        response = client.post("/api/voices/preview", json={"voice": "source", "media": str(wav), "subtitles": str(vi)})
        assert response.status_code == 200
        assert unquote(response.headers["x-preview-text"]) == "Câu số 2."   # overlaps the 5–11s clip most


def test_preview_finds_the_files_next_to_the_video(wav, tmp_path, monkeypatch):
    # a new job only knows the video: its .srt times the clip, its .vi.srt (once translated) is read
    monkeypatch.setattr(tts, "REF_DIR", tmp_path / "ref")
    (tmp_path / "a.srt").write_text(srt.compose(cues((0, 1), (5, 9), (9.2, 11))), encoding="utf-8")
    app = api.create_app(tmp_path, ":memory:")
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.pipeline.steps, "dubber", lambda *args: FakeDubber())
        before = client.post("/api/voices/preview", json={"voice": "source", "media": str(wav)})
        assert before.status_code == 200
        assert unquote(before.headers["x-preview-text"]) == api.SAMPLE_TEXT   # nothing Vietnamese to read yet
        (tmp_path / "a.vi.srt").write_text(srt.compose(cues((0, 1), (5, 9), (9.2, 11))), encoding="utf-8")
        for body in ({"media": str(wav)}, {"subtitles": str(tmp_path / "a.srt")}):
            response = client.post("/api/voices/preview", json={"voice": "source", **body})
            assert unquote(response.headers["x-preview-text"]) == "Câu số 2."
