# Translate with an LLM: the OpenAI API, or the Claude Code CLI (`claude -p`) so usage counts against a
# Claude Pro/Max plan instead of being billed per token.
#
# Cues are grouped into sentences and sent as numbered items, one list per sentence: the LLM translates the
# whole sentence, then splits its translation back over the sentence's cues by meaning (the local NMT model
# can only split by length). Sentences go out in chunks, never cut between two chunks, with a few cues
# before and after as context; chunks run in parallel.
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field

from dich import settings
from dich.core.langs import english_name
from dich.drivers import Driver, UserError, module_missing, register
from dich.drivers.translate.common import clean, group_sentences, translated
from dich.formats.transcript import Transcript

CONTEXT_CUES = 5
NEXT_CUES = 3
MAX_GAP = 1.5          # a pause this long (seconds) also ends a sentence, for transcripts without punctuation
ATTEMPTS = 3
CLAUDE_TIMEOUT = 600
BATCH_POLL = 15        # seconds between batch status checks
BATCH_DONE = {"completed", "failed", "expired", "cancelled"}
BATCH_DEAD = {"failed", "expired", "cancelled", "cancelling"}
BATCH_DISCOUNT = 0.5

# USD per 1M tokens: input, cached input, output (standard tier, batch is half). Prices change: set
# OPENAI_PRICE in .env for a model missing here or priced differently.
OPENAI_PRICES = {
    "gpt-5": (1.25, 0.125, 10.0),
    "gpt-5-mini": (0.25, 0.025, 2.0),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-4.1": (2.0, 0.50, 8.0),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o": (2.50, 1.25, 10.0),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
}

SYSTEM_PROMPT = """You translate {source} video subtitles into {target}.
- Translate naturally and concisely, like professional {target} subtitles.
- Keep technical terms, product names, people's names and code as they are \
(e.g. model, prompt, token, API, framework, commit).
- Each element of "translate" is one sentence, given as the list of cues it is split over. \
Understand and translate the whole sentence naturally first, then split your translation over its \
cues in order, so each cue carries the {target} closest to its own part. Word order may move \
between cues of the same sentence when {target} needs it, but never between sentences.
- Return exactly one translation for every id in "translate", never merge, drop or add ids.
- "context" holds the previous cues and "next" the following cues, for reference only: \
do not translate them."""

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


def openai_price(model):
    """(input, cached input, output) USD per 1M tokens, or None when unknown."""
    if settings.OPENAI_PRICE:
        try:
            prices = tuple(float(p) for p in settings.OPENAI_PRICE.split(","))
            if len(prices) == 3:
                return prices
        except ValueError:
            pass
    # longest matching prefix, so a dated snapshot (gpt-5-mini-2025-08-07) gets its family's price
    name = max((n for n in OPENAI_PRICES if model == n or model.startswith(n + "-")), key=len, default=None)
    return OPENAI_PRICES.get(name)


def duration(seconds):
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


class Usage:
    """Tokens and cost summed over the requests of a task; requests come from several threads."""

    def __init__(self, cost_note="", billed=True):
        self.billed = billed     # False: the cost is only what it would have been (Claude plan), not paid
        self.lock = threading.Lock()
        self.requests = self.input = self.cached = self.output = 0
        self.cost = 0.0
        self.priced = True       # False once a request had no known price: the total would be wrong
        self.cost_note = cost_note

    def add(self, input=0, cached=0, output=0, cost=None, requests=1):
        with self.lock:
            self.requests += requests
            self.input += input
            self.cached += cached
            self.output += output
            if cost is None:
                self.priced = False
            else:
                self.cost += cost

    def merge(self, other):
        self.add(other.input, other.cached, other.output, other.cost if other.priced else None, other.requests)

    def __str__(self):
        text = (f"{self.requests} request, {self.input:,} token vào"
                + (f" ({self.cached:,} từ cache)" if self.cached else "") + f", {self.output:,} token ra")
        if self.priced and self.requests:
            text += f", ~${self.cost:.4f}{self.cost_note}"
        return text


