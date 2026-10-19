# Jobs, tasks, attempts, artifacts and the transcript editor.
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import ValidationError

from dich.api.models import (AddLangs, Attempt, BriefSave, BriefView, EditCue, Event, Job, JobCounts, JobCreate, JobIds, JobPage,
                             JobPatch, JobsCreated, JobsDeleted, JobsDone, JobsMove, JobSort, JobsRerun, LiveAudio, LLMRequest,
                             Rerun, Skipped, StatusGroup, TranscriptSave, TranscriptSaved, TranscriptView, Word)
from dich.core import jobs as job_service
from dich.drivers.translate.llm import read_requests
from dich.formats.transcript import Cue, Transcript, ensure_ids, to_srt, to_vtt
from dich.media import is_media, is_subtitle, sha256
from dich.queue.base import Busy, NeedsConfirm
from dich.runtime.engine import export_task
from dich.storage.live import live_chunk, read_live


def find_media(folder, recursive):
    files = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(f for f in files if f.is_file() and not f.name.startswith(".") and is_media(f.name))


def download_name(source, art):
    """The name an artifact is downloaded as: like its export, after the job's source."""
    from dich.storage.export import export_name, source_stem
    base = Path(art["path"]) if job_service.is_url(source) else Path(source)
    return export_name(art["slot"], source_stem(base), Path(art["path"]).suffix) or Path(art["path"]).name


