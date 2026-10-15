# Translate SRT subtitles from English to Vietnamese with an LLM: the OpenAI API, or the Claude Code
# CLI (`claude -p`) so usage counts against a Claude Pro/Max plan instead of being billed per token.
#
# Unlike the local NMT model, an LLM can follow a sentence split over several cues,
# so cues are sent as numbered items and translated one-to-one: no joining or re-splitting.
# Cues go out in chunks with a few previous cues as context; chunks run in parallel.
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import settings
from srt import Subtitle
from translation import clean

DEFAULT_OPENAI_MODEL = settings.OPENAI_MODEL
DEFAULT_CLAUDE_MODEL = settings.CLAUDE_MODEL
CONTEXT_CUES = 5
ATTEMPTS = 3
CLAUDE_TIMEOUT = 600

SYSTEM_PROMPT = """You translate English video subtitles into Vietnamese.
- Translate naturally and concisely, like professional Vietnamese subtitles.
- Keep technical terms, product names, people's names and code in English \
(e.g. model, prompt, token, API, framework, commit).
- A sentence may be split over several cues: translate so each cue carries the \
Vietnamese for its own part, in the same order.
- Return exactly one translation for every id in "translate", never merge, drop or add ids.
- "context" holds the previous cues for reference only, do not translate them."""

SCHEMA = {
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
}


class LLMTranslator:
    """Chunking, context and id checks shared by the LLM engines; subclasses implement complete()."""
    chunk_size = 60

    def __init__(self, workers=4):
        self.workers = workers

    def complete(self, payload):
        """Return [{"id", "text"}] for payload["translate"], or None when the reply can't be parsed."""
        raise NotImplementedError

    def translate_subtitles(self, subtitles, progress=None):
        """progress(done, total) counts cues of finished chunks; it runs on this thread, so it may raise
        to stop: chunks not yet started are dropped, the ones in flight finish first."""
        texts = [clean(cue.content) for cue in subtitles]
        chunks = [range(start, min(start + self.chunk_size, len(texts)))
                  for start in range(0, len(texts), self.chunk_size)]
        translations, done = {}, 0
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(self.translate_chunk, texts, chunk): chunk for chunk in chunks}
            try:
                for future in as_completed(futures):
                    translations.update(future.result())
                    done += len(futures[future])
                    if progress:
                        progress(done, len(texts))
            except BaseException:
                pool.shutdown(cancel_futures=True)
                raise
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
            raise SystemExit("OPENAI_API_KEY is not set, add it to .env or export OPENAI_API_KEY=sk-...")
        super().__init__(workers)
        self.client = openai.OpenAI(max_retries=5)
        self.model = model or DEFAULT_OPENAI_MODEL

    def complete(self, payload):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "subtitle_translations", "strict": True, "schema": SCHEMA}},
        )
        try:
            return json.loads(response.choices[0].message.content)["translations"]
        except (TypeError, ValueError, KeyError):
            return None


class ClaudeCodeTranslator(LLMTranslator):
    """ANTHROPIC_API_KEY is removed from the child env so the CLI always uses the claude.ai login.
    Tools, MCP servers, skills, settings and CLAUDE.md are all turned off: each call is one plain completion."""
    # every call pays a few seconds of CLI start-up, so send bigger chunks than the OpenAI engine
    chunk_size = 100

    def __init__(self, model=None, workers=3, effort="medium"):
        if not shutil.which("claude"):
            raise SystemExit("Claude Code CLI not found, install it and run `claude` once to log in")
        super().__init__(workers)
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.effort = effort
        self.env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # an empty folder as cwd so no project CLAUDE.md is picked up
        self.cwd = tempfile.mkdtemp(prefix="claude-translate-")

    def complete(self, payload):
        command = [
            "claude", "-p", "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA),
            "--system-prompt", SYSTEM_PROMPT,
            "--tools", "", "--model", self.model, "--effort", self.effort,
            "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands",
        ]
        process = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                 text=True, env=self.env, cwd=self.cwd, timeout=CLAUDE_TIMEOUT)
        try:
            reply = json.loads(process.stdout)
        except ValueError:
            raise RuntimeError(f"claude failed: {(process.stderr or process.stdout).strip()[-300:]}")
        if process.returncode != 0 or reply.get("is_error"):
            # usage limit, auth or API errors: retrying would not help
            raise RuntimeError(f"claude failed: {reply.get('result') or reply.get('subtype')}")
        output = reply.get("structured_output")
        if output is None:
            try:
                output = json.loads(reply.get("result") or "")
            except ValueError:
                return None
        translations = output.get("translations") if isinstance(output, dict) else None
        return translations if isinstance(translations, list) else None
