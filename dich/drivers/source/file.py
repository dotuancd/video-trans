# A file on this machine: picked in the file browser or uploaded (then it lives in the uploads folder).
# A video/audio gives media.video + audio.original (the same file: ASR decodes it in-process, nothing is
# extracted). An .srt gives the transcript, of the language in its name (<name>.vi.srt) or the original.
from pathlib import Path

from pydantic import BaseModel

from dich.core.langs import LANGUAGES
from dich.drivers import Driver, UserError, register
from dich.formats.transcript import read_srt
from dich.media import has_video, is_media, is_subtitle


class Params(BaseModel):
    path: str


def subtitle_lang(path):
    """'vi' for a.vi.srt, None for a.srt."""
    suffix = Path(path).with_suffix("").suffix.lstrip(".").lower()
    return suffix if suffix in LANGUAGES else None


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
            return {"transcript": f"transcript.{lang}" if lang else "transcript.src"}
        return dict(cls.produces)

    def run(self, ctx, inputs, params):
        path = Path(params.path)
        if not path.is_file():
            raise UserError(f"Không tìm thấy file {path}")
        if is_subtitle(path.name):
            lang = subtitle_lang(path)
            transcript = read_srt(path, lang)
            ctx.log.info(f"Đọc {len(transcript.cues)} câu phụ đề từ {path.name}")
            return {"transcript": transcript.save(ctx.output("transcript.json"))}
        if not is_media(path.name):
            raise UserError(f"{path.name} không phải video/audio hay .srt")
        ctx.log.info(f"Nguồn: {path.name}" + ("" if has_video(path) else " (chỉ có âm thanh)"))
        return {"video": path, "audio": path}   # external: referenced, not copied
