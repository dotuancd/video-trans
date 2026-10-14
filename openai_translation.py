# Translate SRT subtitles from English to Vietnamese with the OpenAI API.
# LLMTranslator holds the logic shared with the Claude Code engine (claude_translation.py).
#
# Unlike the local NMT model, an LLM can follow a sentence split over several cues,
# so cues are sent as numbered items and translated one-to-one: no joining or re-splitting.
# Cues go out in chunks with a few previous cues as context; chunks run in parallel.
import json
import os
from concurrent.futures import ThreadPoolExecutor

from srt import Subtitle
from translation import clean

DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
CONTEXT_CUES = 5
ATTEMPTS = 3

SYSTEM_PROMPT = """You translate English video subtitles into Vietnamese.
- Translate naturally and concisely, like professional Vietnamese subtitles.
- Keep technical terms, product names, people's names and code in English \
(e.g. model, prompt, token, API, framework, commit).
- A sentence may be split over several cues: translate so each cue carries the \
Vietnamese for its own part, in the same order.
- Return exactly one translation for every id in "translate", never merge, drop or add ids.
- "context" holds the previous cues for reference only, do not translate them."""

SCHEMA = {
    "name": "subtitle_translations",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    },
}


class LLMTranslator:
    """Chunking, context and id checks shared by the LLM engines; subclasses implement complete()."""
    chunk_size = 60

    def __init__(self, workers=4):
        self.workers = workers

    def complete(self, payload):
        """Return [{"id", "text"}] for payload["translate"], or None when the reply can't be parsed."""
        raise NotImplementedError

    def translate_subtitles(self, subtitles):
        texts = [clean(cue.content) for cue in subtitles]
        chunks = [range(start, min(start + self.chunk_size, len(texts)))
                  for start in range(0, len(texts), self.chunk_size)]
        with ThreadPoolExecutor(self.workers) as pool:
            results = pool.map(lambda chunk: self.translate_chunk(texts, chunk), chunks)
        translations = {}
        for result in results:
            translations.update(result)
        return [Subtitle(index=cue.index, start=cue.start, end=cue.end, content=translations.get(i, ""))
                for i, cue in enumerate(subtitles)]

    def translate_chunk(self, texts, indexes):
        """Map cue index → translation; retries, then halves the chunk when ids come back wrong."""
        wanted = [i for i in indexes if texts[i]]
        if not wanted:
            return {}
        for _ in range(ATTEMPTS):
            result = self.request(texts, wanted)
            if result is not None:
                return result
        if len(wanted) == 1:
            raise RuntimeError(f"No translation returned for cue {wanted[0] + 1}")
        half = len(wanted) // 2
        return {**self.translate_chunk(texts, wanted[:half]), **self.translate_chunk(texts, wanted[half:])}

    def request(self, texts, wanted):
        first = wanted[0]
        payload = {
            "context": [texts[i] for i in range(max(0, first - CONTEXT_CUES), first) if texts[i]],
            "translate": [{"id": i, "text": texts[i]} for i in wanted],
        }
        items = self.complete(payload)
        if items is None:
            return None
        result = {item["id"]: clean(item["text"]) for item in items}
        if sorted(result) != wanted:
            return None
        return result


class OpenAITranslator(LLMTranslator):
    def __init__(self, model=None, workers=4):
        import openai

        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set, e.g. export OPENAI_API_KEY=sk-...")
        super().__init__(workers)
        self.client = openai.OpenAI(max_retries=5)
        self.model = model or DEFAULT_OPENAI_MODEL

    def complete(self, payload):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            response_format={"type": "json_schema", "json_schema": SCHEMA},
        )
        try:
            return json.loads(response.choices[0].message.content)["translations"]
        except (TypeError, ValueError, KeyError):
            return None
