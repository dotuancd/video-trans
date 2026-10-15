# HTTP API for the web UI (`uv run dich ui`): REST for jobs/files, Server-Sent Events for live
# step status, and the built React app (web/dist) served from /. Listens on 127.0.0.1 only and
# reads/writes files under --root only (plus the files and voice clips uploaded from it).
import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

import srt
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import settings
from dich import STEP_OPTIONS, DichError, find_files, find_media, is_media, is_source_srt, read_srt
from dich_server.pipeline import DEFAULT_DB, Busy, Pipeline, Store
from subtitle import words_path

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
VOICES_DIR = DEFAULT_DB.parent / "voices"
UPLOADS_DIR = settings.STORAGE_DIR / "uploads"
ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ACCEPT = {"media": is_media, "srt": is_source_srt, "vi_srt": lambda name: name.endswith(".vi.srt")}
SAMPLE_TEXT = "Xin chào, đây là giọng đọc thử cho video của bạn."
MIN_PREVIEW_CHARS = 40
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi")

StepName = Literal["transcribe", "translate", "dub", "mux"]
Status = Literal["queued", "running", "done", "failed", "cancelled", "stale"]


class Step(BaseModel):
    id: int
    job_id: int
    position: int
    name: StepName
    inputs: list[str]
    output: str
    params: dict
    status: Status
    exists: bool = Field(description="the output file is on disk")
    message: str | None
    error: str | None
    done: int | None
    total: int | None
    attempt: int
    started_at: float | None
    finished_at: float | None


class Job(BaseModel):
    id: int
    source: str
    start: Literal["media", "srt", "vi_srt"]
    created_at: float
    status: Status
    steps: list[Step]


class JobCreate(BaseModel):
    path: str | None = Field(None, description="a file or a folder of them; or give `paths`")
    paths: list[str] = Field(default_factory=list, description="files, each its own job with outputs next to it")
    start: Literal["media", "srt", "vi_srt"] = "media"
    recursive: bool = False
    translate: bool = True
    dub: bool = False
    mux: bool = False
    overwrite: bool = False
    out: str | None = None
    params: dict = Field(default_factory=dict, description="options of every step, see /api/config")


class JobCreated(BaseModel):
    jobs: list[int]
    skipped: list[str]


class RunStep(BaseModel):
    params: dict | None = None
    cascade: bool = True


class StepLog(BaseModel):
    text: str
    offset: int = Field(description="byte offset of `text` in the log (later than asked when the log is long)")
    size: int = Field(description="log size in bytes: the offset to ask for next")


class Entry(BaseModel):
    name: str
    path: str
    dir: bool
    kind: Literal["dir", "media", "srt", "vi_srt", "dub", "audio", "other"]


class Listing(BaseModel):
    path: str
    parent: str | None
    entries: list[Entry]


class Config(BaseModel):
    root: str
    defaults: dict[str, dict]


class Cue(BaseModel):
    index: int
    start: float
    end: float
    source: str
    text: str


class Word(BaseModel):
    word: str
    start: float
    end: float


class Subtitles(BaseModel):
    path: str = Field(description="the file whose cues are `text`: a .vi.srt, or an original .srt")
    source_path: str | None = Field(description="the original .srt whose cues are `source`, when `path` is a .vi.srt")
    cues: list[Cue]
    words: list[Word] | None = Field(None, description="the timed words the original .srt was grouped from "
                                     "(<stem>.words.json), to cut cues where a word is spoken; none for older jobs")


class CueEdit(BaseModel):
    start: float
    end: float
    text: str
    source: str | None = Field(None, description="the original line; given on every cue to rewrite the original .srt too")


class SubtitlesSave(BaseModel):
    path: str
    cues: list[CueEdit] = Field(description="the whole new list: cues may have been merged, split or retimed")


class SubtitlesSaved(BaseModel):
    texts: int = Field(description="cues changed in `path`")
    sources: int = Field(description="cues changed in the original .srt")


class Voice(BaseModel):
    label: str
    name: str


class Preview(BaseModel):
    text: str | None = Field(None, description="what to read; empty: a sentence of `subtitles`")
    voice: str | None = Field(None, description="preset name, or 'source' to clone the speaker of `media`")
    ref_audio: str | None = None
    int8: bool = False
    media: str | None = Field(None, description="the video the voice is cloned from; empty: the one next to `subtitles`")
    subtitles: str | None = Field(None, description="the .vi.srt (or .srt): its timings pick the clip, its text is read; "
                                  "empty: the ones next to `media`")


class PathBody(BaseModel):
    path: str


