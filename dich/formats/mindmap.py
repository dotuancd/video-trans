# The mindmap (mindmap.json, see dich/drivers/mindmap/llm.py) as Markdown, the export <name>.mindmap.md: the title
# and its summary, a heading per part with its time span, then its points as a list, each at its second:
#
#   # Title
#
#   One sentence.
#
#   ## 1. Part [0:00 – 3:12]
#
#   - [0:15] Point — its note
#     - [0:40] Detail
import json
from pathlib import Path


def clock(seconds):
    """m:ss, h:mm:ss past an hour."""
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def line(text):
    return " ".join(str(text or "").split())


def to_markdown(data):
    root = data["root"]
    out = [f"# {line(root['label'])}"]
    if line(root.get("note")):
        out += ["", line(root["note"])]
    parts = root.get("children") or []
    for n, part in enumerate(parts):
        end = part.get("end") or (parts[n + 1]["start"] if n + 1 < len(parts) else root.get("end"))
        span = f"{clock(part['start'])} – {clock(end)}" if end is not None else clock(part["start"])
        out += ["", f"## {n + 1}. {line(part['label'])} [{span}]"]
        if line(part.get("note")):
            out += ["", line(part["note"])]
        points = list(bullets(part.get("children") or [], 0))
        if points:
            out += ["", *points]
    return "\n".join(out) + "\n"


def bullets(nodes, depth):
    for node in nodes:
        note = line(node.get("note"))
        yield f"{'  ' * depth}- [{clock(node['start'])}] {line(node['label'])}" + (f" — {note}" if note else "")
        yield from bullets(node.get("children") or [], depth + 1)
