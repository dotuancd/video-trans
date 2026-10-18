# A video from a URL (YouTube and the ~1800 sites yt-dlp knows), downloaded into the attempt.
import json

from pydantic import BaseModel, Field

from dich.drivers import Driver, UserError, module_missing, register

META_DESCRIPTION = 3000   # characters of the description kept


class Params(BaseModel):
    url: str
    max_height: int = Field(1080, title="Độ phân giải tối đa", description="Độ phân giải tối đa")


@register
class UrlSource(Driver):
    stage = "source"
    name = "url"
    title = "Tải từ URL (yt-dlp)"
    Params = Params
    resources = {"net"}
    produces = {"video": "media.video", "audio": "audio.original", "meta": "media.meta"}
    version = "2"   # writes the page's metadata (title, description, chapters…) for the brief

    @classmethod
    def available(cls):
        return module_missing("yt_dlp") and "Chưa cài yt-dlp: uv sync --extra url"

    def run(self, ctx, inputs, params):
        import yt_dlp

        if not params.url.startswith(("http://", "https://")):
            raise UserError("URL phải bắt đầu bằng http:// hoặc https://")

        def hook(status):
            if status.get("status") == "downloading" and status.get("total_bytes"):
                ctx.progress(status.get("downloaded_bytes", 0), status["total_bytes"])

        options = {
            "outtmpl": str(ctx.outdir / "%(title).80B [%(id)s].%(ext)s"),
            "format": f"bv*[height<={params.max_height}]+ba/b[height<={params.max_height}]/b",
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "noplaylist": True,
            "quiet": True,
        }
        ctx.log.info(f"Tải {params.url}")
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(params.url, download=True)
                path = ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as error:
            raise UserError(f"Không tải được video từ URL: {str(error).removeprefix('ERROR: ')[:200]}")
        files = sorted(ctx.outdir.glob(f"*[[]{info['id']}[]].*"), key=lambda p: p.stat().st_size, reverse=True)
        path = files[0] if files else path
        ctx.log.info(f"Đã tải: {info.get('title')}")
        if info.get("title"):
            ctx.set_job_title(info["title"])
        return {"video": path, "audio": path, "meta": write_meta(info, ctx.output("meta.json"))}


def write_meta(info, path):
    """What the page says about the video, free context for the brief: no request beyond the download."""
    meta = {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "description": (info.get("description") or "")[:META_DESCRIPTION],
        "chapters": [c.get("title") for c in info.get("chapters") or [] if c.get("title")],
        "tags": (info.get("tags") or [])[:30],
        "categories": info.get("categories") or [],
    }
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
