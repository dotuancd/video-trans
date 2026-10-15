from datetime import timedelta

import pytest
from srt import Subtitle

from llm_translation import LLMTranslator


class EchoTranslator(LLMTranslator):
    chunk_size = 10

    def __init__(self):
        super().__init__(workers=2)
        self.requests = 0

    def complete(self, payload):
        self.requests += 1
        return [{"id": item["id"], "text": item["text"].upper()} for item in payload["translate"]]


def cues(n):
    return [Subtitle(index=i + 1, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"cue {i}.")
            for i in range(n)]


def test_progress_counts_cues_up_to_total():
    calls = []
    translated = EchoTranslator().translate_subtitles(cues(25), lambda done, total: calls.append((done, total)))
    assert [s.content for s in translated] == [f"CUE {i}." for i in range(25)]
    assert len(calls) == 3 and all(total == 25 for _, total in calls)
    assert [done for done, _ in calls] == sorted(done for done, _ in calls) and calls[-1][0] == 25


def test_progress_can_stop_translation():
    translator = EchoTranslator()

    def stop(done, total):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        translator.translate_subtitles(cues(100), stop)
    assert translator.requests < 10
