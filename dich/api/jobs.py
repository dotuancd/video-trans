# Jobs, tasks, attempts, artifacts and the transcript editor.
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import ValidationError

from dich.api.models import (AddLangs, Attempt, EditCue, Event, Job, JobCreate, JobPatch, JobsCreated, Rerun,
                             TranscriptSave, TranscriptSaved, TranscriptView, Word)
from dich.core import jobs as job_service
from dich.formats.transcript import Cue, Transcript, ensure_ids, to_srt, to_vtt
from dich.media import is_media, is_subtitle, sha256
from dich.queue.base import Busy, NeedsConfirm
from dich.runtime.engine import export_task


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
    artifacts = store.current_artifacts(jobs[0]["id"] if len(jobs) == 1 else None)
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

    @api.get("/jobs")
    def list_jobs() -> list[Job]:
        jobs = store.jobs()
        return attach_artifacts(store, jobs) if jobs else []

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
        """Moves the job into another project (or out of any)."""
        job_or_404(job_id)
        if body.project_id and not store.project(body.project_id):
            raise HTTPException(404, "Không có project này")
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
            store.rerun(task_id, body.mode, body.params, body.driver, body.confirm)
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

    return api


def quote(text):
    from urllib.parse import quote as q
    return q(text)
