import time

import pytest
from fastapi.testclient import TestClient

from dich.api.app import create_app
from dich.formats.transcript import Transcript
from dich.runtime.local import run_job

FAKE = {"asr": {"driver": "fake"}, "translate": {"driver": "fake"}, "dub": {"driver": "fake"},
        "mux": {"enabled": False}}


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.mp4").write_bytes(b"video")
    (root / "notes.txt").write_text("x")
    app = create_app(root, data_dir=tmp_path / "data", workers=False)
    with TestClient(app) as client:
        yield client, app.state.ctx


def create(client, ctx, **body):
    response = client.post("/api/jobs", json={"sources": [str(ctx.root / "a.mp4")], "overrides": FAKE, **body})
    assert response.status_code == 200, response.text
    return response.json()["jobs"]


def test_listing_and_root_guard(app):
    client, ctx = app
    listing = client.get("/api/fs").json()
    assert {e["name"]: e["kind"] for e in listing["entries"]} == {"a.mp4": "media", "notes.txt": "other"}
    assert client.get("/api/fs", params={"path": "/etc"}).status_code == 403


def test_drivers_and_templates(app):
    client, _ctx = app
    drivers = {(d["stage"], d["name"]): d for d in client.get("/api/drivers").json()}
    assert ("translate", "claude") in drivers and ("asr", "fake") not in drivers
    assert drivers[("dub", "vieneu")]["langs"] == ["vi"]
    assert "max_speed" in drivers[("dub", "vieneu")]["params_schema"]["properties"]
    names = {t["name"] for t in client.get("/api/templates").json()}
    assert {"default", "subs-only", "dub-review", "transcribe"} <= names


def test_create_run_rerun_and_duplicates(app):
    client, ctx = app
    [job_id] = create(client, ctx, langs=["vi", "ja"])
    run_job(ctx.store, job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    tasks = {t["key"]: t for t in job["tasks"]}
    assert tasks["dub@ja"]["status"] == "skipped"
    assert tasks["translate@vi"]["artifacts"][0]["slot"] == "transcript.vi"
    assert (ctx.root / "a.vi.srt").exists()
    assert client.post("/api/jobs", json={"sources": [str(ctx.root / "a.mp4")]}).json()["skipped"]

    rerun = client.post(f"/api/tasks/{tasks['translate@vi']['id']}/rerun", json={"mode": "only"}).json()
    assert {t["key"]: t["status"] for t in rerun["tasks"]}["dub@vi"] == "stale"
    art = tasks["translate@vi"]["artifacts"][0]["id"]
    assert client.get(f"/api/artifacts/{art}/vtt").text.startswith("WEBVTT")
    download = client.get(f"/api/artifacts/{art}/download")
    assert "a.vi.srt" in download.headers["content-disposition"]
    inline = client.get(f"/api/artifacts/{art}/download", params={"inline": True})
    assert "content-disposition" not in inline.headers and "-->" in inline.text

    run_job(ctx.store, job_id)
    translate = tasks["translate@vi"]["id"]
    attempts = client.get(f"/api/tasks/{translate}/attempts").json()
    assert len(attempts) == 2 and attempts[1]["artifacts"][0]["download_name"] == "a.vi.srt"
    used = client.post(f"/api/tasks/{translate}/attempts/{attempts[1]['id']}/use").json()
    assert {t["key"]: t for t in used["tasks"]}["translate@vi"]["current_attempt"] == attempts[1]["id"]
    assert client.post(f"/api/tasks/{translate}/attempts/att_nope/use").status_code == 404


def test_bad_params_and_unknown_driver(app):
    client, ctx = app
    bad = client.post("/api/jobs", json={"sources": [str(ctx.root / "a.mp4")],
                                         "overrides": {**FAKE, "dub": {"driver": "fake"}, "asr": {"driver": "nope"}}})
    assert bad.status_code == 400
    assert client.post("/api/jobs", json={"sources": ["/etc/passwd"]}).status_code == 403


def test_transcript_edit_marks_stale_and_needs_confirm(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    view = client.get(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}).json()
    assert view["cues"][0] == {"id": "c_0001", "start": 0.0, "end": 1.0, "text": "vi:hello.", "source": "hello."}
    cues = view["cues"]
    cues[0]["text"] = "Xin chào."
    saved = client.put(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}, json={"cues": cues}).json()
    assert saved == {"texts": 1, "sources": 0, "source_text_changed": False}
    assert "Xin chào." in (ctx.root / "a.vi.srt").read_text(encoding="utf-8")
    job = client.get(f"/api/jobs/{job_id}").json()
    tasks = {t["key"]: t for t in job["tasks"]}
    assert tasks["dub@vi"]["status"] == "stale" and tasks["translate@vi"]["status"] == "done"
    assert client.get(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}).json()["edited"]

    conflict = client.post(f"/api/tasks/{tasks['translate@vi']['id']}/rerun", json={"mode": "only"})
    assert conflict.status_code == 409 and conflict.json()["detail"]["confirm"]
    assert client.post(f"/api/jobs/{job_id}/refresh").json()["status"] == "queued"


