import pytest

from dich import cli


def test_overrides_from_flags():
    args = cli.build_parser().parse_args(["run", "a.mp4", "-e", "claude", "--voice", "x", "--set", "dub.max_speed=1.5",
                                          "--set", "mux.enabled=false", "--set", "refine.enabled=true"])
    assert cli.overrides_of(args) == {"translate": {"driver": "claude"},
                                      "dub": {"params": {"voice": "x", "max_speed": 1.5}},
                                      "mux": {"enabled": False}, "refine": {"enabled": True}}


def test_run_in_process(tmp_path, monkeypatch, capsys):
    from dich import settings
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "data" / "dich.sqlite")
    video = tmp_path / "a.mp4"
    video.write_bytes(b"v")
    with pytest.raises(SystemExit) as exit:
        cli.main([str(video), "--to", "vi", "--set", "asr.driver=fake", "--set", "translate.driver=fake"])
    assert exit.value.code == 0
    out = capsys.readouterr().out
    assert "[asr] 2 câu" in out and str(tmp_path / "a.vi.srt") in out