def attach_artifacts(store, jobs):
    """Each task gets the artifacts of its current attempt, named as they are downloaded."""
    artifacts = store.current_artifacts(job["id"] for job in jobs)
    for job in jobs:
        for task in job["tasks"]:
            task["artifacts"] = artifacts.get(task["id"], [])
            for art in task["artifacts"]:
                art["download_name"] = download_name(job["source"], art)
    return jobs


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def router(ctx):
    api = APIRouter()
    store = ctx.store

    def job_or_404(job_id):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "Không có job này")
        return attach_artifacts(store, [job])[0]

    def task_or_404(task_id):
        task = store.task(task_id)
        if not task:
            raise HTTPException(404, "Không có bước này")
        return task

    # --- jobs ---

    project_query = Query(None, description="A project id, \"none\" (no project), or empty: every job")
    q_query = Query(None, description="Part of the title or the source, without marks and case: \"tap 2\" finds \"Tập 2\"")

    @api.get("/jobs")
    def list_jobs(project: str | None = project_query, status: StatusGroup | None = None, q: str | None = q_query,
                  sort: JobSort = "created_desc", cursor: str | None = None,
                  limit: int = Query(50, ge=1, le=200)) -> JobPage:
        """The jobs that match, a page at a time: pass next_cursor back as cursor for the next page."""
        try:
            jobs, next_cursor = store.jobs_page(project, status, q, sort, cursor, limit)
        except ValueError as error:
            raise HTTPException(400, str(error))
        return JobPage(jobs=attach_artifacts(store, jobs) if jobs else [], next_cursor=next_cursor)

    @api.get("/jobs/counts")
    def count_jobs(project: str | None = project_query, q: str | None = q_query,
                   status: StatusGroup | None = Query(None, description="Only for pausable / resumable")) -> JobCounts:
        """Numbers for the header, the sidebar, the queue and the filter chips."""
        return store.job_counts(project, q, status)

    @api.get("/jobs/ids")
    def job_ids(project: str | None = project_query, status: StatusGroup | None = None,
                q: str | None = q_query) -> list[str]:
        """Every job that matches, newest first: to act on all of them."""
        return store.job_ids(project, status, q)

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        return job_or_404(job_id)

    @api.post("/jobs")
    def create_jobs(body: JobCreate) -> JobsCreated:
        if not body.langs and body.template not in ("transcribe",):
            raise HTTPException(400, "Chọn ít nhất một ngôn ngữ đích")
        if body.project and not store.project(body.project):
            raise HTTPException(404, "Không có project này")
        sources = []
        for raw in body.sources:
            raw = raw.strip()
            if not raw:
                continue
            if job_service.is_url(raw):
                sources.append(raw)
                continue
            path = ctx.existing(raw)
            if path.is_dir():
                found = find_media(path, body.recursive)
                if not found:
                    raise HTTPException(400, f"Không có video/audio nào trong {path}")
                sources += found
            elif is_media(path.name) or is_subtitle(path.name):
                sources.append(path)
            else:
                raise HTTPException(400, f"{path.name} không phải video/audio hay .srt")
        if not sources:
            raise HTTPException(400, "Chưa chọn file, thư mục hoặc URL")
        overrides = {stage: o.model_dump(exclude_none=True) for stage, o in body.overrides.items()}
        created, skipped = [], []
        for source in sources:
            existing = store.jobs_of_source(source)
            if existing:
                skipped.append(f"{Path(str(source)).name}: đã có việc {existing[-1]}")
                continue
            try:
                created.append(job_service.create_job(store, source, body.langs, body.template, overrides,
                                                      export=body.export, use_subtitles=body.use_subtitles,
                                                      project_id=body.project))
            except (KeyError, ValidationError) as error:
                raise HTTPException(400, str(error).strip("'"))
        return JobsCreated(jobs=created, skipped=skipped)

    @api.patch("/jobs/{job_id}")
    def update_job(job_id: str, body: JobPatch) -> Job:
        """Renames the job and/or moves it into another project (or out of any): only the fields sent change."""
        job_or_404(job_id)
        title = (body.title or "").strip()
        if "title" in body.model_fields_set and not title:
            raise HTTPException(400, "Tên không được để trống")
        if body.project_id and not store.project(body.project_id):
            raise HTTPException(404, "Không có project này")
        if title:
            store.set_title(job_id, title)
        if "project_id" in body.model_fields_set:
            store.set_job_project(job_id, body.project_id)
        return job_or_404(job_id)

    @api.post("/jobs/{job_id}/langs")
    def add_langs(job_id: str, body: AddLangs) -> Job:
        job_or_404(job_id)
        job_service.add_langs(store, job_id, body.langs)
        return job_or_404(job_id)

    @api.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> Job:
        job_or_404(job_id)
        store.cancel_job(job_id)
        return job_or_404(job_id)

    @api.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str) -> Job:
        """Past a review gate, and again from stopped or failed steps."""
        job_or_404(job_id)
        store.resume(job_id)
        return job_or_404(job_id)

    @api.post("/jobs/pause")
    def pause_jobs(body: JobIds) -> list[Job]:
        """Pause many jobs: running steps stop, waiting ones stay put. Jobs waiting for review are left alone.
        Returns the jobs that were paused."""
        return jobs_of(store.pause_jobs(body.ids))

    @api.post("/jobs/unpause")
    def unpause_jobs(body: JobIds) -> list[Job]:
        """Continue many paused jobs from their stopped steps; review gates and failed steps are left alone.
        Returns the jobs that continue."""
        return jobs_of(store.unpause_jobs(body.ids))

    def jobs_of(ids):
        jobs = [job for job in map(store.job, ids) if job]
        return attach_artifacts(store, jobs) if jobs else []

    def skip(job, reason):
        return Skipped(id=job["id"], title=job["title"], reason=reason)

    @api.post("/jobs/move")
    def move_jobs(body: JobsMove) -> list[Job]:
        """Moves many jobs into a project (or out of any). Returns the jobs moved."""
        if body.project_id and not store.project(body.project_id):
            raise HTTPException(404, "Không có project này")
        ids = [job["id"] for job in map(store.job, body.ids) if job]
        for job_id in ids:
            store.set_job_project(job_id, body.project_id)
        return jobs_of(ids)

    @api.post("/jobs/delete")
    def delete_jobs(body: JobIds) -> JobsDeleted:
        """Removes many jobs and their workspaces; exported files stay. Running jobs are skipped."""
        deleted, skipped = [], []
        for job in filter(None, map(store.job, body.ids)):
            try:
                store.delete_job(job["id"])
                deleted.append(job["id"])
            except Busy as error:
                skipped.append(skip(job, str(error)))
        return JobsDeleted(deleted=deleted, skipped=skipped)

    @api.post("/jobs/rerun")
    def rerun_jobs(body: JobsRerun) -> JobsDone:
        """Runs a stage again in many jobs (each of its languages), with a new driver or params. A job whose
        step is busy or has no such step is skipped; one that would lose edits by hand waits for confirm."""
        done, skipped, needs_confirm = [], [], []
        for job in filter(None, map(store.job, body.ids)):
            tasks = [t for t in job["tasks"] if t["stage"] == body.stage and t["status"] != "skipped"]
            if not tasks:
                skipped.append(skip(job, "Không có bước này"))
                continue
            later = [t for task in tasks for t in store.descendants(job["id"], task["key"])]
            touched = tasks + (later if body.mode == "cascade" else [])
            if any(t["status"] == "running" for t in touched):   # checked first: no job half rerun
                skipped.append(skip(job, "Bước này hoặc bước sau đang chạy"))
                continue
            if not body.confirm and store.human_conflicts(touched):
                needs_confirm.append(job["id"])
                continue
            try:
                for task in tasks:
                    store.rerun(task["id"], body.mode, body.params, body.driver, confirm=True, fresh=body.fresh)
                done.append(job["id"])
            except Busy as error:
                skipped.append(skip(job, str(error)))
            except (KeyError, ValidationError) as error:
                raise HTTPException(400, str(error).strip("'"))
        return JobsDone(jobs=jobs_of(done), skipped=skipped, needs_confirm=needs_confirm)

    @api.post("/jobs/{job_id}/refresh")
    def refresh_job(job_id: str) -> Job:
        """Run the steps whose input changed (stale) again."""
        job_or_404(job_id)
        store.refresh(job_id)
        return job_or_404(job_id)

    @api.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        """Removes the job and its workspace; exported files stay."""
        job_or_404(job_id)
        try:
            store.delete_job(job_id)
        except Busy as error:
            raise HTTPException(409, str(error))

    # --- tasks ---

    @api.post("/tasks/{task_id}/rerun", responses={409: {"description": "Busy, or edits by hand need confirm"}})
    def rerun_task(task_id: str, body: Rerun) -> Job:
        task = task_or_404(task_id)
        try:
            store.rerun(task_id, body.mode, body.params, body.driver, body.confirm, body.fresh)
        except NeedsConfirm as error:
            raise HTTPException(409, {"message": str(error), "confirm": True, "keys": error.keys})
        except Busy as error:
            raise HTTPException(409, str(error))
        except (KeyError, ValidationError) as error:
            raise HTTPException(400, str(error).strip("'"))
        return job_or_404(task["job_id"])

    @api.get("/tasks/{task_id}/attempts")
    def list_attempts(task_id: str) -> list[Attempt]:
        """Newest first, each with the files it produced that are still kept."""
        task = task_or_404(task_id)
        source = store.job_row(task["job_id"])["source"]
        attempts = store.attempts_of(task_id)
        for attempt in attempts:
            attempt["artifacts"] = [{**art, "download_name": download_name(source, art)}
                                    for art in store.artifacts_of(attempt["id"]) if Path(art["path"]).is_file()]
        return attempts

    @api.post("/tasks/{task_id}/attempts/{attempt_id}/use", responses={409: {"description": "Busy or files gone"}})
    def pick_attempt(task_id: str, attempt_id: str) -> Job:
        """An earlier run becomes the step's result again; the steps after it become stale."""
        task = task_or_404(task_id)
        try:
            store.use_attempt(task_id, attempt_id)
        except KeyError:
            raise HTTPException(404, "Không có lần chạy này")
        except Busy as error:
            raise HTTPException(409, str(error))
        export_task(store, store.task(task_id))
        return job_or_404(task["job_id"])

    @api.get("/attempts/{attempt_id}/events")
    def attempt_events(attempt_id: str) -> list[Event]:
        if not store.attempt(attempt_id):
            raise HTTPException(404, "Không có lần chạy này")
        return store.attempt_events(attempt_id)

    @api.get("/attempts/{attempt_id}/requests")
    def attempt_requests(attempt_id: str) -> list[LLMRequest]:
        """What an LLM translation sent and got back, request by request ([] for other drivers)."""
        attempt = store.attempt(attempt_id)
        if not attempt:
            raise HTTPException(404, "Không có lần chạy này")
        return read_requests(attempt["dir"])

    # --- live audio (a running dub, playable before it is done) ---

    def live_attempt(task_id):
        """The attempt whose live audio is shown: the running one, else the one in use."""
        task = task_or_404(task_id)
        if task["status"] == "running":
            attempt = next(iter(store.attempts_of(task_id)), None)
        else:
            attempt = store.attempt(task["current_attempt"]) if task["current_attempt"] else None
        if not attempt:
            raise HTTPException(404, "Chưa có bản nghe trước")
        return attempt

    @api.get("/tasks/{task_id}/live")
    def get_live(task_id: str) -> LiveAudio:
        attempt = live_attempt(task_id)
        index = read_live(attempt["dir"])
        if not index:
            raise HTTPException(404, "Chưa có bản nghe trước")
        return {**index, "attempt_id": attempt["id"]}

    @api.get("/tasks/{task_id}/live/{n}", response_class=FileResponse)
    def get_live_chunk(task_id: str, n: int, attempt: str | None = None):
        """attempt: the attempt_id of the index read (chunks are cached per attempt)."""
        found = store.attempt(attempt) if attempt else live_attempt(task_id)
        path = live_chunk(found["dir"], n) if found and found["task_id"] == task_id else None
        if not path:
            raise HTTPException(404, "Không có đoạn này")
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})

    # --- artifacts ---

    def artifact_or_404(artifact_id):
        artifact = store.artifact(artifact_id)
        if not artifact or not Path(artifact["path"]).is_file():
            raise HTTPException(404, "File không còn")
        return artifact

    @api.get("/artifacts/{artifact_id}", response_class=FileResponse)
    def get_artifact(artifact_id: str):
        return FileResponse(artifact_or_404(artifact_id)["path"])

    @api.get("/artifacts/{artifact_id}/vtt", response_class=PlainTextResponse)
    def artifact_vtt(artifact_id: str):
        return PlainTextResponse(to_vtt(Transcript.load(artifact_or_404(artifact_id)["path"])), media_type="text/vtt")

    @api.get("/artifacts/{artifact_id}/download", response_class=FileResponse)
    def download_artifact(artifact_id: str, inline: bool = False):
        """The file as an attachment named like the export; transcripts as .srt. inline: shown in the
        browser instead (transcripts as plain text)."""
        artifact = artifact_or_404(artifact_id)
        name = download_name(store.job_row(artifact["job_id"])["source"], artifact)
        if artifact["slot"].startswith("transcript."):
            text = to_srt(Transcript.load(artifact["path"]))
            if inline:
                return PlainTextResponse(text)
            return PlainTextResponse(text, media_type="application/x-subrip",
                                     headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
        if inline:
            return FileResponse(artifact["path"])
        return FileResponse(artifact["path"], filename=name)

    # --- transcript editor ---

    def transcripts(job_id, lang):
        """(task, transcript) of the language edited, and (task, transcript) of the original next to it."""
        job_or_404(job_id)
        slot = "transcript.src" if lang in (None, "", "src") else f"transcript.{lang}"
        task, art = store.slot_artifact(job_id, slot)
        if not art:
            raise HTTPException(404, "Chưa có phụ đề")
        source_task, source = None, None
        if slot != "transcript.src":
            source_task, src_art = store.slot_artifact(job_id, "transcript.src")
            source = Transcript.load(src_art["path"]) if src_art else None
        return task, Transcript.load(art["path"]), source_task, source

    def flat_words(transcript):
        if not transcript:
            return None
        words = [Word(word=w["w"], start=w["s"], end=w["e"]) for c in transcript.cues for w in (c.words or [])]
        return words or None

    def edited(task):
        attempt = store.attempt(task["current_attempt"]) if task["current_attempt"] else None
        return bool(attempt and attempt["origin"] == "human")

    @api.get("/jobs/{job_id}/transcript")
    def get_transcript(job_id: str, lang: str = "src") -> TranscriptView:
        task, transcript, source_task, source = transcripts(job_id, lang)
        by_id = source.by_id() if source else {}
        cues = [EditCue(id=c.id, start=c.start, end=c.end, text=c.text,
                        source=(by_id.get(c.src_id or c.id).text if by_id.get(c.src_id or c.id) else "")
                        if source else None) for c in transcript.cues]
        return TranscriptView(lang=transcript.lang, task_id=task["id"],
                              source_task_id=source_task["id"] if source_task else None, cues=cues,
                              words=flat_words(source or transcript), edited=edited(task))

    def save(task, transcript, slot):
        path = store.job_dir(task["job_id"]) / "edits" / f"{slot}-{time.time_ns()}.jsonl"
        transcript.save(path)
        name = next(n for n, s in task["outputs"].items() if s == slot)
        try:
            store.save_human(task["id"], name, path, sha256(path), path.stat().st_size)
        except Busy as error:
            raise HTTPException(409, str(error))

    def with_words(cues, words):
        """The original's cues with the timed words heard in each, when the text still has as many words."""
        for cue in cues:
            heard = [w for w in words if w.start >= cue.start - 0.001 and w.end <= cue.end + 0.001]
            if heard and len(heard) == len(cue.text.split()):
                cue.words = [{"w": t, "s": w.start, "e": w.end} for t, w in zip(cue.text.split(), heard)]
        return cues

    @api.put("/jobs/{job_id}/transcript")
    def save_transcript(job_id: str, body: TranscriptSave, lang: str = "src") -> TranscriptSaved:
        """Saves the editor's cues as an edit by hand: steps after it become stale. With the original next to
        it (every cue has `source`), both are saved with the same cues (merges, splits, timings)."""
        task, transcript, source_task, source = transcripts(job_id, lang)
        cues = sorted(body.cues, key=lambda c: c.start)
        if not cues or any(c.end <= c.start for c in cues):
            raise HTTPException(400, "Câu phụ đề phải có thời gian kết thúc sau thời gian bắt đầu")
        with_source = source is not None and all(c.source is not None for c in cues)
        ensure_ids(cues)
        is_src = lang in (None, "", "src")
        slot = "transcript.src" if is_src else f"transcript.{lang}"
        new = Transcript(transcript.lang, [Cue(c.id, round(c.start, 3), round(c.end, 3), c.text.strip(),
                                               src_id=c.id if with_source else None) for c in cues])
        if is_src:
            with_words(new.cues, flat_words(transcript) or [])
        before = {(c.start, c.end, c.text) for c in transcript.cues}
        texts = sum((c.start, c.end, c.text) not in before for c in new.cues)
        texts += 0 if texts or len(new.cues) == len(transcript.cues) else 1
        sources, source_changed = 0, False
        if with_source:
            new_source = Transcript(source.lang, with_words(
                [Cue(c.id, round(c.start, 3), round(c.end, 3), (c.source or "").strip()) for c in cues],
                flat_words(source) or []))
            old = {(c.start, c.end, c.text) for c in source.cues}
            sources = sum((c.start, c.end, c.text) not in old for c in new_source.cues)
            sources += 0 if sources or len(new_source.cues) == len(source.cues) else 1
            source_changed = norm(" ".join(c.text for c in source.cues)) != norm(" ".join(c.text for c in new_source.cues))
            if sources:
                status = store.task(task["id"])["status"]
                save(source_task, new_source, "transcript.src")
                if not source_changed and status == "done":   # only cut differently: the translation still fits
                    store._status(store.task(task["id"]), "done", message=None)
                export_task(store, store.task(source_task["id"]))
        if texts:
            save(task, new, slot)
            export_task(store, store.task(task["id"]))
        return TranscriptSaved(texts=texts, sources=sources, source_text_changed=source_changed)

    # --- video brief ---

    def brief_of(job_id):
        job_or_404(job_id)
        task, art = store.slot_artifact(job_id, "brief")
        if not art:
            raise HTTPException(404, "Chưa có bối cảnh video")
        return task, art

    @api.get("/jobs/{job_id}/brief")
    def get_brief(job_id: str) -> BriefView:
        task, art = brief_of(job_id)
        return BriefView(task_id=task["id"], text=Path(art["path"]).read_text(encoding="utf-8"), edited=edited(task))

    @api.put("/jobs/{job_id}/brief")
    def save_brief(job_id: str, body: BriefSave) -> BriefView:
        """Saves the brief as an edit by hand: the translations after it become stale."""
        task, art = brief_of(job_id)
        text = body.text.strip() + "\n"
        if text == Path(art["path"]).read_text(encoding="utf-8"):
            return BriefView(task_id=task["id"], text=text, edited=edited(task))
        path = store.job_dir(job_id) / "edits" / f"brief-{time.time_ns()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:
            store.save_human(task["id"], "brief", path, sha256(path), path.stat().st_size)
        except Busy as error:
            raise HTTPException(409, str(error))
        return BriefView(task_id=task["id"], text=text, edited=True)

    return api


def quote(text):
    from urllib.parse import quote as q
    return q(text)
