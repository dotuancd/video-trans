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
# The parts (level 1) follow each other in time and cover the video from 0 to its end. `detail` sets how many
# parts and nodes the LLM is asked for and how deep the tree goes; what it returns past that is cut off. The
# video's brief and its page's title and chapters, when the job has them, go with the transcript.
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from dich.core.langs import english_name
from dich.drivers import Driver, Skip, UserError, register
from dich.drivers.brief.llm import read_meta
from dich.drivers.engines import make_translator, pick_engine, usable
from dich.drivers.translate.llm import REQUESTS, LLMDriver, RequestLog
from dich.formats.mindmap import clock
from dich.formats.transcript import Transcript

BLOCK = 25            # seconds of speech per block the LLM points at
MAX_WORDS = 40000     # of the transcript (~4 hours): one request can't take more

# detail → (levels under the root, parts asked for, nodes asked for, most nodes kept)
DETAILS = {"gọn": (2, "about 4", 15, 20), "vừa": (2, "4-8", 40, 50), "chi tiết": (3, "4-8", 60, 75)}

MINDMAP_PROMPT = """You write the mindmap of a video for its viewers: what it says, in the order it says it. \
The transcript comes in blocks, one per line: "<block id> [<time>] <text>". Write in {lang}:
- title: the video's title, under 10 words; summary: what the video is about, one sentence.
- sections: {parts} parts that follow each other in time and cover the whole video, each with the main points \
said in it{deeper} (about {nodes} nodes in all). A node has a label (under 8 words), an optional note (one short \
sentence, null when the label says it all) and the id of the block where it starts being said.
Nodes come in the order they are said. Only use block ids that are in the transcript."""


class Params(BaseModel):
    detail: Literal["gọn", "vừa", "chi tiết"] = Field(
        "vừa", title="Mức chi tiết",
        description="gọn: ~4 phần, ~15 ý, 2 cấp · vừa: 4–8 phần, ~40 ý · chi tiết: có cấp 3, ~60 ý")
    instructions: str = Field("", title="Hướng dẫn",
                              description="Ví dụ: bỏ qua phần quảng cáo, tập trung vào phần thực hành…",
                              json_schema_extra={"format": "textarea"})
    engine: Literal["auto", "openai", "claude"] = Field(
        "auto", title="AI tạo mindmap", description="auto: theo bước Dịch, không có thì AI đầu tiên dùng được")