def test_merge_in_editor_keeps_translation_fresh(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    cues = client.get(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}).json()["cues"]
    merged = [{**cues[0], "end": cues[1]["end"], "text": cues[0]["text"] + " " + cues[1]["text"],
               "source": cues[0]["source"] + " " + cues[1]["source"]}]
    saved = client.put(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}, json={"cues": merged}).json()
    assert saved["sources"] == 1 and not saved["source_text_changed"]
    tasks = {t["key"]: t["status"] for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}
    assert tasks["translate@vi"] == "done" and tasks["dub@vi"] == "stale"
    src = client.get(f"/api/jobs/{job_id}/transcript").json()
    assert [c["text"] for c in src["cues"]] == ["hello. world."]


def test_add_language_cancel_resume_delete(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    job = client.post(f"/api/jobs/{job_id}/langs", json={"langs": ["fr"]}).json()
    assert job["langs"] == ["vi", "fr"]
    assert {t["key"]: t["status"] for t in job["tasks"]}["translate@fr"] == "queued"
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job_id}/resume").json()["status"] == "queued"
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_pause_unpause_many(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    [paused] = client.post("/api/jobs/pause", json={"ids": [job_id, "nope"]}).json()
    assert paused["id"] == job_id and paused["status"] == "cancelled"
    assert client.post("/api/jobs/pause", json={"ids": [job_id]}).json() == []
    [resumed] = client.post("/api/jobs/unpause", json={"ids": [job_id]}).json()
    assert resumed["status"] in ("queued", "blocked")


def test_move_rerun_delete_many(app):
    client, ctx = app
    [job_id] = create(client, ctx, langs=["vi", "ja"])
    run_job(ctx.store, job_id)
    project = client.post("/api/projects", json={"name": "P"}).json()["id"]
    [moved] = client.post("/api/jobs/move", json={"ids": [job_id, "nope"], "project_id": project}).json()
    assert moved["project_id"] == project
    assert client.post("/api/jobs/move", json={"ids": [job_id], "project_id": "nope"}).status_code == 404

    done = client.post("/api/jobs/rerun", json={"ids": [job_id], "stage": "translate", "mode": "only"}).json()
    statuses = {t["key"]: t["status"] for t in done["jobs"][0]["tasks"]}
    assert statuses["translate@vi"] == statuses["translate@ja"] == "queued" and statuses["dub@vi"] == "stale"
    assert client.post("/api/jobs/rerun", json={"ids": [job_id], "stage": "nope"}).json()["skipped"][0]["id"] == job_id

    run_job(ctx.store, job_id)
    transcript = client.get(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}).json()
    transcript["cues"][0]["text"] = "sửa tay"
    assert client.put(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}, json=transcript).status_code == 200
    held = client.post("/api/jobs/rerun", json={"ids": [job_id], "stage": "translate"}).json()
    assert held["needs_confirm"] == [job_id] and held["jobs"] == []
    assert client.post("/api/jobs/rerun", json={"ids": [job_id], "stage": "translate", "confirm": True}).json()["jobs"]

    assert client.post("/api/jobs/delete", json={"ids": [job_id, "nope"]}).json() == {"deleted": [job_id], "skipped": []}
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_system_error_has_trace(app):
    client, ctx = app
    [job_id] = create(client, ctx, overrides={**FAKE, "asr": {"driver": "fake", "params": {"fail": "system"}}})
    run_job(ctx.store, job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    asr = next(t for t in job["tasks"] if t["key"] == "asr")
    code = asr["message"].split("mã ")[1].rstrip(")")
    assert "ValueError: boom" in client.get(f"/api/admin/traces/{code}").text
    assert "ValueError: boom" in client.get(f"/api/admin/traces/{job['trace_id']}").text
    [attempt] = client.get(f"/api/tasks/{asr['id']}/attempts").json()
    events = client.get(f"/api/attempts/{attempt['id']}/events").json()
    assert events[-1]["message"] == asr["message"]


def test_upload_and_url_job(app):
    client, ctx = app
    up = client.put("/api/uploads", params={"name": "b.mp4"}, content=b"video").json()
    assert up["kind"] == "media"
    [job_id] = client.post("/api/jobs", json={"sources": [up["path"]], "overrides": FAKE}).json()["jobs"]
    assert client.get(f"/api/jobs/{job_id}").json()["export_dir"] == str(ctx.uploads / up["id"])
    [url_job] = client.post("/api/jobs", json={"sources": ["https://example.com/v"], "overrides": FAKE}).json()["jobs"]
    job = client.get(f"/api/jobs/{url_job}").json()
    assert job["tasks"][0]["driver"] == "url" and job["export_dir"].endswith("export")


def test_preview_goes_through_a_worker_call(app, monkeypatch):
    import threading
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    wav = ctx.data_dir / "p.wav"
    wav.write_bytes(b"RIFF")

    def answer():
        for _ in range(100):
            call = ctx.store.claim_call()
            if call:
                assert call["args"]["cues"][0][2] == "vi:hello."
                ctx.store.answer_call(call["id"], {"path": str(wav), "text": "vi:hello."})
                return
            time.sleep(0.02)

    threading.Thread(target=answer).start()
    response = client.post("/api/voices/preview", json={"job_id": job_id})
    assert response.status_code == 200 and response.headers["x-preview-text"] == "vi%3Ahello."


def test_presets(app):
    client, ctx = app
    assert client.get("/api/presets").json() == []
    body = {"export": False, "stages": {"asr": {"params": {"needle": {"lang": "ja"}}}, "refine": {"enabled": False}}}
    saved = client.put("/api/presets/Anime JP", json=body).json()
    assert saved["name"] == "Anime JP" and saved["stages"]["asr"]["params"]["needle"]["lang"] == "ja"
    client.put("/api/presets/Anime JP", json={"export": True})
    [preset] = client.get("/api/presets").json()
    assert preset["export"] is True and preset["stages"] == {}
    assert (ctx.data_dir / "presets.json").is_file()
    assert client.put("/api/presets/%20", json={}).status_code == 400
    assert client.delete("/api/presets/Anime JP").status_code == 204
    assert client.delete("/api/presets/Anime JP").status_code == 404


def test_projects(app):
    client, ctx = app
    assert client.post("/api/projects", json={"name": "  "}).status_code == 400
    assert client.post("/api/projects", json={"name": "x", "export_dir": "/etc"}).status_code == 403
    project = client.post("/api/projects", json={"name": "Khoá học", "langs": ["vi", "ja"],
                                                 "options": {"export": True, "stages": {"asr": {"driver": "fake"}}},
                                                 "export_dir": "out"}).json()
    assert project["id"].startswith("prj_") and project["export_dir"] == str(ctx.root / "out")
    assert project["options"]["stages"]["asr"]["driver"] == "fake" and project["jobs"] == 0

    assert client.post("/api/jobs", json={"sources": [str(ctx.root / "a.mp4")], "project": "prj_nope"}).status_code == 404
    [job_id] = create(client, ctx, project=project["id"])
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["project_id"] == project["id"] and job["export_dir"] == str(ctx.root / "out")
    run_job(ctx.store, job_id)
    assert (ctx.root / "out" / "a.vi.srt").exists() and not (ctx.root / "a.vi.srt").exists()
    assert client.get("/api/projects").json()[0]["jobs"] == 1

    saved = client.put(f"/api/projects/{project['id']}", json={**project, "name": "Đổi tên", "color": "#e0456b"}).json()
    assert saved["name"] == "Đổi tên" and saved["langs"] == ["vi", "ja"]
    assert client.put("/api/projects/prj_nope", json={"name": "x"}).status_code == 404

    assert client.patch(f"/api/jobs/{job_id}", json={"project_id": "prj_nope"}).status_code == 404
    assert client.patch(f"/api/jobs/{job_id}", json={"project_id": None}).json()["project_id"] is None
    assert client.patch(f"/api/jobs/{job_id}", json={"project_id": project["id"]}).json()["project_id"] == project["id"]

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/jobs/{job_id}").json()["project_id"] is None


def test_prune_uploads(app):
    import os
    from dich.api.system import prune_uploads
    from dich.core.ids import new_id
    client, ctx = app
    old, used, fresh = (ctx.uploads / new_id("upl") for _ in range(3))
    for folder in (old, used, fresh):
        folder.mkdir(parents=True)
        (folder / "a.mp4").write_bytes(b"video")
    for folder in (old, used):
        os.utime(folder, (0, 0))
    create(client, ctx, sources=[str(used / "a.mp4")])
    assert prune_uploads(ctx.store, ctx.uploads) == [old.name]
    assert not old.exists() and used.exists() and fresh.exists()


def test_queue(app):
    client, ctx = app
    [job_id] = create(client, ctx, langs=["vi"])
    view = client.get("/api/queue").json()
    lanes = {lane["name"]: lane for lane in view["lanes"]}
    assert {"cpu", "net", "gpu"} <= set(lanes) and "interactive" not in lanes
    queued = [item for lane in view["lanes"] for item in lane["queued"]]
    assert queued and all(item["job_id"] == job_id and item["title"] for item in queued)
    assert view["blocked"] > 0 and lanes["cpu"]["workers"] is None

    task = ctx.store.claim([queued[0]["lane"]])
    ctx.store.start_attempt(task, pid=4242)
    lane = next(lane for lane in client.get("/api/queue").json()["lanes"] if lane["name"] == task["lane"])
    assert [(item["id"], item["pid"]) for item in lane["running"]] == [(task["id"], 4242)]
    assert lane["running"][0]["started_at"]


def test_set_workers(app):
    client, ctx = app
    assert client.put("/api/queue/lanes/cpu/workers", json={"count": 2}).status_code == 409   # no supervisor here

    class Fake:
        workers = {"cpu": 1, "net": 3, "gpu": 0}

        def scale(self, lane, count):
            self.workers[lane] = count

        def status(self):
            return {lane: [(1, True, False)] * n for lane, n in self.workers.items()}

    ctx.supervisor = Fake()
    try:
        view = client.put("/api/queue/lanes/gpu/workers", json={"count": 2}).json()
        gpu = next(lane for lane in view["lanes"] if lane["name"] == "gpu")
        assert gpu["configured"] == 2 and len(gpu["workers"]) == 2 and gpu["scalable"]
        assert client.put("/api/queue/lanes/nope/workers", json={"count": 1}).status_code == 404
        assert client.put("/api/queue/lanes/cpu/workers", json={"count": 99}).status_code == 422
    finally:
        ctx.supervisor = None


def test_live_dub_chunks(app):
    import numpy as np

    from dich.storage.live import LIVE_DIR, LiveAudio

    client, ctx = app
    [job_id] = create(client, ctx, langs=["vi"])
    run_job(ctx.store, job_id)
    dub = {t["key"]: t for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}["dub@vi"]
    assert client.get(f"/api/tasks/{dub['id']}/live").status_code == 404

    attempt = ctx.store.attempt(dub["current_attempt"])
    live = LiveAudio(f"{attempt['dir']}/{LIVE_DIR}", 8000, duration=3.0)
    live.add(0.0, np.zeros(8000, dtype=np.float32))
    index = client.get(f"/api/tasks/{dub['id']}/live").json()
    assert index["chunks"] == [{"n": 0, "start": 0.0, "end": 1.0}] and index["ready_until"] == 1.0
    assert index["attempt_id"] == attempt["id"] and not index["finished"]
    chunk = client.get(f"/api/tasks/{dub['id']}/live/0", params={"attempt": attempt["id"]})
    assert chunk.status_code == 200 and chunk.content[:4] == b"RIFF"
    assert client.get(f"/api/tasks/{dub['id']}/live/1").status_code == 404
    assert client.get(f"/api/tasks/{dub['id']}/live/0", params={"attempt": "att_nope"}).status_code == 404
