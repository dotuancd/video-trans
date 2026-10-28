# The video brief: what the whole video is about (field, summary, tone, key terms), written once before
# translating and put into the system prompt of every chunk. Chunks are translated apart and only see a few
# cues around them, so without it the LLM guesses the field from a few lines and renders terms differently
# from one chunk to the next.
#
# Where it comes from, cheapest first:
#   summary   the user's text (YouTube's AI summary pasted, their own notes): no request
#   meta      what the page says (title, chapters, tags; from the url source): no request, always added
#   generate  one LLM request on the transcript and the page's metadata, when the user gave no summary
# `instructions` (the field, how to address the audience…) are added as they are: they suit a project's
# defaults, the summary suits one video.
import json
from typing import Literal

from pydantic import BaseModel, Field

from dich.core.langs import english_name
from dich.drivers import Driver, register
from dich.drivers.engines import make_translator, pick_engine
from dich.drivers.translate.llm import REQUESTS, LLMDriver, RequestLog
from dich.formats.transcript import Transcript

MAX_WORDS = 8000     # of the transcript sent to write the brief (~11k tokens); longer ones are sampled
SAMPLES = 8          # evenly spread windows a long transcript is sampled in

BRIEF_PROMPT = """You write a brief for translators who subtitle a {source} video into {targets}. They \
translate it in small chunks and never see the whole video: the brief must give them what they need to \
understand it and translate it consistently. From the page's metadata and the transcript, write in {lang}, \
as plain text with these four headings (translated into {lang}):
Domain: the field and sub-field, one line.
Summary: what the video covers, 2-4 sentences.
Tone and audience: register, who speaks, how they address the audience.
Key terms: up to 25 recurring technical terms, names and acronyms, one per line as \
"term: what it means here; keep as is / suggested translation". Add the right spelling of terms the \
speech recognition visibly misheard.
No introduction, no markdown, under 400 words."""


class Params(BaseModel):
    summary: str = Field("", title="Tóm tắt / bối cảnh video",
                         description="Dán tóm tắt của YouTube hoặc tự viết vài câu. Có nội dung thì không gọi AI "
                                     "để tự tóm tắt (miễn phí)",
                         json_schema_extra={"format": "textarea"})
    instructions: str = Field("", title="Hướng dẫn dịch",
                              description="Lĩnh vực, cách xưng hô, giọng văn, thuật ngữ cần giữ… Hợp để đặt sẵn "
                                          "trong project",
                              json_schema_extra={"format": "textarea"})
    generate: bool = Field(True, title="Tự tóm tắt bằng AI",
                           description="Khi không có tóm tắt: 1 request đọc phụ đề và mô tả video để viết bối cảnh")
    engine: Literal["auto", "openai", "claude"] = Field(
        "auto", title="AI tóm tắt", description="auto: theo bước Dịch")


@register
class LLMBrief(Driver):
    stage = "brief"
    name = "llm"
    title = "Bối cảnh video (AI)"
    Params = Params
    resources = {"net"}
    consumes = {"transcript": "transcript.src", "meta": "media.meta?"}
    produces = {"brief": "brief"}

    def run(self, ctx, inputs, params):
        meta = read_meta(inputs.get("meta"))
        job = ctx.job_info()
        meta.setdefault("title", job["title"])
        parts = []
        if params.summary.strip():
            parts.append(params.summary.strip())
            ctx.log.info("Bối cảnh: dùng tóm tắt bạn nhập")
        elif params.generate and (engine := pick_engine(params.engine)):
            source = Transcript.load(inputs["transcript"].path)
            parts.append(self.generate(ctx, engine, source, meta, job["langs"], params.instructions))
        else:
            ctx.log.info("Bối cảnh: chỉ có thông tin của video" if params.generate is False
                         else "Bối cảnh: không có OpenAI hay Claude Code để tự tóm tắt, chỉ dùng thông tin của video")
        if params.instructions.strip():
            parts.append(f"Hướng dẫn của người dùng:\n{params.instructions.strip()}")
        if about := describe(meta):
            parts.append(about)
        path = ctx.output("brief.md")
        path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        return {"brief": path}

    def generate(self, ctx, engine, source, meta, langs, instructions):
        translator = make_translator(engine)
        translator.request_log = RequestLog(ctx.dir / REQUESTS)
        lang = english_name(langs[0]) if langs else "English"
        system = BRIEF_PROMPT.format(source=english_name(source.lang), lang=lang,
                                     targets=", ".join(english_name(l) for l in langs) or lang)
        text = json.dumps({k: v for k, v in meta.items() if v}, ensure_ascii=False, indent=1)
        if instructions.strip():
            text += f"\n\nThe user's instructions:\n{instructions.strip()}"
        text += f"\n\nTranscript:\n{sample([c.text for c in source.cues])}"
        ctx.log.info(f"Tự tóm tắt video bằng {engine} ({translator.model})")
        brief = translator.ask(system, text).strip()
        LLMDriver.log_usage(ctx, translator.usage, "Đã viết bối cảnh")
        return brief


def read_meta(artifact):
    if not artifact:
        return {}
    try:
        meta = json.loads(artifact.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def describe(meta):
    """The page's metadata that tells what the video is about, as lines; the description is left to the LLM
    (links, sponsors…)."""
    lines = []
    if meta.get("title"):
        lines.append(f"Tiêu đề: {meta['title']}")
    if meta.get("channel"):
        lines.append(f"Kênh: {meta['channel']}")
    if meta.get("chapters"):
        lines.append("Các phần: " + "; ".join(meta["chapters"]))
    if meta.get("tags"):
        lines.append("Tags: " + ", ".join(meta["tags"][:15]))
    return "Thông tin video:\n" + "\n".join(lines) if lines else ""


def sample(texts, max_words=MAX_WORDS, samples=SAMPLES):
    """The transcript as one text, or SAMPLES evenly spread windows of it when longer than max_words."""
    words = " ".join(t for t in texts if t).split()
    if len(words) <= max_words:
        return " ".join(words)
    size = max_words // samples
    step = (len(words) - size) / (samples - 1)
    return " […] ".join(" ".join(words[round(n * step):round(n * step) + size]) for n in range(samples))