class LLMTranslator:
    """Chunking, context and id checks shared by the LLM engines; subclasses implement complete()."""
    chunk_size = 60

    def __init__(self, workers=4, prompt=None):
        self.workers = workers
        self.prompt = prompt or system_prompt("en", "vi")
        self.usage = Usage()
        self.checkpoint = None   # a storage.checkpoint.Checkpoint: chunks translated by an earlier attempt

    def complete(self, payload):
        """Return [{"id", "text"}] for payload["translate"], or None when the reply can't be parsed."""
        raise NotImplementedError

    def translate_texts(self, texts, progress=None, sentences=None):
        """sentences: lists of consecutive cue indexes that form one sentence (by punctuation if not given).
        progress(done, total) counts cues of finished chunks."""
        texts = [clean(t) for t in texts]
        if sentences is None:
            sentences = group_sentences([SimpleNamespace(text=t) for t in texts])
        chunks = self.chunks(sentences)
        translations, done = {}, 0
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(self.translate_chunk, texts, chunk): chunk for chunk in chunks}
            try:
                for future in as_completed(futures):
                    translations.update(future.result())
                    done += sum(len(sentence) for sentence in futures[future])
                    if progress:
                        progress(done, len(texts))
            except BaseException:
                pool.shutdown(cancel_futures=True)
                raise
        return [translations.get(i, "") for i in range(len(texts))]

    def chunks(self, sentences):
        """Consecutive sentences packed up to chunk_size cues; a longer sentence is a chunk of its own."""
        chunks, current, size = [], [], 0
        for sentence in sentences:
            if current and size + len(sentence) > self.chunk_size:
                chunks.append(current)
                current, size = [], 0
            current.append(sentence)
            size += len(sentence)
        if current:
            chunks.append(current)
        return chunks

    def translate_chunk(self, texts, sentences):
        """Map cue index → translation for one chunk, from the checkpoint when an earlier attempt did it."""
        sentences = [kept for sentence in sentences if (kept := [i for i in sentence if texts[i]])]
        if not sentences:
            return {}
        if self.checkpoint is None:
            return self.translate_sentences(texts, sentences)
        key = hashlib.sha1(json.dumps(self.payload(texts, sentences), ensure_ascii=False).encode()).hexdigest()
        saved = self.checkpoint.get(key)
        if saved is not None:
            return {int(i): text for i, text in saved.items()}
        result = self.translate_sentences(texts, sentences)
        self.checkpoint.put(key, result)
        return result

    def translate_sentences(self, texts, sentences):
        """Retries, then halves the chunk between sentences when ids come back wrong. A single sentence
        that keeps failing falls back to its cues one by one."""
        for _ in range(ATTEMPTS):
            result = self.request(texts, sentences)
            if result is not None:
                return result
        if len(sentences) == 1:
            if len(sentences[0]) == 1:
                raise RuntimeError(f"No translation returned for cue {sentences[0][0] + 1}")
            sentences = [[i] for i in sentences[0]]
        half = len(sentences) // 2
        return {**self.translate_sentences(texts, sentences[:half]), **self.translate_sentences(texts, sentences[half:])}

    def request(self, texts, sentences):
        return self.check(self.complete(self.payload(texts, sentences)), sentences)

    @staticmethod
    def payload(texts, sentences):
        first, last = sentences[0][0], sentences[-1][-1]
        payload = {
            "context": [texts[i] for i in range(max(0, first - CONTEXT_CUES), first) if texts[i]],
            "translate": [[{"id": i, "text": texts[i]} for i in sentence] for sentence in sentences],
        }
        following = [t for t in texts[last + 1:last + 1 + NEXT_CUES] if t]
        if following:
            payload["next"] = following
        return payload

    @staticmethod
    def check(items, sentences):
        """Cue index → translation, or None when the ids don't match the cues asked for."""
        if items is None:
            return None
        try:
            result = {item["id"]: clean(item["text"]) for item in items}
        except (TypeError, KeyError):
            return None
        if sorted(result) != [i for sentence in sentences for i in sentence]:
            return None
        return result


