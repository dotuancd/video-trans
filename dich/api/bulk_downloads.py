# Bulk downloads ("Gói tải về"): a zip of the results of chosen jobs, made by a bulk_download worker
# (dich.storage.bulk) and kept a while. Only done jobs count; the preview says which are left out.
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from dich.api.models import BulkDownload, BulkDownloadAsk, BulkDownloadPreview, BulkFile, UnfinishedJob
from dich.storage.export import download_name


NOT_READY = {"queued": "Gói chưa tạo xong", "running": "Gói chưa tạo xong", "cancelled": "Gói đã bị huỷ"}


def zip_name(project, now=None):
    """<project>-YYYYMMDD-HHmm.zip (characters a file name can't have replaced), or dich-video-… ."""
    stem = "".join("-" if c in '/\\:*?"<>|' else c for c in project["name"]).strip(" .") if project else ""
    return f"{stem or 'dich-video'}-{time.strftime('%Y%m%d-%H%M', time.localtime(now))}.zip"


def router(ctx):
    api = APIRouter()
    store = ctx.store

    def bulk_or_404(bulk_id):
        bulk = store.bulk_download(bulk_id)
        if not bulk:
            raise HTTPException(404, "Không có gói tải về này")
        return bulk

    def contents(body):
        """(files, unfinished jobs, langs having files, projects of the files' jobs) of what body asks for."""
        slots = (["media.out"] if body.video else []) + (["transcript"] if body.srt else [])
        files, unfinished, langs, projects = [], [], [], set()
        for job_id in dict.fromkeys(body.job_ids):
            job = store.job(job_id)
            if not job:
                continue
            if job["status"] != "done":
                unfinished.append(UnfinishedJob(id=job["id"], title=job["title"], status=job["status"]))
                continue
            for slot in slots:
                for lang in job["langs"]:
                    _task, art = store.slot_artifact(job_id, f"{slot}.{lang}")
                    if not art:
                        continue
                    langs.append(lang)
                    if body.langs is None or lang in body.langs:
                        files.append(BulkFile(artifact_id=art["id"], job_id=job_id, name=download_name(job["source"], art),
                                              slot=art["slot"], lang=lang, size=art["size"]))
                        projects.add(job["project_id"])
        return files, unfinished, list(dict.fromkeys(langs)), projects

    @api.post("/bulk-downloads/preview")
    def preview_bulk_download(body: BulkDownloadAsk) -> BulkDownloadPreview:
        """What a bulk download of these jobs would hold, before asking for it."""
        files, unfinished, langs, _projects = contents(body)
        return BulkDownloadPreview(files=files, size=sum(f.size or 0 for f in files), unfinished=unfinished,
                                   langs=langs, single=files[0] if len(files) == 1 else None)

    @api.post("/bulk-downloads")
    def create_bulk_download(body: BulkDownloadAsk) -> BulkDownload:
        """Queues the zip of the done jobs' files (unfinished ones left out). The same files asked again
        give back the zip already made or being made."""
        files, _unfinished, _langs, projects = contents(body)
        if not files:
            raise HTTPException(400, "Không có file nào để tải")
        project = store.project(projects.pop()) if len(projects) == 1 and None not in projects else None
        # the artifacts alone say what the zip holds (the worker names them, transcripts as .srt)
        return BulkDownload.of(store.create_bulk_download(zip_name(project), [f.artifact_id for f in files]))

    @api.get("/bulk-downloads")
    def list_bulk_downloads() -> list[BulkDownload]:
        """Newest first."""
        return [BulkDownload.of(b) for b in store.bulk_downloads()]

    @api.get("/bulk-downloads/{bulk_id}/download", response_class=FileResponse,
             responses={200: {"content": {"application/zip": {}}}})
    def download_bulk_download(bulk_id: str):
        """The zip, resumable (Range)."""
        bulk = bulk_or_404(bulk_id)
        if bulk["status"] != "ready":
            raise HTTPException(409, NOT_READY[bulk["status"]] if bulk["status"] in NOT_READY else bulk["error"])
        if bulk["expires_at"] < time.time() or not bulk["path"] or not Path(bulk["path"]).is_file():
            raise HTTPException(410, "Gói đã hết hạn, hãy tạo lại")
        return FileResponse(bulk["path"], media_type="application/zip", filename=bulk["name"])

    @api.delete("/bulk-downloads/{bulk_id}", status_code=204)
    def delete_bulk_download(bulk_id: str):
        """Cancels one waiting or being made (its worker stops); deletes any other, with its zip."""
        if bulk_or_404(bulk_id)["status"] in ("queued", "running"):
            store.cancel_bulk_download(bulk_id)
        else:
            store.delete_bulk_download(bulk_id)

    return api
