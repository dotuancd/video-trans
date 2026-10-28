# Translate with an LLM: the OpenAI API, or the Claude Code CLI (`claude -p`) so usage counts against a
# Claude Pro/Max plan instead of being billed per token.
#
# Cues are grouped into sentences and sent as numbered items, one list per sentence: the LLM translates the
# whole sentence, then splits its translation back over the sentence's cues by meaning (the local NMT model
# can only split by length). Sentences go out in chunks, never cut between two chunks, with a few cues
# before and after as context; chunks run in parallel.
#
# For a dub (target with a known reading speed, see settings.SPEECH_RATE) each sentence also carries how long
# the speaker takes and how many syllables fit in that time: a translation much shorter than the original left
# dead air (the dub only slows down to min_speed), a longer one was sped up or pushed the next sentence late.
# The LLM counts syllables poorly, so the budget in the prompt alone barely helped; a second pass sends back
# the sentences still off by more than FIT_RANGE with their actual count. On 105 sentences: dead air
# (< 0.75 of the budget) 35% → 13%, late (> 1.2) 15% → 3%, for about twice the translation cost.
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field

from dich import settings
from dich.core.langs import english_name
from dich.drivers import Driver, UserError, module_missing, register
from dich.drivers.translate.common import clean, group_sentences, translated
from dich.formats.transcript import Transcript

CONTEXT_CUES = 5
DUB_GAP = 1.0          # the dub's MAX_GAP: a pause this long is not filled, so it is not counted in a sentence's time
SPEECH_RATES = {"vi": settings.SPEECH_RATE}   # syllables per second the dub voice reads, by target language
FIT_RANGE = (0.8, 1.15)   # syllables / budget a sentence may be off by before the length pass rewrites it
FIT_MIN_SECONDS = 1.0     # shorter sentences are left alone: a syllable or two is already a big ratio
NEXT_CUES = 3
MAX_GAP = 1.5          # a pause this long (seconds) also ends a sentence, for transcripts without punctuation
ATTEMPTS = 3
CLAUDE_TIMEOUT = 600
BATCH_POLL = 15        # seconds between batch status checks
BATCH_DONE = {"completed", "failed", "expired", "cancelled"}
BATCH_DEAD = {"failed", "expired", "cancelled", "cancelling"}
BATCH_DISCOUNT = 0.5
REQUESTS = "requests.jsonl"   # in the attempt folder: every request sent and its reply, shown in the web UI

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
{timing}{rules}- Keep technical terms, product names, people's names and code as they are \
(e.g. model, prompt, token, API, framework, commit).
- Each element of "translate" is one sentence, given as the list of "cues" it is split over. \
Understand and translate the whole sentence naturally first, then split your translation over its \
cues in order, so each cue carries the {target} closest to its own part. Word order may move \
between cues of the same sentence when {target} needs it, but never between sentences.
- Return exactly one translation for every id in "translate", never merge, drop or add ids.
- "context" holds the previous cues and "next" the following cues, for reference only: \
do not translate them."""

TIMING_PROMPT = """- The translation is read aloud as a dub over the original speech. "seconds" is how long the \
speaker takes for the sentence and "syllables" how many {target} syllables are read in that time. \
Make each sentence's translation about that many syllables: a shorter one leaves silence in the dub, \
up to 15% more is fine (it is read a little faster). Below the count, say it more fully and \
naturally (complete phrasing, connectives, subjects) rather than tersely; above it, condense. \
Never add or drop meaning.
"""

# Rules for one source → target pair, on top of the general ones (after YouDub's English → Vietnamese rules).
PAIR_RULES = {
    ("en", "vi"): """- Drop meaningless fillers (um, uh, er, you know, I mean; like, kind of, sort of only when \
used as hesitation) instead of translating them, never words that carry meaning (an apology, a thanks, \
a yes or no). Never leave a cue empty: a cue of nothing but fillers gets a short natural interjection \
("À", "Ừ", "Rồi").
- Keep the forms of address the same for the whole video: the host speaking to viewers is "mình" and \
the viewers "các bạn"; between people on screen pick fitting pronouns (anh/chị/em/tôi/bạn...) once and \
stick to them.
- Do not translate "so" opening a sentence as "vì vậy"/"do đó" unless it really means a consequence: \
as a filler drop it, as a transition use "rồi", "thế là", "vậy thì".
- Numbers in Vietnamese style: decimal comma (3,5), no thousands separator (12000, 1500000).
- Usual YouTube phrases in their usual Vietnamese: subscribe → "đăng ký kênh", like → "bấm thích", \
"link in the description" → "link ở phần mô tả", comment → "bình luận".
- Swearing and slang keep their strength: do not soften or censor them, nor make them stronger.
- Terms Vietnamese speakers commonly use in English stay in English (e.g. app, email, livestream, \
startup, marketing, deadline), as do brand and product names.
""",
}

FIT_PROMPT = """You adjust the length of dub lines, translated from video subtitles. Each element of \
"translate" is one sentence: its source cues ("source"), the current translation of each cue ("text"), \
how many syllables the sentence has now ("syllables") and how many fit its time ("target"). Rewrite \
the translation so the sentence has about the target number of syllables (within 10%), keeping its \
meaning and its split over the cues. Longer: fuller, natural phrasing (subjects, connectives, complete \
expressions), never new information. Shorter: condense. Keep technical terms, names and code as they \
are. Return exactly one text for every cue id."""

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


BRIEF_PROMPT = """

