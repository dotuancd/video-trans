# Results are copied out of the workspace to where the user looks for them: next to the source file
# (picked in the file browser), or the job's export folder (uploads, URLs). Transcripts become .srt.
#
#   transcript.src  → <name>.srt          audio.dub.vi → <name>.vi.wav
#   transcript.vi   → <name>.vi.srt       media.out.vi → <name>.dub.vi.mp4
#   mindmap         → <name>.mindmap.md
from pathlib import Path

from dich.formats import mindmap
from dich.formats.transcript import Transcript, to_srt
from dich.storage.artifacts import link_or_copy


def export_name(slot, stem, source_suffix):
    kind, _, rest = slot.partition(".")
    if kind == "transcript":
        return f"{stem}.srt" if rest == "src" else f"{stem}.{rest}.srt"
    if slot.startswith("audio.dub."):
        return f"{stem}.{slot.removeprefix('audio.dub.')}.wav"
    if slot.startswith("media.out."):
        return f"{stem}.dub.{slot.removeprefix('media.out.')}{source_suffix}"
    if slot == "mindmap":
        return f"{stem}.mindmap.md"
    return None


def source_stem(path):
    """a for a.mp4, a.vi.srt and a.srt; the title part of a yt-dlp download name."""
    path = Path(path)
    stem = path.name.removesuffix(path.suffix)
    if path.suffix.lower() == ".srt":
        stem = stem.rsplit(".", 1)[0] if "." in stem and len(stem.rsplit(".", 1)[1]) <= 3 else stem
    if stem.endswith("]") and " [" in stem:
        stem = stem.rsplit(" [", 1)[0]
    return stem


def download_name(source, art):
    """The name an artifact is downloaded as: like its export, after the job's source."""
    from dich.core.jobs import is_url
    base = Path(art["path"]) if is_url(source) else Path(source)
    return export_name(art["slot"], source_stem(base), Path(art["path"]).suffix) or Path(art["path"]).name


def as_text(artifact):
    """What a transcript or the mindmap is exported and downloaded as (.srt, .md); None for the others (as is)."""
    if artifact["slot"].startswith("transcript."):
        return to_srt(Transcript.load(artifact["path"]))
    if artifact["slot"] == "mindmap":
        return mindmap.to_markdown(mindmap.load(artifact["path"]))
    return None


def export(artifact, slot, export_dir, source):
    """Path written for the artifact, or None when the slot is not exported. Never overwrites `source`."""
    if not export_dir:
        return None
    source = Path(source)
    name = export_name(slot, source_stem(source), source.suffix if source.suffix.lower() != ".srt" else ".mp4")
    if not name:
        return None
    target = Path(export_dir) / name
    if target.resolve() == source.resolve():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    text = as_text({**artifact, "slot": slot})
    if text is not None:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    else:
        link_or_copy(artifact["path"], target)
    return target
