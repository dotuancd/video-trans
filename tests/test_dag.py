import pytest

from dich.core.dag import plan
from dich.core.template import StageConf, get_template, resolve


def by_key(specs):
    return {s.key: s for s in specs}


def stages(*overrides, source="/v/a.mp4", template="default", **change):
    return resolve(get_template(template), source, {"translate": {"driver": "claude"}, **change})


def test_default_template_wires_slots():
    specs = by_key(plan(stages(), ["vi"]))
    assert list(specs) == ["source", "asr", "brief", "translate@vi", "dub@vi", "mux@vi"]
    assert specs["asr"].inputs == {"audio": ["source", "audio.original"]}     # no separate stage
    assert specs["dub@vi"].deps == ["source", "translate@vi"]
    assert specs["mux@vi"].inputs["dub"] == ["dub@vi", "audio.dub.vi"]
    assert "background" not in specs["mux@vi"].inputs
    assert specs["translate@vi"].lane == "net" and specs["asr"].lane == "cpu"


def test_refine_slots_into_the_chain():
    specs = by_key(plan(stages(refine={"enabled": True}), ["vi"]))
    assert specs["refine"].inputs == {"transcript": ["asr", "transcript.src"]}
    assert specs["translate@vi"].inputs == {"transcript": ["refine", "transcript.src"], "brief": ["brief", "brief"]}
    assert specs["mux@vi"].inputs["original_subtitles"] == ["refine", "transcript.src"]


def test_per_language_and_unsupported():
    specs = by_key(plan(stages(), ["vi", "ja"]))
    assert specs["translate@ja"].skip is None
    assert "không hỗ trợ" in specs["dub@ja"].skip
    assert specs["mux@ja"].skip.startswith("Thiếu đầu vào")
    assert specs["mux@vi"].skip is None


def test_subtitle_source_skips_asr_and_media_stages():
    specs = by_key(plan(stages(source="/v/a.vi.srt"), ["vi"]))
    assert specs["asr"].skip.startswith("Thiếu")
    assert specs["translate@vi"].skip.startswith("Thiếu")    # needs transcript.src
    assert specs["dub@vi"].skip is None and specs["dub@vi"].inputs == {"transcript": ["source", "transcript.vi"]}
    assert specs["mux@vi"].skip.startswith("Thiếu")


def test_original_srt_source_is_translated():
    specs = by_key(plan(stages(source="/v/a.srt", template="subs-only"), ["vi"]))
    assert specs["asr"].skip and specs["translate@vi"].inputs["transcript"] == ["source", "transcript.src"]


def test_adding_a_language_keeps_existing_keys():
    one = by_key(plan(stages(), ["vi"]))
    two = by_key(plan(stages(), ["vi", "fr"]))
    assert set(one) < set(two)
    assert two["translate@fr"].deps == ["asr", "brief"]
    assert two["asr"].inputs == one["asr"].inputs


def test_gate_and_params_validated():
    specs = by_key(plan(stages(template="dub-review"), ["vi"]))
    assert specs["dub@vi"].gate and not specs["mux@vi"].gate
    assert specs["dub@vi"].params["max_speed"] == 1.3
    with pytest.raises(Exception):
        resolve(get_template("default"), "/a.mp4", {"dub": {"params": {"max_speed": 9}}})
    with pytest.raises(KeyError):
        resolve(get_template("default"), "/a.mp4", {"asr": {"driver": "nope"}})


def test_url_source():
    specs = by_key(plan(stages(source="https://youtu.be/x"), ["vi"]))
    assert specs["source"].driver == "url" and specs["source"].lane == "net"


def test_video_with_existing_subtitles_skips_their_stages(tmp_path):
    from dich.drivers.source.file import siblings
    video = tmp_path / "a.b.mp4"
    for name in ("a.b.mp4", "a.b.srt", "a.b.vi.srt", "a.b.x.srt", "a.srt"):
        (tmp_path / name).write_text("")
    found = siblings(video)
    assert found == {"src": str(tmp_path / "a.b.srt"), "vi": str(tmp_path / "a.b.vi.srt")}
    specs = by_key(plan(resolve(get_template("default"), str(video),
                                {"source": {"params": {"subtitles": {"vi": found["vi"]}}}}), ["vi"]))
    assert specs["asr"].skip is None and specs["translate@vi"].skip == "Đã có sẵn từ nguồn"
    assert specs["dub@vi"].inputs["transcript"] == ["source", "transcript.vi"]
    assert specs["mux@vi"].skip is None


def test_fit_for_dub_off_by_default_and_only_with_a_dub():
    translate = lambda confs: next(c for c in confs if c.stage == "translate").params["fit_dub"]
    on = {"driver": "claude", "params": {"fit_dub": True}}
    assert translate(stages()) is False                       # off by default
    assert translate(stages(translate=on)) is True
    assert translate(stages(template="subs-only", translate=on)) is False
    assert translate(stages(dub={"enabled": False}, translate=on)) is False


def test_brief_runs_once_for_every_language_and_follows_the_translation():
    specs = by_key(plan(stages(), ["vi", "fr"]))
    assert specs["brief"].lang is None and specs["brief"].params["engine"] == "claude"
    assert specs["translate@vi"].inputs["brief"] == specs["translate@fr"].inputs["brief"] == ["brief", "brief"]


def test_brief_is_off_without_an_llm_translation():
    local = by_key(plan(resolve(get_template("default"), "/v/a.mp4", {"translate": {"driver": "local"}}), ["vi"]))
    assert "brief" not in local and "brief" not in local["translate@vi"].inputs
    chosen = resolve(get_template("default"), "/v/a.mp4",
                     {"translate": {"driver": "claude"}, "brief": {"params": {"engine": "openai"}}})
    assert next(c for c in chosen if c.stage == "brief").params["engine"] == "openai"


def test_mindmap_reads_the_brief_but_does_not_keep_it_on():
    on = {"mindmap": {"enabled": True}}
    specs = by_key(plan(stages(**on), ["vi"]))
    assert specs["mindmap"].inputs["brief"] == ["brief", "brief"] and specs["mindmap"].params["engine"] == "claude"
    transcribe = by_key(plan(resolve(get_template("transcribe"), "/v/a.mp4", on), []))
    assert "brief" not in transcribe and "brief" not in transcribe["mindmap"].inputs
    assert transcribe["mindmap"].params["engine"] == "auto"
    chosen = by_key(plan(stages(mindmap={"enabled": True, "params": {"engine": "openai", "detail": "gọn"}}), ["vi"]))
    assert chosen["mindmap"].params == {"detail": "gọn", "instructions": "", "engine": "openai"}
