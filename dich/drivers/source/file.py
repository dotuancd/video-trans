# A file on this machine: picked in the file browser or uploaded (then it lives in the uploads folder).
# A video/audio gives media.video + audio.original (the same file: ASR decodes it in-process, nothing is
# extracted), plus the subtitles given in `subtitles` (found next to it): later stages that would make
# those are then skipped. An .srt alone gives the transcript of the language in its name (<name>.vi.srt)
# or the original.
from pathlib import Path

from pydantic import BaseModel, Field

from dich.core.langs import LANGUAGES
from dich.drivers import Driver, UserError, register
from dich.formats.transcript import read_srt
from dich.media import has_video, is_media, is_subtitle


class Params(BaseModel):
    path: str
    subtitles: dict[str, str] = Field(default_factory=dict, title="Phụ đề có sẵn",
                                      description="Ngôn ngữ (src = gốc) → file .srt dùng thay vì tạo mới")


def subtitle_lang(path):
    """'vi' for a.vi.srt, None for a.srt."""
    suffix = Path(path).with_suffix("").suffix.lstrip(".").lower()
    return suffix if suffix in LANGUAGES else None


def siblings(media):
    """{"src" | lang: path} of the .srt files named like the video next to it (a.srt, a.vi.srt)."""
    media = Path(media)
    stem = media.name.removesuffix(media.suffix)
    found = {}
    for path in sorted(media.parent.iterdir()) if media.parent.is_dir() else []:
        if path.name.startswith(stem + ".") and path.suffix.lower() == ".srt":
            middle = path.name[len(stem) + 1:-4]
            if middle == "":
                found["src"] = str(path)
            elif middle.lower() in LANGUAGES:
                found[middle.lower()] = str(path)
    return found


def slot_of(key):
    return "transcript.src" if key == "src" else f"transcript.{key}"


@register
class FileSource(Driver):
    stage = "source"
    name = "file"
    title = "File trên máy / tải lên"
    Params = Params
    produces = {"video": "media.video", "audio": "audio.original"}
    cache = False    # cheap, and the file may have changed since

    @classmethod
    def outputs(cls, params, lang=None):
        path = params["path"]
        if is_subtitle(path):
            lang = subtitle_lang(path)
            return {"transcript": slot_of(lang or "src")}
        return {**cls.produces, **{f"subtitles_{key}": slot_of(key) for key in params.get("subtitles") or {}}}

    def run(self, ctx, inputs, params):
        path = Path(params.path)
        if not path.is_file():
            raise UserError(f"Không tìm thấy file {path}")
        if is_subtitle(path.name):
            transcript = read_srt(path, subtitle_lang(path))
            ctx.log.info(f"Đọc {len(transcript.cues)} câu phụ đề từ {path.name}")
            return {"transcript": transcript.save(ctx.output("transcript.jsonl"))}
        if not is_media(path.name):
            raise UserError(f"{path.name} không phải video/audio hay .srt")
        outputs = {"video": path, "audio": path}   # external: referenced, not copied
        for key, srt in params.subtitles.items():
            if not Path(srt).is_file():
                raise UserError(f"Không tìm thấy phụ đề {srt}")
            transcript = read_srt(srt, None if key == "src" else key)
            outputs[f"subtitles_{key}"] = transcript.save(ctx.output(f"transcript.{key}.jsonl"))
            ctx.log.info(f"Dùng phụ đề có sẵn {Path(srt).name} ({len(transcript.cues)} câu)")
        ctx.log.info(f"Nguồn: {path.name}" + ("" if has_video(path) else " (chỉ có âm thanh)"))
        return outputs
