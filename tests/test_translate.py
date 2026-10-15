import pytest

from dich.drivers.translate.common import group_sentences, split_like
from dich.drivers.translate.llm import LLMTranslator, system_prompt
from dich.formats.transcript import Cue


class EchoTranslator(LLMTranslator):
    chunk_size = 10

    def __init__(self):
        super().__init__(workers=2)
        self.requests = 0

    def complete(self, payload):
        self.requests += 1
        return [{"id": item["id"], "text": item["text"].upper()} for item in payload["translate"]]


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