@register
class LLMMindmap(Driver):
    stage = "mindmap"
    name = "llm"
    title = "Mindmap (AI)"
    Params = Params
    resources = {"net"}
    consumes = {"transcript": "transcript.src", "brief": "brief?", "meta": "media.meta?"}
    produces = {"mindmap": "mindmap"}

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        job = ctx.job_info()
        meta = read_meta(inputs.get("meta"))
        lang = job["langs"][0] if job["langs"] else source.lang
        engine = pick_engine(params.engine)
        if not engine or not usable()[engine]:
            raise Skip({None: "Không có OpenAI hay Claude Code nên bỏ qua mindmap",
                        "openai": "Chưa có OPENAI_API_KEY nên bỏ qua mindmap",
                        "claude": "Chưa cài Claude Code (lệnh claude) nên bỏ qua mindmap"}[engine])
        blocks = group_blocks(source.cues)
        if not blocks:
            raise UserError("Phụ đề trống, không có gì để tạo mindmap")
        words = sum(len(b["text"].split()) for b in blocks)
        if words > MAX_WORDS:
            raise UserError(f"Video quá dài để tạo mindmap ({words} từ, tối đa {MAX_WORDS} — khoảng 4 giờ)")
        depth, parts, nodes, most = DETAILS[params.detail]
        translator = make_translator(engine)
        translator.request_log = RequestLog(ctx.dir / REQUESTS)
        ctx.log.info(f"Tạo mindmap ({params.detail}) bằng {engine} ({translator.model}) từ {len(blocks)} đoạn ~{BLOCK}s")
        ctx.progress(0, 1)
        system = MINDMAP_PROMPT.format(lang=english_name(lang), parts=parts, nodes=nodes,
                                       deeper=", and under a point the details said in it" if depth > 2 else "")
        text = "\n\n".join(filter(None, [
            about(meta), brief(inputs.get("brief")),
            f"The user's instructions:\n{params.instructions.strip()}" if params.instructions.strip() else "",
            "Transcript:\n" + "\n".join(f"{b['id']} [{clock(b['start'])}] {b['text']}" for b in blocks)]))
        reply = translator.ask(system, text, kind="mindmap", schema=schema(depth))
        LLMDriver.log_usage(ctx, translator.usage, "Đã tạo mindmap")
        root, fixed = build(parse(reply), blocks, source.cues[-1].end, meta.get("title") or job["title"],
                            depth=depth, most=most)
        if fixed:
            ctx.log.warn(f"{len(fixed)} ý chỉ tới đoạn không có: " + "; ".join(fixed))
        if not root["children"]:
            raise UserError("AI không trả về mindmap dùng được")
        ctx.progress(1, 1)
        path = ctx.output("mindmap.json")
        path.write_text(json.dumps({"lang": lang, "root": root}, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"mindmap": path}


def about(meta):
    """The page's title and chapters, for the LLM."""
    lines = [f"Title: {meta['title']}"] if meta.get("title") else []
    if meta.get("chapters"):
        lines.append("Chapters: " + "; ".join(meta["chapters"]))
    return "\n".join(lines)


def brief(artifact):
    text = artifact.path.read_text(encoding="utf-8").strip() if artifact and artifact.path.exists() else ""
    return f"What the video is about:\n{text}" if text else ""


def node_schema(depth):
    properties = {"label": {"type": "string"}, "note": {"type": ["string", "null"]}, "block": {"type": "string"}}
    if depth > 1:
        properties["children"] = {"type": "array", "items": node_schema(depth - 1)}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def schema(depth):
    return {
        "type": "object",
        "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                       "sections": {"type": "array", "items": node_schema(depth)}},
        "required": ["title", "summary", "sections"],
        "additionalProperties": False,
    }


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


def build(data, blocks, duration, title=None, depth=2, most=None):
    """(root node, notes on the nodes pointing at no block) from the LLM's tree: block ids turned into seconds,
    the parts in time order, following each other from 0 to `duration`. A part at no block is dropped; a point
    at none starts with its parent. Nodes deeper than `depth` levels under the root are dropped, then the tree is
    cut down to `most` nodes (see trim)."""
    starts = {b["id"]: b["start"] for b in blocks}
    fixed = []

    def node(raw, id, parent_start, level=1):
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
        children = [c for n, raw_child in enumerate(raw.get("children") or [] if level < depth else [], 1)
                    if (c := node(raw_child, f"{id}.{n}", start, level + 1))]
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
    if most is not None:
        trim(merged, most)
    for n, part in enumerate(merged):
        if n == 0:
            part["start"] = 0
        part["end"] = merged[n + 1]["start"] if n + 1 < len(merged) else duration
    root = {"id": "root", "label": str(data.get("title") or "").strip() or title or "Mindmap"}
    if str(data.get("summary") or "").strip():
        root["note"] = str(data["summary"]).strip()
    root.update(start=0, end=duration, children=merged)
    return root, fixed


def trim(parts, most):
    """Cuts the tree (the parts, in place) down to `most` nodes, level by level: the parts first, then their
    points taken in turn from each part (the first of every part, then the second…), then the details the same
    way, so no part loses all its points to the ones before it."""
    del parts[most:]
    left = most - len(parts)
    level = parts
    while level and left > 0:
        parents = [n for n in level if n.get("children")]
        kept = {id(p): [] for p in parents}
        for rank in range(max((len(p["children"]) for p in parents), default=0)):
            for parent in parents:
                if rank < len(parent["children"]) and left > 0:
                    kept[id(parent)].append(parent["children"][rank])
                    left -= 1
        for parent in parents:
            parent["children"] = kept[id(parent)]
        level = [c for p in parents for c in p["children"]]
    for node in level:
        node.pop("children", None)
    for node in walk(parts):
        if node.get("children") == []:
            del node["children"]


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children") or [])