class OpenAITranslator(LLMTranslator):
    def __init__(self, model, workers=4, prompt=None):
        import openai

        super().__init__(workers, prompt)
        self.client = openai.OpenAI(max_retries=5)
        self.model = model

    def body(self, payload):
        """Chat completion request, as sent directly or as one line of a batch."""
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": self.prompt},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "subtitle_translations", "strict": True, "schema": SCHEMA}},
        }

    def complete(self, payload):
        response = self.client.chat.completions.create(**self.body(payload))
        self.count(self.usage, response.usage and response.usage.model_dump())
        return self.items(response.choices[0].message.content)

    def count(self, usage, reply_usage, factor=1.0):
        """Add one reply's usage (a chat completion "usage" dict) to `usage`."""
        reply_usage = reply_usage or {}
        input = reply_usage.get("prompt_tokens") or 0
        cached = (reply_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        output = reply_usage.get("completion_tokens") or 0
        price = openai_price(self.model)
        cost = None
        if price:
            cost = ((input - cached) * price[0] + cached * price[1] + output * price[2]) / 1e6 * factor
        usage.add(input, cached, output, cost)

    @staticmethod
    def items(content):
        try:
            return json.loads(content)["translations"]
        except (TypeError, ValueError, KeyError):
            return None


class OpenAIBatchTranslator(OpenAITranslator):
    """All chunks go out as one Batch API job (half the price, done within 24h, usually minutes).
    Chunks whose answer is missing or has wrong ids are then translated with direct requests.
    The batch is tagged with a hash of its content: a retried task picks up the batch already running."""
    poll = BATCH_POLL

    def __init__(self, model, workers=4, prompt=None, log=None):
        super().__init__(model, workers, prompt)
        self.log = log

    def info(self, message):
        if self.log:
            self.log.info(message)

    def warn(self, message):
        if self.log:
            self.log.warn(message)

    def translate_texts(self, texts, progress=None, sentences=None):
        texts = [clean(t) for t in texts]
        if sentences is None:
            sentences = group_sentences([SimpleNamespace(text=t) for t in texts])
        chunks = [kept for chunk in self.chunks(sentences)
                  if (kept := [s for sentence in chunk if (s := [i for i in sentence if texts[i]])])]
        total = len(texts)
        replies = self.run_batch([self.body(self.payload(texts, chunk)) for chunk in chunks], total, progress)
        translations, failed = {}, []
        for chunk, reply in zip(chunks, replies):
            result = self.check(self.items(reply), chunk)
            if result is None:
                failed.append(chunk)
            else:
                translations.update(result)
        if failed:
            self.warn(f"{len(failed)}/{len(chunks)} phần không có kết quả từ batch, dịch lại trực tiếp")
            with ThreadPoolExecutor(self.workers) as pool:
                futures = [pool.submit(self.translate_chunk, texts, chunk) for chunk in failed]
                try:
                    for future in as_completed(futures):
                        translations.update(future.result())
                        if progress:
                            progress(len(translations), total)
                except BaseException:
                    pool.shutdown(cancel_futures=True)
                    raise
        if progress:
            progress(total, total)
        return [translations.get(i, "") for i in range(total)]

    def run_batch(self, bodies, total, progress):
        """The message content of each body's reply, in order (None where it failed)."""
        if not bodies:
            return []
        lines = [json.dumps({"custom_id": str(n), "method": "POST", "url": "/v1/chat/completions", "body": body},
                            ensure_ascii=False) for n, body in enumerate(bodies)]
        data = ("\n".join(lines) + "\n").encode()
        key = hashlib.sha256(data).hexdigest()
        batch = self.find_batch(key)
        if batch:
            self.info(f"Tiếp tục batch {batch.id} đã gửi trước đó")
        else:
            upload = self.client.files.create(file=("dich-translate.jsonl", data), purpose="batch")
            batch = self.client.batches.create(input_file_id=upload.id, endpoint="/v1/chat/completions",
                                               completion_window="24h", metadata={"dich_key": key})
            self.info(f"Đã gửi batch {batch.id} ({len(bodies)} request), chờ OpenAI xử lý")
        status = None
        while batch.status not in BATCH_DONE:
            if batch.status != status:
                status = batch.status
                self.info(f"Batch {batch.id}: {status}")
            counts = batch.request_counts
            if progress and counts and counts.total:
                progress(total * (counts.completed + counts.failed) // counts.total, total)
            time.sleep(self.poll)
            batch = self.client.batches.retrieve(batch.id)
        replies = [None] * len(bodies)
        if batch.created_at and batch.completed_at:
            waited = (batch.in_progress_at - batch.created_at) if batch.in_progress_at else 0
            self.info(f"Batch {batch.id} xong sau {duration(batch.completed_at - batch.created_at)}"
                      f" (chờ hàng đợi {duration(waited)})")
        if batch.status == "failed":
            errors = "; ".join(e.message or e.code or "" for e in (batch.errors.data if batch.errors else []) or [])
            self.warn(f"Batch {batch.id} lỗi: {errors or 'không rõ lý do'}")
            return replies
        if batch.status != "completed":
            self.warn(f"Batch {batch.id} kết thúc ở trạng thái {batch.status}")
        if batch.output_file_id:
            usage = Usage()
            for line in self.client.files.content(batch.output_file_id).text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                response = row.get("response") or {}
                if response.get("status_code") != 200:
                    continue
                body = response.get("body") or {}
                self.count(usage, body.get("usage"), BATCH_DISCOUNT)
                try:
                    replies[int(row["custom_id"])] = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
            self.info(f"Batch {batch.id}: {usage}")
            self.usage.merge(usage)
        return replies

    def find_batch(self, key):
        """A recent batch with the same content that is still running or finished fine."""
        for batch in self.client.batches.list(limit=100).data:
            if (batch.metadata or {}).get("dich_key") == key and batch.status not in BATCH_DEAD:
                if batch.status == "completed" and not batch.output_file_id:
                    continue
                return batch
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
        self.usage = Usage(" theo giá API (gói Pro/Max không tính theo token)", billed=False)

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
        usage = reply.get("usage") or {}
        cached = usage.get("cache_read_input_tokens") or 0
        self.usage.add(input=(usage.get("input_tokens") or 0) + cached + (usage.get("cache_creation_input_tokens") or 0),
                       cached=cached, output=usage.get("output_tokens") or 0, cost=reply.get("total_cost_usd"))
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

    def make(self, params, prompt, log):
        raise NotImplementedError

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        prompt = system_prompt(source.lang, ctx.lang)
        ctx.log.info(f"Dịch {len(source.cues)} câu sang {english_name(ctx.lang)} bằng {self.name} ({params.model})")
        sentences = group_sentences(source.cues, max_gap=MAX_GAP)
        translator = self.make(params, prompt, ctx.log)
        translator.checkpoint = ctx.checkpoint
        if ctx.checkpoint.resumed:
            ctx.log.info(f"Tiếp tục từ lần chạy trước: {ctx.checkpoint.resumed} phần đã dịch, không gửi lại")
        started = time.monotonic()
        try:
            texts = translator.translate_texts([c.text for c in source.cues], ctx.progress, sentences)
        except BaseException:
            # what failed attempts spent counts in the job's cost too
            if translator.usage.requests:
                self.log_usage(ctx, translator.usage, f"Đã dùng trước khi lỗi ({duration(time.monotonic() - started)})")
            raise
        empty = sum(1 for t, c in zip(texts, source.cues) if c.text.strip() and not t)
        if empty:
            ctx.log.warn(f"{empty} câu không có bản dịch")
        self.log_usage(ctx, translator.usage, f"Dịch xong sau {duration(time.monotonic() - started)}")
        return {"transcript": translated(source, texts, ctx.lang).save(ctx.output("transcript.jsonl"))}

    @staticmethod
    def log_usage(ctx, usage, prefix):
        """The job's cost is the sum of cost_usd over these events (see Store.job_costs)."""
        ctx.log.info(f"{prefix}: {usage}", requests=usage.requests, input_tokens=usage.input,
                     cached_tokens=usage.cached, output_tokens=usage.output,
                     cost_usd=round(usage.cost, 6) if usage.priced else None, billed=usage.billed)


class OpenAIParams(BaseModel):
    model: str = Field(settings.OPENAI_MODEL, title="Model", description="Model OpenAI (OPENAI_MODEL)")
    workers: int = Field(4, title="Request song song", ge=1, le=16, description="Số request song song")


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

    def make(self, params, prompt, log):
        return OpenAITranslator(params.model, params.workers, prompt)


@register
class OpenAIBatchTranslate(OpenAITranslate):
    name = "openai-batch"
    title = "OpenAI Batch (rẻ 50%, chậm hơn)"

    def make(self, params, prompt, log):
        return OpenAIBatchTranslator(params.model, params.workers, prompt, log)


class ClaudeParams(BaseModel):
    model: Literal["sonnet", "haiku", "opus"] = Field(
        settings.CLAUDE_MODEL if settings.CLAUDE_MODEL in ("sonnet", "haiku", "opus") else "sonnet", title="Model")
    workers: int = Field(3, title="Lệnh claude song song", ge=1, le=3, description="Số lệnh claude song song")
    effort: Literal["low", "medium", "high"] = Field("medium", title="Mức suy nghĩ")


@register
class ClaudeTranslate(LLMDriver):
    name = "claude"
    title = "Claude Code (gói Pro/Max)"
    Params = ClaudeParams

    @classmethod
    def available(cls):
        return None if shutil.which("claude") else "Chưa cài Claude Code CLI (lệnh claude)"

    def make(self, params, prompt, log):
        return ClaudeCodeTranslator(params.model, params.workers, params.effort, prompt)