class Uploaded(BaseModel):
    id: str = Field(description="the upload folder: pass it with the other files of the same video")
    path: str
    kind: Literal["dir", "media", "srt", "vi_srt", "dub", "audio", "other"]


def ulid():
    """26 chars, sortable by creation time: 48 bits of milliseconds then 80 random bits."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def kind_of(path):
    name = path.name.lower()
    if path.is_dir():
        return "dir"
    if name.endswith(".vi.srt"):
        return "vi_srt"
    if name.endswith(".srt"):
        return "srt"
    if Path(name).with_suffix("").suffix == ".dub":
        return "dub"
    if name.endswith(".vi.wav"):
        return "audio"
    return "media" if is_media(name) else "other"


def create_app(root, db_path=DEFAULT_DB):
    root = Path(root).expanduser().resolve()
    pipeline = Pipeline(Store(db_path))

    def inside(path, allow_voices=False):
        path = Path(path).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        allowed = [root, UPLOADS_DIR.resolve()] + ([VOICES_DIR.resolve()] if allow_voices else [])
        if not any(path == base or base in path.parents for base in allowed):
            raise HTTPException(403, f"Chỉ được dùng file trong {root}")
        return path

    def existing(path, **options):
        path = inside(path, **options)
        if not path.exists():
            raise HTTPException(404, f"Không tìm thấy {path}")
        return path

    @asynccontextmanager
    async def lifespan(_app):
        pipeline.start()
        yield
        pipeline.stop()

    app = FastAPI(title="dich", lifespan=lifespan, generate_unique_id_function=lambda route: route.name)
    app.state.pipeline = pipeline

    @app.get("/api/config")
    def get_config() -> Config:
        return Config(root=str(root), defaults=STEP_OPTIONS)

    @app.get("/api/fs")
    def list_dir(path: str = "") -> Listing:
        folder = existing(path or root)
        if folder.is_file():
            folder = folder.parent
        entries = [Entry(name=p.name, path=str(p), dir=p.is_dir(), kind=kind_of(p))
                   for p in folder.iterdir() if not p.name.startswith(".")]
        entries.sort(key=lambda e: (not e.dir, e.name.lower()))
        return Listing(path=str(folder), parent=str(folder.parent) if folder != root else None, entries=entries)

    # --- jobs ---

    @app.get("/api/jobs")
    def list_jobs() -> list[Job]:
        return pipeline.store.jobs()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int) -> Job:
        found = pipeline.store.job(job_id)
        if not found:
            raise HTTPException(404, "Không có job này")
        return found

    @app.post("/api/jobs")
    def add_jobs(body: JobCreate) -> JobCreated:
        options = (body.translate, body.dub, body.mux, body.overwrite, body.params)
        if body.paths:
            added, skipped = [], []
            for source in map(existing, body.paths):
                if not source.is_file() or not ACCEPT[body.start](source.name.lower()):
                    raise HTTPException(400, f"{source.name} không phải file cần cho bước đầu")
                more, notes = pipeline.add([source], body.start, source.parent, None, *options)
                added += more
                skipped += notes
            return JobCreated(jobs=added, skipped=skipped)
        if not body.path:
            raise HTTPException(400, "Chưa chọn file hoặc thư mục")
        path = existing(body.path)
        out = inside(body.out) if body.out else None
        base = path.parent if path.is_file() else path
        sources = find_files(path, body.recursive, ACCEPT[body.start])
        if not sources:
            kind = {"media": "video/audio", "srt": ".srt", "vi_srt": ".vi.srt"}[body.start]
            raise HTTPException(400, f"Không có file {kind} nào trong {path}")
        added, skipped = pipeline.add(sources, body.start, base, out, *options)
        return JobCreated(jobs=added, skipped=skipped)

    @app.post("/api/jobs/{job_id}/steps/{name}/run")
    def run_step(job_id: int, name: StepName, body: RunStep) -> Job:
        try:
            pipeline.rerun(job_id, name, body.params, body.cascade)
        except KeyError:
            raise HTTPException(404, "Job không có bước này")
        except Busy as error:
            raise HTTPException(409, str(error))
        return pipeline.store.job(job_id)

    @app.get("/api/jobs/{job_id}/steps/{name}/log")
    def get_step_log(job_id: int, name: StepName, offset: int = 0) -> StepLog:
        step = pipeline.store.step(job_id, name)
        if not step:
            raise HTTPException(404, "Job không có bước này")
        text, start, size = pipeline.logs.read(job_id, step["id"], offset)
        return StepLog(text=text, offset=start, size=size)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> Job:
        pipeline.cancel(job_id)
        return pipeline.store.job(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: int):
        try:
            pipeline.delete(job_id)
        except Busy as error:
            raise HTTPException(409, str(error))

    @app.get("/api/events", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}}})
    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def listener(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def stream():
            pipeline.listeners.add(listener)
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                pipeline.listeners.discard(listener)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- subtitles ---

    def original_of(vi_path):
        if not vi_path.name.endswith(".vi.srt"):
            return None
        original = vi_path.with_name(vi_path.name.removesuffix(".vi.srt") + ".srt")
        return original if original.is_file() else None

    @app.get("/api/subtitles")
    def get_subtitles(path: str) -> Subtitles:
        """A .vi.srt with the original line of each cue next to it, or an original .srt alone."""
        path = existing(path)
        original = original_of(path)
        sources = {s.start: s.content for s in read_srt(original)} if original else {}
        cues = [Cue(index=s.index, start=s.start.total_seconds(), end=s.end.total_seconds(),
                    source=sources.get(s.start, ""), text=s.content) for s in read_srt(path)]
        return Subtitles(path=str(path), source_path=str(original) if original else None, cues=cues,
                         words=read_words(original or path))

    @app.put("/api/subtitles")
    def save_subtitles(body: SubtitlesSave) -> SubtitlesSaved:
        """Rewrite the cues (texts, and timings when cues were merged or split), and the original .srt with
        the same timings when every cue carries its `source`."""
        path = existing(body.path)
        cues = sorted(body.cues, key=lambda c: c.start)
        if not cues or any(c.end <= c.start for c in cues):
            raise HTTPException(400, "Câu phụ đề phải có thời gian kết thúc sau thời gian bắt đầu")
        with_source = all(c.source is not None for c in cues)
        if any(c.source is not None for c in cues) and not with_source:
            raise HTTPException(400, "Thiếu câu gốc ở vài câu")
        original = original_of(path) if with_source else None
        if with_source and not original:
            raise HTTPException(400, "Không có phụ đề gốc cạnh file này")
        changed = write_cues(path, [(c.start, c.end, c.text) for c in cues])
        source_changed = write_cues(original, [(c.start, c.end, c.source) for c in cues]) if original else 0
        return SubtitlesSaved(texts=changed, sources=source_changed)

    @app.get("/api/subtitles/vtt", response_class=PlainTextResponse)
    def subtitles_vtt(path: str):
        cues = read_srt(existing(path))
        lines = ["WEBVTT", ""]
        for cue in cues:
            start = srt.timedelta_to_srt_timestamp(cue.start).replace(",", ".")
            end = srt.timedelta_to_srt_timestamp(cue.end).replace(",", ".")
            lines += [f"{start} --> {end}", cue.content, ""]
        return PlainTextResponse("\n".join(lines), media_type="text/vtt")

    # --- uploads ---

    @app.put("/api/uploads", openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}}})
    async def upload(request: Request, name: str, id: str | None = None) -> Uploaded:
        """The request body is the file. Each video gets a folder; upload its .srt / .vi.srt with the
        same `id` so the steps find them next to it. Outputs are written in that folder too."""
        name = Path(name).name
        if not (is_media(name.lower()) or name.lower().endswith(".srt")):
            raise HTTPException(400, f"{name}: chỉ nhận video/audio, .srt hoặc .vi.srt")
        if id is not None and not (ULID.fullmatch(id) and (UPLOADS_DIR / id).is_dir()):
            raise HTTPException(400, "Thư mục upload không hợp lệ")
        id = id or ulid()
        target = UPLOADS_DIR / id / name
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

    # --- media ---

    @app.get("/api/media", response_class=FileResponse)
    def media(path: str):
        return FileResponse(existing(path, allow_voices=True))

    @app.post("/api/reveal", status_code=204)
    def reveal(body: PathBody):
        """Show the file in Finder / the file manager."""
        path = existing(body.path)
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path if path.is_dir() else path.parent)], check=False)

    # --- voices ---

    @app.get("/api/voices")
    def list_voices() -> list[Voice]:
        try:
            from tts import preset_voices
            return [Voice(label=label, name=name) for label, name in preset_voices(pipeline.steps.dubber().tts)]
        except ImportError:
            raise HTTPException(503, "Chưa cài lồng tiếng: uv sync --extra tts")

    @app.post("/api/voices/upload")
    def upload_voice(file: UploadFile) -> PathBody:
        """Keep a 3–8s clip to clone a voice from; returns its path for the dub step's ref_audio."""
        data = file.file.read()
        suffix = Path(file.filename or "clip.wav").suffix.lower() or ".wav"
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(file.filename or "clip").stem
        target = VOICES_DIR / f"{stem}-{hashlib.sha1(data).hexdigest()[:8]}{suffix}"
        target.write_bytes(data)
        return PathBody(path=str(target))

    @app.post("/api/voices/preview", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
    def preview_voice(body: Preview):
        """Reads text (or a sentence of the subtitles) in the voice the dub step would use.
        The text read is in the X-Preview-Text header, URL-encoded."""
        import soundfile as sf
        from tts import SAMPLE_RATE, SOURCE_VOICE, group_cues, overlapping_text, reference_clip

        media, timing, cues = preview_files(existing(body.media) if body.media else None,
                                            existing(body.subtitles) if body.subtitles else None)
        ref_audio = existing(body.ref_audio, allow_voices=True) if body.ref_audio else None
        window = None
        if not ref_audio and body.voice == SOURCE_VOICE:
            if not (media and timing):
                raise HTTPException(400, "Cần video gốc và phụ đề (.srt hoặc .vi.srt) cạnh nó để nghe thử giọng gốc")
            clip = reference_clip(media, timing)
            if clip is None:
                raise HTTPException(400, "Không tìm thấy đoạn nói liền 3 giây nào để clone giọng")
            ref_audio, window = clip
        text = (body.text or "").strip()
        if not text and cues:   # the moment the voice was cloned from, else a sentence long enough to judge
            sentences = [t for _start, _end, t in group_cues(cues)]
            text = ((overlapping_text(cues, window) if window else None)
                    or next((t for t in sentences if len(t) >= MIN_PREVIEW_CHARS), None)
                    or (sentences[0] if sentences else ""))
        text = text or SAMPLE_TEXT
        try:
            dubber = pipeline.steps.dubber(body.voice, ref_audio, body.int8)
        except ImportError:
            raise HTTPException(503, "Chưa cài lồng tiếng: uv sync --extra tts")
        buffer = io.BytesIO()
        sf.write(buffer, dubber.synthesize(text), SAMPLE_RATE, format="WAV")
        return Response(buffer.getvalue(), media_type="audio/wav",
                        headers={"X-Preview-Text": quote(text)})

    @app.exception_handler(DichError)
    def dich_error(_request, error):
        return PlainTextResponse(str(error), status_code=400)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def missing_web():
            return "Chưa build giao diện: cd web && npm install && npm run build"
    return app


def read_words(srt_path):
    """The words saved next to an original .srt by the Subtitles step, if any."""
    path = words_path(srt_path)
    if srt_path.name.endswith(".vi.srt") or not path.is_file():
        return None
    try:
        return [Word(**w) for w in json.loads(path.read_text(encoding="utf-8"))]
    except (ValueError, TypeError):
        return None


def write_cues(path, cues):
    """Rewrites path with (start, end, text) cues; returns how many are not in it as they were."""
    before = {(c.start, c.end, c.content) for c in read_srt(path)}
    subtitles = [srt.Subtitle(index=i + 1, start=timedelta(milliseconds=round(start * 1000)),
                              end=timedelta(milliseconds=round(end * 1000)),
                              content=text.strip())
                 for i, (start, end, text) in enumerate(cues)]
    changed = sum((s.start, s.end, s.content) not in before for s in subtitles)
    if changed or len(subtitles) != len(before):
        path.write_text(srt.compose(subtitles, reindex=False), encoding="utf-8")
    return changed


def preview_files(media, subtitles):
    """What a voice preview uses, from the video and/or a subtitle file of it (the other found next to it):
    (video or None, cues timing the clip to clone, Vietnamese cues to read). Before translation the
    original .srt still times the clip; the text read is then the fixed sample."""
    if media is None and subtitles is not None:
        media = find_media(subtitles)
    if media is None and subtitles is None:
        return None, [], []
    base = media.with_suffix("") if media else subtitles.with_name(subtitles.name.removesuffix(".srt").removesuffix(".vi"))
    candidates = ([subtitles] if subtitles else []) + [base.with_name(base.name + ".vi.srt"), base.with_name(base.name + ".srt")]
    vi = next((p for p in candidates if p.name.endswith(".vi.srt") and p.is_file()), None)
    original = next((p for p in candidates if is_source_srt(p.name) and p.is_file()), None)
    cues = read_srt(vi) if vi else []
    return media, cues or (read_srt(original) if original else []), cues


def launch(root, port=7860, open_browser=True):
    import threading
    import webbrowser

    import uvicorn

    app = create_app(root)
    url = f"http://127.0.0.1:{port}"
    print(f"Dịch video: {url}  (thư mục: {Path(root).expanduser().resolve()})")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1)


def openapi_json():
    """`python -m dich_server.api` prints the OpenAPI schema the web client is generated from."""
    print(json.dumps(create_app(Path.home(), ":memory:").openapi(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    openapi_json()