Video brief (background on the whole video, written before translating; keep terms and tone consistent \
with it, the rules above take precedence):
{brief}"""


def system_prompt(source_lang, target_lang, timed=False, brief=None):
    """timed: sentences come with "seconds"/"syllables" (see LLMTranslator.rate). brief: what the video is
    about (see drivers/brief), so chunks translated apart use the same terms. Some language pairs add their
    own rules (PAIR_RULES)."""
    target = english_name(target_lang)
    timing = TIMING_PROMPT.format(target=target) if timed else ""
    rules = PAIR_RULES.get((source_lang, target_lang), "")
    prompt = SYSTEM_PROMPT.format(source=english_name(source_lang), target=target, timing=timing, rules=rules)
    return with_brief(prompt, brief)


def fit_system_prompt(source_lang, target_lang, brief=None):
    """The length pass's prompt: it rewrites the translation, so it keeps the pair's rules too."""
    rules = PAIR_RULES.get((source_lang, target_lang))
    return with_brief(FIT_PROMPT + ("\n" + rules.rstrip() if rules else ""), brief)



def with_brief(prompt, brief):
    return prompt + BRIEF_PROMPT.format(brief=brief.strip()) if brief and brief.strip() else prompt


def syllables(text):
    """Vietnamese writes one syllable per word."""
    return len(text.split())


def speech_rate(lang):
    """Syllables per second the dub reads lang at, or None: no time budget for that language."""
    return SPEECH_RATES.get(lang)


def spoken_seconds(spans, max_gap=DUB_GAP):
    """Seconds a sentence's cues (start, end) take, without the pauses longer than max_gap between them."""
    total = spans[-1][1] - spans[0][0]
    total -= sum(b[0] - a[1] for a, b in zip(spans, spans[1:]) if b[0] - a[1] > max_gap)
    return max(0.0, total)


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


def asked_ids(payload):
    """The cue ids a payload asks a translation for."""
    return sorted(cue["id"] for sentence in payload.get("translate", []) for cue in sentence["cues"])


