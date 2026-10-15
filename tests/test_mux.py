from pathlib import Path

from dich.drivers.mux.ffmpeg import mux_command


def test_ducking_and_subtitles():
    command, subs = mux_command(Path("a.mp4"), Path("d.wav"), Path("o.mp4"), [("vi", Path("vi.srt")), (None, Path("s.srt"))])
    text = " ".join(command)
    assert "[0:a]volume=0.15[bg]" in text and "-map 2 " in text and "-map 3 " in text
    assert "language=vie" in text and "mov_text" in text and len(subs) == 2


def test_background_replaces_ducking_and_audio_has_no_subs():
    command, subs = mux_command(Path("a.mp4"), Path("d.wav"), Path("o.mp4"), [("vi", Path("vi.srt"))], Path("bg.wav"))
    text = " ".join(command)
    assert "[2:a][1:a]amix" in text and "volume=" not in text and "-map 3 " in text
    _command, subs = mux_command(Path("a.wav"), Path("d.wav"), Path("o.wav"), [("vi", Path("vi.srt"))])
    assert subs == []
