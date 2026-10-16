import pytest

from dich.drivers.translate.common import group_sentences, split_like
from dich.drivers.translate.llm import LLMTranslator, system_prompt
from dich.formats.transcript import Cue


class EchoTranslator(LLMTranslator):
    chunk_size = 10

    def __init__(self, workers=2):
        super().__init__(workers=workers)
        self.requests = 0
        self.payloads = []

    def complete(self, payload):
        self.requests += 1
        self.payloads.append(payload)
        return [{"id": cue["id"], "text": cue["text"].upper()} for sentence in payload["translate"] for cue in sentence]


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
    assert [[c["id"] for c in s] for s in first["translate"]] == [[i] for i in range(8)]
    assert first["next"] == ["So what", "we did", "was this."]
    assert [[c["id"] for c in s] for s in second["translate"]] == [[8, 9, 10], [11]]
    assert second["context"] == [f"cue {i}." for i in range(3, 8)]
    assert "next" not in second


def test_failing_sentence_retries_cue_by_cue():
    class Flaky(EchoTranslator):
        def complete(self, payload):
            items = super().complete(payload)
            # drop an id whenever a sentence of several cues is sent, so only single cues succeed
            return items[1:] if any(len(s) > 1 for s in payload["translate"]) else items

    texts = ["One.", "Two and", "three."]
    translator = Flaky(workers=1)
    assert translator.translate_texts(texts) == ["ONE.", "TWO AND", "THREE."]
    assert [[[c["id"] for c in s] for s in p["translate"]] for p in translator.payloads][-2:] == [[[1]], [[2]]]


def test_checkpoint_resumes_finished_chunks(tmp_path):
    from dich.storage.checkpoint import Checkpoint

    class LimitedTranslator(EchoTranslator):   # e.g. the Claude plan's usage limit hit on the 3rd request
        def complete(self, payload):
            if self.requests == 2:
                raise RuntimeError("usage limit")
            return super().complete(payload)

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
