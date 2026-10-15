# Translate with an LLM: the OpenAI API, or the Claude Code CLI (`claude -p`) so usage counts against a
# Claude Pro/Max plan instead of being billed per token.
#
# Unlike the local NMT model, an LLM can follow a sentence split over several cues, so cues are sent as
# numbered items and translated one-to-one. Cues go out in chunks with a few previous cues as context;
# chunks run in parallel.
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel, Field

from dich import settings
from dich.core.langs import english_name
from dich.drivers import Driver, UserError, module_missing, register
from dich.drivers.translate.common import clean, translated
from dich.formats.transcript import Transcript

CONTEXT_CUES = 5
ATTEMPTS = 3
CLAUDE_TIMEOUT = 600

SYSTEM_PROMPT = """You translate {source} video subtitles into {target}.
- Translate naturally and concisely, like professional {target} subtitles.
- Keep technical terms, product names, people's names and code as they are \
(e.g. model, prompt, token, API, framework, commit).
- A sentence may be split over several cues: translate so each cue carries the \
{target} for its own part, in the same order.
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


def system_prompt(source_lang, target_lang):
    return SYSTEM_PROMPT.format(source=english_name(source_lang), target=english_name(target_lang))


class LLMTranslator:
    """Chunking, context and id checks shared by the LLM engines; subclasses implement complete()."""
    chunk_size = 60

    def __init__(self, workers=4, prompt=None):
        self.workers = workers
        self.prompt = prompt or system_prompt("en", "vi")

    def complete(self, payload):
        """Return [{"id", "text"}] for payload["translate"], or None when the reply can't be parsed."""
        raise NotImplementedError

    def translate_texts(self, texts, progress=None):
        """progress(done, total) counts cues of finished chunks."""
        texts = [clean(t) for t in texts]
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
        return [translations.get(i, "") for i in range(len(texts))]

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
    def __init__(self, model, workers=4, prompt=None):
        import openai

        super().__init__(workers, prompt)
        self.client = openai.OpenAI(max_retries=5)
        self.model = model

    def complete(self, payload):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.prompt},
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

    def __init__(self, model, workers=3, effort="medium", prompt=None):
        super().__init__(workers, prompt)
        self.model = model
        self.effort = effort
        self.env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # an empty folder as cwd so no project CLAUDE.md is picked up
        self.cwd = tempfile.mkdtemp(prefix="claude-translate-")

    def complete(self, payload):
        command = [
            "claude", "-p", "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA),
            "--system-prompt", self.prompt,
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
            raise UserError(f"Claude Code báo lỗi: {reply.get('result') or reply.get('subtype')}")
        output = reply.get("structured_output")
        if output is None:
            try:
                output = json.loads(reply.get("result") or "")
            except ValueError:
                return None
        translations = output.get("translations") if isinstance(output, dict) else None
        return translations if isinstance(translations, list) else None


class LLMDriver(Driver):
    stage = "translate"
    resources = {"net"}
    consumes = {"transcript": "transcript.src"}
    produces = {"transcript": "transcript.{lang}"}

    def make(self, params, prompt):
        raise NotImplementedError

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        prompt = system_prompt(source.lang, ctx.lang)
        ctx.log.info(f"Dịch {len(source.cues)} câu sang {english_name(ctx.lang)} bằng {self.name} ({params.model})")
        texts = self.make(params, prompt).translate_texts([c.text for c in source.cues], ctx.progress)
        empty = sum(1 for t, c in zip(texts, source.cues) if c.text.strip() and not t)
        if empty:
            ctx.log.warn(f"{empty} câu không có bản dịch")
        return {"transcript": translated(source, texts, ctx.lang).save(ctx.output("transcript.json"))}


class OpenAIParams(BaseModel):
    model: str = Field(settings.OPENAI_MODEL, description="Model OpenAI (OPENAI_MODEL)")
    workers: int = Field(4, ge=1, le=16, description="Số request song song")


@register
class OpenAITranslate(LLMDriver):
    name = "openai"
    title = "OpenAI API"
    Params = OpenAIParams

    @classmethod
    def available(cls):
        if missing := module_missing("openai"):
            return missing + ": uv sync --extra openai"
        if not os.environ.get("OPENAI_API_KEY"):
            return "Chưa đặt OPENAI_API_KEY trong .env"
        return None

    def make(self, params, prompt):
        return OpenAITranslator(params.model, params.workers, prompt)


class ClaudeParams(BaseModel):
    model: Literal["sonnet", "haiku", "opus"] = Field(
        settings.CLAUDE_MODEL if settings.CLAUDE_MODEL in ("sonnet", "haiku", "opus") else "sonnet")
    workers: int = Field(3, ge=1, le=3, description="Số lệnh claude song song")
    effort: Literal["low", "medium", "high"] = "medium"


@register
class ClaudeTranslate(LLMDriver):
    name = "claude"
    title = "Claude Code (gói Pro/Max)"
    Params = ClaudeParams

    @classmethod
    def available(cls):
        return None if shutil.which("claude") else "Chưa cài Claude Code CLI (lệnh claude)"

    def make(self, params, prompt):
        return ClaudeCodeTranslator(params.model, params.workers, params.effort, prompt)
