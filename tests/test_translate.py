import pytest

from dich.drivers.translate.common import group_sentences, split_like
from dich.drivers.translate.llm import LLMTranslator, speech_rate, spoken_seconds, system_prompt
from dich.formats.transcript import Cue


class EchoTranslator(LLMTranslator):
    chunk_size = 10

    def __init__(self, workers=2, rate=None):
        super().__init__(workers=workers, rate=rate)
        self.requests = 0
        self.payloads = []

    def complete(self, payload, prompt=None):
        self.requests += 1
        self.payloads.append(payload)
        return [{"id": cue["id"], "text": cue["text"].upper()} for sentence in payload["translate"] for cue in sentence["cues"]]


def test_progress_counts_cues_up_to_total():
    calls = []
    out = EchoTranslator().translate_texts([f"cue {i}." for i in range(25)], lambda d, t: calls.append((d, t)))
    assert out == [f"CUE {i}." for i in range(25)]
    assert len(calls) == 3 and calls[-1] == (25, 25)


def test_progress_can_stop_translation():
    translator = EchoTranslator()

    def stop(done, total):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        translator.translate_texts([f"cue {i}." for i in range(100)], stop)
    assert translator.requests < 10


def test_prompt_names_languages():
    assert "English video subtitles into Japanese" in system_prompt("en", "ja")


def test_sentences_carry_a_syllable_budget():
    translator = EchoTranslator(rate=4.0)
    texts = ["Short one.", "A longer", "sentence with a pause."]
    timing = [(0.0, 1.0), (2.0, 3.0), (5.0, 6.5)]   # the 2s pause inside the second sentence is not spoken
    translator.translate_texts(texts, timing=timing)
    sentences = translator.payloads[0]["translate"]
    assert [(s["seconds"], s["syllables"]) for s in sentences] == [(1.0, 4), (2.5, 10)]
    assert '"syllables"' in translator.prompt


