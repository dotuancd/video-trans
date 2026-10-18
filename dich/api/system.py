# Config, drivers, templates, files, uploads, the live event stream and the admin trace view.
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from dich import drivers, settings
from dich.api.models import (Config, DriverInfo, Entry, Lane, Language, Listing, PathBody, QueueItem, QueueView,
                             TemplateInfo, Uploaded, Worker, WorkerCount)
from dich.core.ids import is_id, new_id
from dich.core.langs import LANGUAGES
from dich.core.template import load_templates
from dich.media import is_media, is_subtitle

POLL = 0.3   # seconds between reads of the events table


def kind_of(path):
    if path.is_dir():
        return "dir"
    if is_subtitle(path.name):
        return "subtitle"
    return "media" if is_media(path.name) else "other"


def prune_uploads(store, uploads, days=7):
    """Removes upload folders older than `days` that no job starts from (dropped, never started)."""
    import shutil
    import time
    if not uploads.is_dir():
        return []
    used = {Path(r["source"]).parent.name for r in store.q("select source from jobs")}
    cutoff = time.time() - days * 86400
    removed = []
    for folder in uploads.iterdir():
        if folder.is_dir() and is_id(folder.name, "upl") and folder.name not in used and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def router(ctx):
    api = APIRouter()
    store = ctx.store

    @api.get("/config")
    def get_config() -> Config:
        return Config(root=str(ctx.root), languages=[Language(code=c, name=v[2]) for c, v in LANGUAGES.items()],
                      default_langs=["vi"], default_drivers={"translate": settings.ENGINE})

    @api.get("/drivers")
    def list_drivers() -> list[DriverInfo]:
        return [DriverInfo(stage=cls.stage, name=cls.name, title=cls.title or cls.name,
                           params_schema=cls.Params.model_json_schema(), available=cls.available(),
                           langs=sorted(cls.langs) if cls.langs else None, per_lang=cls.per_lang(),
                           lane=drivers.lane(cls),
                           reads=sorted({p.rstrip("?") for p in cls.consumes.values()}))
                for cls in drivers.load_all().values() if not cls.__module__.startswith("fake_")]

    @api.get("/templates")
    def list_templates() -> list[TemplateInfo]:
        return [TemplateInfo(name=t.name, title=t.title, description=t.description,
                             stages=[s.model_dump() for s in t.stages]) for t in load_templates().values()]

    # --- queue ---

    @api.get("/queue")
    def get_queue() -> QueueView:
        """What the workers are doing and what waits for them, by lane."""
        items, blocked, waiting_review = store.queue()
        supervisor = ctx.supervisor
        started = supervisor.status() if supervisor else {}
        wanted = supervisor.workers if supervisor else settings.WORKERS
        names = [n for n in settings.WORKERS if n != "interactive"]
        names += sorted({i["lane"] for i in items} - set(names))
        lanes = []
        for name in names:
            mine = [QueueItem(**i) for i in items if i["lane"] == name]
            lanes.append(Lane(
                name=name, configured=wanted.get(name, 0), scalable=supervisor is not None,
                workers=[Worker(pid=pid, alive=alive, retiring=retiring) for pid, alive, retiring in started[name]]
                if name in started else None,
                running=[i for i in mine if i.status == "running"], queued=[i for i in mine if i.status == "queued"]))
        return QueueView(lanes=lanes, blocked=blocked, waiting_review=waiting_review)

    @api.put("/queue/lanes/{lane}/workers")
    def set_workers(lane: str, body: WorkerCount) -> QueueView:
        """How many worker processes run on a lane, from now on and after a restart. Fewer: the extra
        workers finish their task first."""
        if not ctx.supervisor:
            raise HTTPException(409, "Server này không tự chạy worker (dùng `dich worker` để thêm)")
        if lane not in settings.WORKERS or lane == "interactive":
            raise HTTPException(404, f"Không có lane {lane}")
        ctx.supervisor.scale(lane, body.count)
        return get_queue()

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

    # --- live events ---

    @api.get("/events", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
    async def events(request: Request):
        """{"type": "job", "job"} when a job or one of its tasks changed, {"type": "progress", "job_id", "task_id",
        "done", "total"}, {"type": "log", "job_id", "task_id", "event"}, {"type": "deleted", "id"},
        {"type": "project", "id", "deleted"} when a project was created, changed or deleted, {"type": "voice", "id",
        "deleted"} likewise for a library voice."""

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
                    elif row["type"] in ("project", "voice"):
                        out.append({"type": row["type"], "id": data.get("id"), "deleted": bool(data.get("deleted"))})
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