class RequestLog:
    """<attempt dir>/requests.jsonl: one line per request sent (retries and errors too), to see what went to the LLM
    and what came back. Requests come from several threads."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def write(self, row):
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)


def read_requests(attempt_dir):
    """The rows of an attempt's requests.jsonl, [] when it has none."""
    try:
        lines = (Path(attempt_dir) / REQUESTS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue   # the last line of a run killed mid-write
    return rows


class LLMTranslator:
    """Chunking, context and id checks shared by the LLM engines; subclasses implement complete()."""
    chunk_size = 60
    engine = "llm"
    model = None
    fit_prompt = FIT_PROMPT   # the length pass's system prompt (with the brief when there is one)

    def __init__(self, workers=4, prompt=None, rate=None):
        self.workers = workers
        self.prompt = prompt or system_prompt("en", "vi", timed=rate is not None)
        self.rate = rate         # syllables per second: with timings, each sentence gets a syllable budget
        self.timing = None       # (start, end) seconds per cue, set by translate_texts
        self.fitted = None       # (sentences off their budget, rewritten) after the length pass
        self.usage = Usage()
        self.checkpoint = None   # a storage.checkpoint.Checkpoint: chunks translated by an earlier attempt
        self.request_log = None  # a RequestLog: every request sent is written there

    def record(self, payload, prompt, started, reply=None, items=None, usage=None, error=None, batch_id=None,
               kind=None):
        """One line in the request log (when there is one) for a request sent at `started` (time.time())."""
        if self.request_log is None:
            return
        asked = asked_ids(payload)
        try:
            ok = error is None and sorted(item["id"] for item in items) == asked
        except (TypeError, KeyError):
            ok = False
        self.request_log.write({
            "ts": started, "engine": self.engine, "model": self.model,
            "kind": kind or ("fit" if prompt == self.fit_prompt else "translate"), "batch_id": batch_id,
            "system": prompt or self.prompt, "payload": payload, "reply": reply, "ok": ok, "cues": len(asked),
            "usage": usage, "seconds": round(time.time() - started, 2) if batch_id is None else None, "error": error,
        })

    def complete(self, payload, prompt=None):
        """Return [{"id", "text"}] for payload["translate"] (system prompt: self.prompt unless given), or None
        when the reply can't be parsed."""
        raise NotImplementedError

    def ask(self, system, text, kind="brief", schema=None):
        """The reply to `text`: free text (the brief), or JSON text following `schema` (the mindmap)."""
        raise NotImplementedError

    def translate_texts(self, texts, progress=None, sentences=None, timing=None):
        """sentences: lists of consecutive cue indexes that form one sentence (by punctuation if not given).
        timing: (start, end) seconds per cue, for the syllable budgets. progress(done, total) counts cues
        of finished chunks."""
        texts = [clean(t) for t in texts]
        self.timing = timing
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
        self.fit(texts, sentences, translations, progress)
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
        return self.cached(self.payload(texts, sentences), lambda: self.translate_sentences(texts, sentences))

    def cached(self, payload, compute):
        """compute() → {cue index: text}, from the checkpoint when an earlier attempt sent the same payload."""
        if self.checkpoint is None:
            return compute()
        key = hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        saved = self.checkpoint.get(key)
        if saved is not None:
            return {int(i): text for i, text in saved.items()}
        result = compute()
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

    def payload(self, texts, sentences):
        first, last = sentences[0][0], sentences[-1][-1]
        payload = {
            "context": [texts[i] for i in range(max(0, first - CONTEXT_CUES), first) if texts[i]],
            "translate": [self.sentence(texts, sentence) for sentence in sentences],
        }
        following = [t for t in texts[last + 1:last + 1 + NEXT_CUES] if t]
        if following:
            payload["next"] = following
        return payload

    def sentence(self, texts, sentence):
        item = {}
        if self.rate and self.timing:
            seconds = spoken_seconds([self.timing[i] for i in sentence])
            item = {"seconds": round(seconds, 1), "syllables": max(1, round(seconds * self.rate))}
        item["cues"] = [{"id": i, "text": texts[i]} for i in sentence]
        return item

    def budget(self, sentence):
        """Syllables that fit the sentence's time, or None when it has no budget."""
        if not (self.rate and self.timing):
            return None
        seconds = spoken_seconds([self.timing[i] for i in sentence])
        return seconds * self.rate if seconds >= FIT_MIN_SECONDS else None

    def off_budget(self, sentence, translations):
        """How far the translation is off its budget (0 = right on it), None without a budget."""
        budget = self.budget(sentence)
        if budget is None:
            return None
        ratio = sum(syllables(translations.get(i, "")) for i in sentence) / budget
        return 0.0 if FIT_RANGE[0] <= ratio <= FIT_RANGE[1] else abs(ratio - 1)

    def fit(self, texts, sentences, translations, progress=None):
        """The length pass: sentences off their syllable budget go back with their actual count, in chunks;
        a rewrite replaces the translation only when it is closer to the budget. Best effort: a reply with
        wrong ids keeps the first translation."""
        sentences = [kept for sentence in sentences if (kept := [i for i in sentence if texts[i]])]
        off = [s for s in sentences if self.off_budget(s, translations)]
        if not off:
            return
        fitted = 0
        chunks = self.chunks(off)
        payloads = [self.fit_payload(texts, chunk, translations) for chunk in chunks]
        for chunk, result in zip(chunks, self.complete_all(payloads, chunks, self.fit_prompt, progress)):
            for sentence in chunk:
                if result and self.off_budget(sentence, result) < self.off_budget(sentence, translations):
                    translations.update({i: result[i] for i in sentence})
                    fitted += 1
        self.fitted = (len(off), fitted)

    def fit_payload(self, texts, sentences, translations):
        return {"translate": [{
            "syllables": sum(syllables(translations[i]) for i in sentence),
            "target": round(self.budget(sentence)),
            "cues": [{"id": i, "source": texts[i], "text": translations[i]} for i in sentence],
        } for sentence in sentences]}

    def complete_all(self, payloads, chunks, prompt, progress=None):
        """{cue index: text} (or None) for each payload, the replies checked against its chunk's cues."""
        def one(payload, chunk):
            return self.cached({"prompt": prompt, **payload},
                               lambda: self.check(self.complete(payload, prompt), chunk) or {})

        results = [None] * len(payloads)
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(one, p, c): n for n, (p, c) in enumerate(zip(payloads, chunks))}
            try:
                for future in as_completed(futures):
                    results[futures[future]] = future.result() or None
                    if progress:   # translation is already counted done: this only lets a stop through
                        progress(len(self.timing), len(self.timing))
            except BaseException:
                pool.shutdown(cancel_futures=True)
                raise
        return results

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
    engine = "openai"

    def __init__(self, model, workers=4, prompt=None, rate=None):
        import openai

        super().__init__(workers, prompt, rate)
        self.client = openai.OpenAI(max_retries=5)
        self.model = model

    def body(self, payload, prompt=None):
        """Chat completion request, as sent directly or as one line of a batch."""
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": prompt or self.prompt},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "subtitle_translations", "strict": True, "schema": SCHEMA}},
        }

    def complete(self, payload, prompt=None):
        started = time.time()
        try:
            response = self.client.chat.completions.create(**self.body(payload, prompt))
        except Exception as error:
            self.record(payload, prompt, started, error=f"{type(error).__name__}: {error}")
            raise
        used = self.count(self.usage, response.usage and response.usage.model_dump())
        content = response.choices[0].message.content
        items = self.items(content)
        self.record(payload, prompt, started, content, items, used)
        return items

    def ask(self, system, text, kind="brief", schema=None):
        started = time.time()
        payload = {"input": text}
        format = {"response_format": {"type": "json_schema",
                                      "json_schema": {"name": kind, "strict": True, "schema": schema}}} if schema else {}
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                **format)
        except Exception as error:
            self.record(payload, system, started, error=f"{type(error).__name__}: {error}", kind=kind)
            raise
        used = self.count(self.usage, response.usage and response.usage.model_dump())
        content = response.choices[0].message.content or ""
        self.record(payload, system, started, content, [], used, kind=kind)
        return content

    def count(self, usage, reply_usage, factor=1.0):
        """Add one reply's usage (a chat completion "usage" dict) to `usage`; return it for the request log."""
        reply_usage = reply_usage or {}
        input = reply_usage.get("prompt_tokens") or 0
        cached = (reply_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        output = reply_usage.get("completion_tokens") or 0
        price = openai_price(self.model)
        cost = None
        if price:
            cost = ((input - cached) * price[0] + cached * price[1] + output * price[2]) / 1e6 * factor
        usage.add(input, cached, output, cost)
        return {"input": input, "cached": cached, "output": output, "cost_usd": cost}

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
    engine = "openai-batch"

    def __init__(self, model, workers=4, prompt=None, log=None, rate=None):
        super().__init__(model, workers, prompt, rate)
        self.log = log

    def info(self, message):
        if self.log:
            self.log.info(message)

    def warn(self, message):
        if self.log:
            self.log.warn(message)

    def translate_texts(self, texts, progress=None, sentences=None, timing=None):
        texts = [clean(t) for t in texts]
        self.timing = timing
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
        self.fit(texts, sentences, translations)
        return [translations.get(i, "") for i in range(total)]

    def complete_all(self, payloads, chunks, prompt, progress=None):
        """The length pass goes out as a batch too (half price)."""
        replies = self.run_batch([self.body(p, prompt) for p in payloads], 0, None)
        return [self.check(self.items(reply), chunk) for chunk, reply in zip(chunks, replies)]

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
            used = [None] * len(bodies)
            errors = [None] * len(bodies)
            for line in self.client.files.content(batch.output_file_id).text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                try:
                    n = int(row["custom_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                response = row.get("response") or {}
                if response.get("status_code") != 200:
                    errors[n] = json.dumps(row.get("error") or response, ensure_ascii=False)[:2000]
                    continue
                body = response.get("body") or {}
                used[n] = self.count(usage, body.get("usage"), BATCH_DISCOUNT)
                try:
                    replies[n] = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    pass
            self.info(f"Batch {batch.id}: {usage}")
            self.usage.merge(usage)
            for n, body in enumerate(bodies):
                messages = body["messages"]
                self.record(json.loads(messages[1]["content"]), messages[0]["content"], batch.created_at or time.time(),
                            replies[n], self.items(replies[n]), used[n],
                            errors[n] or (None if replies[n] is not None else "không có kết quả"), batch.id)
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
    engine = "claude"

    def __init__(self, model, workers=3, effort="medium", prompt=None, rate=None):
        super().__init__(workers, prompt, rate)
        self.model = model
        self.effort = effort
        self.env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # an empty folder as cwd so no project CLAUDE.md is picked up
        self.cwd = tempfile.mkdtemp(prefix="claude-translate-")
        self.usage = Usage(" theo giá API (gói Pro/Max không tính theo token)", billed=False)

    def run_cli(self, system, text, payload, schema=None, kind=None):
        """(reply, usage for the request log, start time) of one `claude -p` call; raises on errors."""
        command = [
            "claude", "-p", "--output-format", "json",
            *(["--json-schema", json.dumps(schema)] if schema else []),
            "--system-prompt", system,
            "--tools", "", "--model", self.model, "--effort", self.effort,
            "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands",
        ]
        started = time.time()
        prompt = None if system == self.prompt else system
        try:
            process = subprocess.run(command, input=text, capture_output=True,
                                     text=True, env=self.env, cwd=self.cwd, timeout=CLAUDE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.record(payload, prompt, started, error=f"timeout sau {CLAUDE_TIMEOUT}s", kind=kind)
            raise
        try:
            reply = json.loads(process.stdout)
        except ValueError:
            error = (process.stderr or process.stdout).strip()[-300:]
            self.record(payload, prompt, started, process.stdout, error=f"claude failed: {error}", kind=kind)
            raise RuntimeError(f"claude failed: {error}")
        if process.returncode != 0 or reply.get("is_error"):
            # usage limit, auth or API errors: retrying would not help
            error = f"Claude Code báo lỗi: {reply.get('result') or reply.get('subtype')}"
            self.record(payload, prompt, started, process.stdout, error=error, kind=kind)
            raise UserError(error)
        usage = reply.get("usage") or {}
        cached = usage.get("cache_read_input_tokens") or 0
        used = {"input": (usage.get("input_tokens") or 0) + cached + (usage.get("cache_creation_input_tokens") or 0),
                "cached": cached, "output": usage.get("output_tokens") or 0, "cost_usd": reply.get("total_cost_usd")}
        self.usage.add(input=used["input"], cached=cached, output=used["output"], cost=used["cost_usd"])
        return reply, used, started

    def ask(self, system, text, kind="brief", schema=None):
        payload = {"input": text}
        reply, used, started = self.run_cli(system, text, payload, schema, kind=kind)
        output = reply.get("structured_output") if schema else None
        content = json.dumps(output, ensure_ascii=False) if output is not None else reply.get("result") or ""
        self.record(payload, system, started, content, [], used, kind=kind)
        return content

    def complete(self, payload, prompt=None):
        reply, used, started = self.run_cli(prompt or self.prompt, json.dumps(payload, ensure_ascii=False), payload,
                                            SCHEMA)
        output = reply.get("structured_output")
        text = json.dumps(output, ensure_ascii=False) if output is not None else reply.get("result")
        if output is None:
            try:
                output = json.loads(reply.get("result") or "")
            except ValueError:
                output = None
        translations = output.get("translations") if isinstance(output, dict) else None
        translations = translations if isinstance(translations, list) else None
        self.record(payload, prompt, started, text, translations, used)
        return translations


class LLMDriver(Driver):
    stage = "translate"
    resources = {"net"}
    consumes = {"transcript": "transcript.src", "brief": "brief?"}
    produces = {"transcript": "transcript.{lang}"}
    version = "4"   # English → Vietnamese rules (PAIR_RULES)

    def make(self, params, prompt, log, rate):
        raise NotImplementedError

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        rate = speech_rate(ctx.lang) if params.fit_dub else None
        brief = read_brief(inputs.get("brief"))
        prompt = system_prompt(source.lang, ctx.lang, timed=rate is not None, brief=brief)
        ctx.log.info(f"Dịch {len(source.cues)} câu sang {english_name(ctx.lang)} bằng {self.name} ({params.model})"
                     + (" theo bối cảnh video" if brief else ""))
        sentences = group_sentences(source.cues, max_gap=MAX_GAP)
        translator = self.make(params, prompt, ctx.log, rate)
        translator.fit_prompt = fit_system_prompt(source.lang, ctx.lang, brief)
        translator.checkpoint = ctx.checkpoint
        translator.request_log = RequestLog(ctx.dir / REQUESTS)
        if ctx.checkpoint.resumed:
            ctx.log.info(f"Tiếp tục từ lần chạy trước: {ctx.checkpoint.resumed} phần đã dịch, không gửi lại")
        started = time.monotonic()
        try:
            texts = translator.translate_texts([c.text for c in source.cues], ctx.progress, sentences,
                                               [(c.start, c.end) for c in source.cues])
        except BaseException:
            # what failed attempts spent counts in the job's cost too
            if translator.usage.requests:
                self.log_usage(ctx, translator.usage, f"Đã dùng trước khi lỗi ({duration(time.monotonic() - started)})")
            raise
        empty = sum(1 for t, c in zip(texts, source.cues) if c.text.strip() and not t)
        if empty:
            ctx.log.warn(f"{empty} câu không có bản dịch")
        if translator.fitted:
            ctx.log.info(f"Chỉnh độ dài cho lồng tiếng: sửa {translator.fitted[1]}/{translator.fitted[0]} câu lệch khung thời gian")
        self.log_usage(ctx, translator.usage, f"Dịch xong sau {duration(time.monotonic() - started)}")
        return {"transcript": translated(source, texts, ctx.lang).save(ctx.output("transcript.jsonl"))}

    @staticmethod
    def log_usage(ctx, usage, prefix):
        """The job's cost is the sum of cost_usd over these events (see Store.job_costs)."""
        ctx.log.info(f"{prefix}: {usage}", requests=usage.requests, input_tokens=usage.input,
                     cached_tokens=usage.cached, output_tokens=usage.output,
                     cost_usd=round(usage.cost, 6) if usage.priced else None, billed=usage.billed)


def read_brief(artifact):
    """The brief's text, None without one."""
    if not artifact:
        return None
    try:
        return artifact.path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class OpenAIParams(BaseModel):
    model: str = Field(settings.OPENAI_MODEL, title="Model", description="Model OpenAI (OPENAI_MODEL)")
    workers: int = Field(4, title="Request song song", ge=1, le=16, description="Số request song song")
    fit_dub: bool = Field(False, title="Chỉnh độ dài cho lồng tiếng",
                          description="Dịch vừa thời lượng câu gốc, thêm một lượt sửa câu lệch (~gấp đôi chi phí dịch, "
                                      "câu dịch dễ kém tự nhiên). Chỉ bật khi lồng tiếng bị hở/trễ nhiều")


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

    def make(self, params, prompt, log, rate):
        return OpenAITranslator(params.model, params.workers, prompt, rate)


@register
class OpenAIBatchTranslate(OpenAITranslate):
    name = "openai-batch"
    title = "OpenAI Batch (rẻ 50%, chậm hơn)"

    def make(self, params, prompt, log, rate):
        return OpenAIBatchTranslator(params.model, params.workers, prompt, log, rate)


class ClaudeParams(BaseModel):
    model: Literal["sonnet", "haiku", "opus"] = Field(
        settings.CLAUDE_MODEL if settings.CLAUDE_MODEL in ("sonnet", "haiku", "opus") else "sonnet", title="Model")
    workers: int = Field(3, title="Lệnh claude song song", ge=1, le=3, description="Số lệnh claude song song")
    effort: Literal["low", "medium", "high"] = Field("medium", title="Mức suy nghĩ")
    fit_dub: bool = Field(False, title="Chỉnh độ dài cho lồng tiếng",
                          description="Dịch vừa thời lượng câu gốc, thêm một lượt sửa câu lệch (~gấp đôi chi phí dịch, "
                                      "câu dịch dễ kém tự nhiên). Chỉ bật khi lồng tiếng bị hở/trễ nhiều")


@register
class ClaudeTranslate(LLMDriver):
    name = "claude"
    title = "Claude Code (gói Pro/Max)"
    Params = ClaudeParams

    @classmethod
    def available(cls):
        return None if shutil.which("claude") else "Chưa cài Claude Code CLI (lệnh claude)"

    def make(self, params, prompt, log, rate):
        return ClaudeCodeTranslator(params.model, params.workers, params.effort, prompt, rate)