class TerseTranslator(EchoTranslator):
    """Translates every cue as one word; the length pass pads to the target, or keeps one word for id 1."""

    def complete(self, payload, prompt=None):
        self.requests += 1
        self.payloads.append(payload)
        if prompt is None:
            return [{"id": c["id"], "text": "ngắn"} for s in payload["translate"] for c in s["cues"]]
        return [{"id": c["id"], "text": "ngắn" if c["id"] == 1 else " ".join(["dài"] * (s["target"] // len(s["cues"])))}
                for s in payload["translate"] for c in s["cues"]]


def test_length_pass_rewrites_sentences_off_budget():
    translator = TerseTranslator(rate=4.0)
    texts = ["One two three four five.", "Six seven eight.", "Nine."]
    timing = [(0.0, 2.0), (2.0, 3.0), (3.0, 3.5)]   # budgets 8 and 4 syllables; the last is too short to fit
    out = translator.translate_texts(texts, timing=timing)
    assert out == ["dài " * 7 + "dài", "ngắn", "ngắn"]
    fit = translator.payloads[-1]["translate"]
    assert [(s["syllables"], s["target"]) for s in fit] == [(1, 8), (1, 4)]
    assert fit[0]["cues"] == [{"id": 0, "source": texts[0], "text": "ngắn"}]
    assert translator.fitted == (2, 1)   # id 1 came back no closer: the first translation stays


def test_no_length_pass_without_rate():
    translator = TerseTranslator()
    translator.translate_texts(["One two three four five."], timing=[(0.0, 2.0)])
    assert translator.requests == 1 and translator.fitted is None


def test_no_budget_without_timing_or_rate():
    for translator, timing in ((EchoTranslator(rate=4.0), None), (EchoTranslator(), [(0.0, 1.0)])):
        translator.translate_texts(["One."], timing=timing)
        assert translator.payloads[0]["translate"] == [{"cues": [{"id": 0, "text": "One."}]}]
    assert '"syllables"' not in EchoTranslator().prompt
    assert '"syllables"' not in system_prompt("en", "ja")
    assert speech_rate("vi") and speech_rate("ja") is None


def test_spoken_seconds_drops_long_pauses():
    assert spoken_seconds([(0.0, 1.0), (1.5, 2.0)]) == 2.0
    assert spoken_seconds([(0.0, 1.0), (3.0, 4.0)]) == 2.0


def test_group_and_split():
    cues = [Cue("a", 0, 1, "When we arrived"), Cue("b", 1, 2, "nobody was there."), Cue("c", 5, 6, "Then")]
    assert group_sentences(cues) == [[0, 1], [2]]
    assert group_sentences(cues[:1] + [Cue("x", 9, 10, "later")], max_gap=1) == [[0], [1]]
    assert split_like("khi chúng tôi đến không có ai", ["When we arrived", "nobody was there."]) == [
        "khi chúng tôi", "đến không có ai"]


def test_chunks_never_cut_a_sentence():
    # 8 one-cue sentences, then one sentence over 3 cues that would straddle the 10-cue chunk boundary
    texts = [f"cue {i}." for i in range(8)] + ["So what", "we did", "was this.", "Done."]
    translator = EchoTranslator(workers=1)
    assert translator.translate_texts(texts) == [t.upper() for t in texts]
    first, second = translator.payloads
    assert [[c["id"] for c in s["cues"]] for s in first["translate"]] == [[i] for i in range(8)]
    assert first["next"] == ["So what", "we did", "was this."]
    assert [[c["id"] for c in s["cues"]] for s in second["translate"]] == [[8, 9, 10], [11]]
    assert second["context"] == [f"cue {i}." for i in range(3, 8)]
    assert "next" not in second


def test_failing_sentence_retries_cue_by_cue():
    class Flaky(EchoTranslator):
        def complete(self, payload, prompt=None):
            items = super().complete(payload, prompt)
            # drop an id whenever a sentence of several cues is sent, so only single cues succeed
            return items[1:] if any(len(s["cues"]) > 1 for s in payload["translate"]) else items

    texts = ["One.", "Two and", "three."]
    translator = Flaky(workers=1)
    assert translator.translate_texts(texts) == ["ONE.", "TWO AND", "THREE."]
    assert [[[c["id"] for c in s["cues"]] for s in p["translate"]] for p in translator.payloads][-2:] == [[[1]], [[2]]]


def test_checkpoint_resumes_finished_chunks(tmp_path):
    from dich.storage.checkpoint import Checkpoint

    class LimitedTranslator(EchoTranslator):   # e.g. the Claude plan's usage limit hit on the 3rd request
        def complete(self, payload, prompt=None):
            if self.requests == 2:
                raise RuntimeError("usage limit")
            return super().complete(payload, prompt)

    texts = [f"cue {i}." for i in range(30)]   # 3 chunks of 10
    first = LimitedTranslator(workers=1)
    first.checkpoint = Checkpoint(tmp_path / "cp", "fp1")
    with pytest.raises(RuntimeError):
        first.translate_texts(texts)

    again = EchoTranslator(workers=1)
    again.checkpoint = Checkpoint(tmp_path / "cp", "fp1")
    assert again.checkpoint.resumed == 2
    assert again.translate_texts(texts) == [t.upper() for t in texts]
    assert again.requests == 1                     # only the chunk not done before

    other = Checkpoint(tmp_path / "cp", "fp2")     # other params or inputs: start over
    assert other.resumed == 0 and not (tmp_path / "cp").exists()


def test_checkpoint_skips_a_half_written_line(tmp_path):
    from dich.storage.checkpoint import PIECES, Checkpoint

    Checkpoint(tmp_path, "fp").put("a", {"0": "x"})
    with (tmp_path / PIECES).open("a") as file:
        file.write('{"key": "b", "val')
    assert Checkpoint(tmp_path, "fp").get("a") == {"0": "x"}


def test_claude_requests_are_logged(tmp_path, monkeypatch):
    import json
    import subprocess

    from dich.drivers.translate.llm import FIT_PROMPT, ClaudeCodeTranslator, RequestLog, read_requests

    def run(command, input, **kwargs):
        cues = [cue for sentence in json.loads(input)["translate"] for cue in sentence["cues"]]
        if len(cues) > 1:   # wrong ids: the chunk is split, then each half asked again
            cues = cues[:1]
        output = {"translations": [{"id": c["id"], "text": c["text"].upper()} for c in cues]}
        reply = {"structured_output": output, "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}
        return subprocess.CompletedProcess(command, 0, json.dumps(reply), "")

    monkeypatch.setattr(subprocess, "run", run)
    translator = ClaudeCodeTranslator("sonnet", workers=1)
    translator.request_log = RequestLog(tmp_path / "requests.jsonl")
    assert translator.translate_texts(["One.", "Two."]) == ["ONE.", "TWO."]
    rows = read_requests(tmp_path)
    assert [(r["ok"], r["cues"]) for r in rows] == [(False, 2)] * 3 + [(True, 1)] * 2
    assert rows[0]["engine"] == "claude" and rows[0]["model"] == "sonnet" and rows[0]["kind"] == "translate"
    assert rows[0]["payload"]["translate"][0]["cues"][0]["text"] == "One."
    assert '"ONE."' in rows[0]["reply"] and rows[0]["usage"]["cost_usd"] == 0.01
    translator.complete({"translate": [{"cues": [{"id": 0, "text": "x"}]}]}, FIT_PROMPT)
    assert read_requests(tmp_path)[-1]["kind"] == "fit"


def test_claude_error_is_logged(tmp_path, monkeypatch):
    import json
    import subprocess

    from dich.drivers import UserError
    from dich.drivers.translate.llm import ClaudeCodeTranslator, RequestLog, read_requests

    reply = json.dumps({"is_error": True, "result": "usage limit"})
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 1, reply, ""))
    translator = ClaudeCodeTranslator("sonnet", workers=1)
    translator.request_log = RequestLog(tmp_path / "requests.jsonl")
    with pytest.raises(UserError):
        translator.translate_texts(["One."])
    [row] = read_requests(tmp_path)
    assert not row["ok"] and "usage limit" in row["error"]


def test_read_requests_skips_a_half_written_line(tmp_path):
    from dich.drivers.translate.llm import REQUESTS, read_requests

    assert read_requests(tmp_path) == []
    (tmp_path / REQUESTS).write_text('{"a": 1}\n{"b"', encoding="utf-8")
    assert read_requests(tmp_path) == [{"a": 1}]


def test_prompt_carries_the_brief():
    from dich.drivers.translate.llm import FIT_PROMPT, with_brief
    assert "Video brief" in system_prompt("en", "vi", brief="Lĩnh vực: tài chính") and "tài chính" in system_prompt("en", "vi", brief="Lĩnh vực: tài chính")
    assert system_prompt("en", "vi", brief="  ") == system_prompt("en", "vi")
    assert with_brief(FIT_PROMPT, "x").startswith(FIT_PROMPT)


class BriefCtx:
    def __init__(self, tmp_path, title="Bear markets explained", langs=("vi",)):
        from dich.logging.events import EventLog
        self.dir = tmp_path
        self.log = EventLog(tmp_path / "events.jsonl", lambda *a: None)
        self.info = {"title": title, "langs": list(langs)}

    def job_info(self):
        return dict(self.info)

    def output(self, name):
        return self.dir / name


def brief_inputs(tmp_path, meta=None):
    from types import SimpleNamespace as NS
    from dich.formats.transcript import Transcript
    transcript = Transcript("en", [Cue(1, 0, 1, "The bear market is here."), Cue(2, 1, 2, "Buy the dip.")])
    inputs = {"transcript": NS(path=transcript.save(tmp_path / "t.jsonl"))}
    if meta is not None:
        (tmp_path / "meta.json").write_text(__import__("json").dumps(meta))
        inputs["meta"] = NS(path=tmp_path / "meta.json")
    return inputs


def test_brief_from_the_users_summary_sends_no_request(tmp_path, monkeypatch):
    from dich.drivers.brief import llm as brief
    monkeypatch.setattr(brief, "make_translator", lambda engine: pytest.fail("no request expected"))
    out = brief.LLMBrief().run(BriefCtx(tmp_path), brief_inputs(tmp_path, {"title": "T", "chapters": ["Intro", "Crypto"]}),
                               brief.Params(summary="Video về chứng khoán.", instructions="Xưng 'mình'"))
    text = out["brief"].read_text(encoding="utf-8")
    assert "Video về chứng khoán." in text and "Xưng 'mình'" in text and "Intro; Crypto" in text and "Tiêu đề: T" in text


def test_brief_generated_with_one_request(tmp_path, monkeypatch):
    from dich.drivers.brief import llm as brief
    from dich.drivers.translate.llm import Usage
    asked = []

    class Fake:
        model = "fake"
        usage = Usage()
        request_log = None

        def ask(self, system, text):
            asked.append((system, text))
            return "Lĩnh vực: tài chính"

    monkeypatch.setattr(brief, "make_translator", lambda engine: Fake())
    out = brief.LLMBrief().run(BriefCtx(tmp_path), brief_inputs(tmp_path), brief.Params(engine="openai"))
    assert len(asked) == 1 and "Vietnamese" in asked[0][0] and "bear market" in asked[0][1]
    text = out["brief"].read_text(encoding="utf-8")
    assert text.startswith("Lĩnh vực: tài chính") and "Bear markets explained" in text   # job title as metadata


def test_long_transcript_is_sampled_across_the_video():
    from dich.drivers.brief.llm import sample
    words = [f"w{i}" for i in range(1000)]
    out = sample(words, max_words=100, samples=4).split()
    assert out[0] == "w0" and out[-1] == "w999" and len([w for w in out if w != "[…]"]) == 100


def test_english_to_vietnamese_gets_its_own_rules():
    from dich.drivers.translate.llm import FIT_PROMPT, PAIR_RULES, fit_system_prompt
    rules = PAIR_RULES[("en", "vi")]
    assert rules in system_prompt("en", "vi") and rules in system_prompt("en", "vi", timed=True, brief="x")
    for source, target in (("en", "ja"), ("ja", "vi"), (None, "vi")):
        assert rules not in system_prompt(source, target)
    assert rules.rstrip() in fit_system_prompt("en", "vi", brief="x") and fit_system_prompt("en", "ja") == FIT_PROMPT
