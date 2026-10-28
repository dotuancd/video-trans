# The mindmap: what the video says, in order, as a tree whose every node starts at a real moment of the video
# (clicked in the player, the video goes there). Written by one LLM request on the whole transcript.
#
# The cues are grouped into blocks of about BLOCK seconds (never cutting a cue), each with a stable id. The LLM
# points each node at the block it starts in and never writes seconds: the code turns the block back into the
# second it starts at, so a node can't land where nothing is said. The artifact (mindmap.json):
#
#   {"lang": "vi", "root": {"id": "root", "label": <title>, "note": <one sentence>, "start": 0, "end": <duration>,
#                           "children": [<part>, …]}}
#   node: {"id", "label", "note"?, "start", "end"? (root and parts only), "children"?}
#
# The parts (level 1) follow each other in time and cover the video from 0 to its end.
import json
import re

from pydantic import BaseModel

from dich.core.langs import english_name
from dich.drivers import Driver, UserError, register
from dich.drivers.engines import make_translator, pick_engine
from dich.drivers.translate.llm import REQUESTS, LLMDriver, RequestLog
from dich.formats.transcript import Transcript

BLOCK = 25            # seconds of speech per block the LLM points at
MAX_WORDS = 40000     # of the transcript (~4 hours): one request can't take more
DEPTH = 2             # levels under the root
PARTS = "4-8"
NODES = 40

MINDMAP_PROMPT = """You write the mindmap of a video for its viewers: what it says, in the order it says it. \
The transcript comes in blocks, one per line: "<block id> [<time>] <text>". Write in {lang}:
- title: the video's title, under 10 words; summary: what the video is about, one sentence.
- sections: {parts} parts that follow each other in time and cover the whole video, each with the main points \
said in it (about {nodes} nodes in all). A node has a label (under 8 words), an optional note (one short \
sentence, null when the label says it all) and the id of the block where it starts being said.
Nodes come in the order they are said. Only use block ids that are in the transcript."""


class Params(BaseModel):
    pass


@register
class LLMMindmap(Driver):
    stage = "mindmap"
    name = "llm"
    title = "Mindmap (AI)"
    Params = Params
    resources = {"net"}
    consumes = {"transcript": "transcript.src"}
    produces = {"mindmap": "mindmap"}

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        job = ctx.job_info()
        lang = job["langs"][0] if job["langs"] else source.lang
        blocks = group_blocks(source.cues)
        if not blocks:
            raise UserError("Phụ đề trống, không có gì để tạo mindmap")
        words = sum(len(b["text"].split()) for b in blocks)
        if words > MAX_WORDS:
            raise UserError(f"Video quá dài để tạo mindmap ({words} từ, tối đa {MAX_WORDS})")
        engine = pick_engine("auto")
        if not engine:
            raise UserError("Không có OpenAI hay Claude Code để tạo mindmap")
        translator = make_translator(engine)
        translator.request_log = RequestLog(ctx.dir / REQUESTS)
        ctx.log.info(f"Tạo mindmap bằng {engine} ({translator.model}) từ {len(blocks)} đoạn ~{BLOCK}s")
        ctx.progress(0, 1)
        system = MINDMAP_PROMPT.format(lang=english_name(lang), parts=PARTS, nodes=NODES)
        reply = translator.ask(system, "\n".join(f"{b['id']} [{clock(b['start'])}] {b['text']}" for b in blocks),
                               kind="mindmap", schema=SCHEMA)
        LLMDriver.log_usage(ctx, translator.usage, "Đã tạo mindmap")
        root, fixed = build(parse(reply), blocks, source.cues[-1].end, job["title"])
        if fixed:
            ctx.log.warn(f"{len(fixed)} ý chỉ tới đoạn không có: " + "; ".join(fixed))
        if not root["children"]:
            raise UserError("AI không trả về mindmap dùng được")
        ctx.progress(1, 1)
        path = ctx.output("mindmap.json")
        path.write_text(json.dumps({"lang": lang, "root": root}, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"mindmap": path}


def node_schema(depth):
    properties = {"label": {"type": "string"}, "note": {"type": ["string", "null"]}, "block": {"type": "string"}}
    if depth > 1:
        properties["children"] = {"type": "array", "items": node_schema(depth - 1)}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                   "sections": {"type": "array", "items": node_schema(DEPTH)}},
    "required": ["title", "summary", "sections"],
    "additionalProperties": False,
}


def clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def group_blocks(cues, size=BLOCK):
    """The cues with text as blocks of about `size` seconds, a cue never cut: [{"id", "start", "text"}]."""
    blocks = []
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        if blocks and cue.end - blocks[-1]["start"] <= size:
            blocks[-1]["text"] += " " + text
        else:
            blocks.append({"id": f"b_{len(blocks):03d}", "start": cue.start, "text": text})
    return blocks


def parse(reply):
    """The LLM's JSON, from a fenced code block too; {} when it is none."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (reply or "").strip())
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def build(data, blocks, duration, title=None):
    """(root node, notes on the nodes pointing at no block) from the LLM's tree: block ids turned into seconds,
    the parts in time order, following each other from 0 to `duration`. A part at no block is dropped; a point
    at none starts with its parent."""
    starts = {b["id"]: b["start"] for b in blocks}
    fixed = []

    def node(raw, id, parent_start):
        if not isinstance(raw, dict) or not str(raw.get("label") or "").strip():
            return None
        label = str(raw["label"]).strip()
        start = starts.get(raw.get("block"))
        if start is None:
            fixed.append(f"“{label}” ({raw.get('block')}): " + ("bỏ" if parent_start is None else "theo ý cha"))
            if parent_start is None:
                return None
            start = parent_start
        if parent_start is not None:
            start = max(start, parent_start)
        out = {"id": id, "label": label}
        if str(raw.get("note") or "").strip():
            out["note"] = str(raw["note"]).strip()
        out["start"] = start
        children = [c for n, raw_child in enumerate(raw.get("children") or [], 1)
                    if (c := node(raw_child, f"{id}.{n}", start))]
        if children:
            out["children"] = sorted(children, key=lambda c: c["start"])
        return out

    parts = sorted((p for n, raw in enumerate(data.get("sections") or [], 1) if (p := node(raw, f"s{n}", None))),
                   key=lambda p: p["start"])
    merged = []
    for part in parts:
        if merged and part["start"] <= merged[-1]["start"]:   # starts with the one before: one part
            children = merged[-1].setdefault("children", [])
            children.extend(part.get("children") or [])
            children.sort(key=lambda c: c["start"])
        else:
            merged.append(part)
    for n, part in enumerate(merged):
        if n == 0:
            part["start"] = 0
        part["end"] = merged[n + 1]["start"] if n + 1 < len(merged) else duration
    root = {"id": "root", "label": str(data.get("title") or "").strip() or title or "Mindmap"}
    if str(data.get("summary") or "").strip():
        root["note"] = str(data["summary"]).strip()
    root.update(start=0, end=duration, children=merged)
    return root, fixed
