# Config, drivers, templates, files, uploads, voices, the live event stream and the admin trace view.
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from dich import drivers, settings
from dich.api.models import (Config, DriverInfo, Entry, Language, Listing, PathBody, Preview, TemplateInfo, Uploaded,
                             Voice)
from dich.core.ids import is_id, new_id
from dich.core.langs import LANGUAGES
from dich.core.template import load_templates
from dich.formats.transcript import Transcript, read_srt
from dich.media import is_media, is_subtitle

POLL = 0.3   # seconds between reads of the events table


def kind_of(path):
    if path.is_dir():
        return "dir"
    if is_subtitle(path.name):
        return "subtitle"
    return "media" if is_media(path.name) else "other"


def router(ctx):
    api = APIRouter()
    store = ctx.store
    voices_cache = []

    @api.get("/config")
    def get_config() -> Config:
        return Config(root=str(ctx.root), languages=[Language(code=c, name=v[2]) for c, v in LANGUAGES.items()],
                      default_langs=["vi"], default_drivers={"translate": settings.ENGINE})

    @api.get("/drivers")
    def list_drivers() -> list[DriverInfo]:
        return [DriverInfo(stage=cls.stage, name=cls.name, title=cls.title or cls.name,
                           params_schema=cls.Params.model_json_schema(), available=cls.available(),
                           langs=sorted(cls.langs) if cls.langs else None, per_lang=cls.per_lang(),
                           lane=drivers.lane(cls))
                for cls in drivers.load_all().values() if not cls.__module__.startswith("fake_")]

    @api.get("/templates")
    def list_templates() -> list[TemplateInfo]:
        return [TemplateInfo(name=t.name, title=t.title, description=t.description,
                             stages=[s.model_dump() for s in t.stages]) for t in load_templates().values()]

    # --- files ---

    @api.get("/fs")
    def list_dir(path: str = "") -> Listing:
        folder = ctx.existing(path or ctx.root)
        if folder.is_file():
            folder = folder.parent
        entries = [Entry(name=p.name, path=str(p), dir=p.is_dir(), kind=kind_of(p))
                   for p in folder.iterdir() if not p.name.startswith(".")]
        entries.sort(key=lambda e: (not e.dir, e.name.lower()))
        return Listing(path=str(folder), parent=str(folder.parent) if folder != ctx.root else None, entries=entries)

    @api.put("/uploads", openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}}})
    async def upload(request: Request, name: str, id: str | None = None) -> Uploaded:
        """The request body is the file. Each upload gets a folder (pass the same `id` to put more files
        in it); the job's results are exported into it too."""
        name = Path(name).name
        if not (is_media(name) or is_subtitle(name)):
            raise HTTPException(400, f"{name}: chỉ nhận video/audio hoặc .srt")
        if id is not None and not (is_id(id, "upl") and (ctx.uploads / id).is_dir()):
            raise HTTPException(400, "Thư mục upload không hợp lệ")
        id = id or new_id("upl")
        target = ctx.uploads / id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            with part.open("wb") as file:
                async for chunk in request.stream():
                    file.write(chunk)
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
        return Uploaded(id=id, path=str(target), kind=kind_of(target))

    @api.post("/reveal", status_code=204)
    def reveal(body: PathBody):
        """Show the file in Finder / the file manager."""
        path = ctx.existing(body.path, extra=[ctx.data_dir])
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path if path.is_dir() else path.parent)], check=False)

    # --- voices: answered by the interactive worker, which keeps the TTS model loaded ---

    async def call(kind, args, timeout=180):
        call_id = store.call(kind, args)
        result, error = await asyncio.to_thread(store.wait_call, call_id, timeout)
        if error:
            raise HTTPException(503 if error.startswith("Chưa cài") else 400, error)
        return result

    @api.get("/voices")
    async def list_voices() -> list[Voice]:
        if not voices_cache:
            voices_cache.extend(await call("voices", {}))
        return voices_cache

    @api.post("/voices/upload")
    def upload_voice(file: UploadFile) -> PathBody:
        """Keep a 3–8s clip to clone a voice from; returns its path for the dub step's ref_audio."""
        data = file.file.read()
        suffix = Path(file.filename or "clip.wav").suffix.lower() or ".wav"
        ctx.voices.mkdir(parents=True, exist_ok=True)
        stem = Path(file.filename or "clip").stem
        target = ctx.voices / f"{stem}-{hashlib.sha1(data).hexdigest()[:8]}{suffix}"
        target.write_bytes(data)
        return PathBody(path=str(target))

    def spans(transcript):
        return [[c.start, c.end, c.text] for c in transcript.cues] if transcript else []

    def preview_files(body):
        """(video, cues timing the clip to clone, cues to read) of a job or of a video with its .srt next to it."""
        if body.job_id:
            _t, video = store.slot_artifact(body.job_id, "media.video")
            _t, target = store.slot_artifact(body.job_id, f"transcript.{body.lang}")
            _t, source = store.slot_artifact(body.job_id, "transcript.src")
            target = Transcript.load(target["path"]) if target else None
            source = Transcript.load(source["path"]) if source else None
            return video["path"] if video else None, spans(target or source), spans(target)
        if body.path:
            media = ctx.existing(body.path)
            stem = media.with_suffix("")
            near = {lang: Path(f"{stem}{suffix}") for lang, suffix in ((None, ".srt"), (body.lang, f".{body.lang}.srt"))}
            found = {lang: read_srt(p) for lang, p in near.items() if p.is_file()}
            target = found.get(body.lang)
            return str(media), spans(target or found.get(None)), spans(target)
        return None, [], []

    @api.post("/voices/preview", response_class=FileResponse, responses={200: {"content": {"audio/wav": {}}}})
    async def preview_voice(body: Preview):
        """Reads text (or a sentence of the subtitles) in the voice the dub step would use.
        The text read is in the X-Preview-Text header, URL-encoded."""
        media, timing, cues = preview_files(body)
        ref_audio = str(ctx.existing(body.ref_audio, extra=[ctx.voices])) if body.ref_audio else None
        result = await call("preview", {"voice": body.voice, "ref_audio": ref_audio, "int8": body.int8,
                                        "text": body.text, "media": media, "timing": timing, "cues": cues})
        return FileResponse(result["path"], media_type="audio/wav", headers={"X-Preview-Text": quote(result["text"])})

    # --- live events ---

    @api.get("/events", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
    async def events(request: Request):
        """{"type": "job", "job"} when a job or one of its tasks changed, {"type": "progress", "job_id", "task_id",
        "done", "total"}, {"type": "log", "job_id", "task_id", "event"}, {"type": "deleted", "id"}."""

        async def stream():
            cursor = await asyncio.to_thread(store.last_event_id)
            yield ": connected\n\n"
            idle = 0.0
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(store.events_after, cursor, 500)
                if not rows:
                    await asyncio.sleep(POLL)
                    idle += POLL
                    if idle >= 15:
                        idle = 0.0
                        yield ": ping\n\n"
                    continue
                idle = 0.0
                cursor = rows[-1]["id"]
                changed, out = [], []
                for row in rows:
                    data = row["data"] or {}
                    if row["type"] in ("task", "job"):
                        if data.get("deleted"):
                            out.append({"type": "deleted", "id": row["job_id"]})
                        elif row["job_id"] not in changed:
                            changed.append(row["job_id"])
                    elif row["type"] == "progress":
                        out.append({"type": "progress", "job_id": row["job_id"], "task_id": row["task_id"],
                                    "done": data.get("done"), "total": data.get("total")})
                    elif row["type"] == "log":
                        out.append({"type": "log", "job_id": row["job_id"], "task_id": row["task_id"], "event": row})
                for job_id in changed:
                    job = await asyncio.to_thread(job_with_artifacts, job_id)
                    if job:
                        out.append({"type": "job", "job": job})
                for event in out:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def job_with_artifacts(job_id):
        from dich.api.jobs import attach_artifacts
        job = store.job(job_id)
        return attach_artifacts(store, [job])[0] if job else None

    # --- admin ---

    @api.get("/admin/traces/{trace_id}", response_class=PlainTextResponse)
    def trace(trace_id: str):
        """The system logs of every attempt of the job with this trace id (or an error code / span id)."""
        rows = store.q("select a.*, t.key from attempts a join tasks t on a.task_id = t.id join jobs j on t.job_id = j.id"
                       " where j.trace_id = ? or a.span_id like ? order by a.rowid", trace_id, f"{trace_id}%")
        if not rows:
            raise HTTPException(404, "Không có trace này")
        parts = []
        for row in rows:
            log = Path(row["dir"]) / "system.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
            parts.append(f"===== {row['key']} #{row['n']} span={row['span_id']} {row['status']} =====\n{text}")
        return PlainTextResponse("\n".join(parts))

    @api.get("/attempts/{attempt_id}/system", response_class=PlainTextResponse)
    def attempt_system_log(attempt_id: str, tail: int = 200_000):
        attempt = store.attempt(attempt_id)
        if not attempt:
            raise HTTPException(404, "Không có lần chạy này")
        log = Path(attempt["dir"]) / "system.log"
        data = log.read_bytes()[-tail:] if log.is_file() else b""
        return PlainTextResponse(data.decode("utf-8", errors="replace"))

    return api
