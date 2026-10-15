import pytest

from dich.core.dag import plan
from dich.core.template import StageConf, get_template, resolve


def by_key(specs):
    return {s.key: s for s in specs}


def stages(*overrides, source="/v/a.mp4", template="default", **change):
    return resolve(get_template(template), source, {"translate": {"driver": "claude"}, **change})


def test_default_template_wires_slots():
    specs = by_key(plan(stages(), ["vi"]))
    assert list(specs) == ["source", "asr", "translate@vi", "dub@vi", "mux@vi"]
    assert specs["asr"].inputs == {"audio": ["source", "audio.original"]}     # no separate stage
    assert specs["dub@vi"].deps == ["source", "translate@vi"]
    assert specs["mux@vi"].inputs["dub"] == ["dub@vi", "audio.dub.vi"]
    assert "background" not in specs["mux@vi"].inputs
    assert specs["translate@vi"].lane == "net" and specs["asr"].lane == "cpu"


def test_refine_slots_into_the_chain():
    specs = by_key(plan(stages(refine={"enabled": True}), ["vi"]))
    assert specs["refine"].inputs == {"transcript": ["asr", "transcript.src"]}
    assert specs["translate@vi"].inputs == {"transcript": ["refine", "transcript.src"]}
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
    assert specs["asr"].skip and specs["translate@vi"].inputs == {"transcript": ["source", "transcript.src"]}


def test_adding_a_language_keeps_existing_keys():
    one = by_key(plan(stages(), ["vi"]))
    two = by_key(plan(stages(), ["vi", "fr"]))
    assert set(one) < set(two)
    assert two["translate@fr"].deps == ["asr"]
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
