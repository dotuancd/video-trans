import json
import re
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


def test_replan_turns_a_stage_on_without_running_done_tasks_again(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    asr = ctx.store.task_by_key(job_id, "asr")
    attempts = len(ctx.store.attempts_of(asr["id"]))
    job = client.post(f"/api/jobs/{job_id}/replan", json={"overrides": {"mux": {"enabled": True, "driver": "fake"}}}).json()
    statuses = {t["key"]: t["status"] for t in job["tasks"]}
    assert statuses["mux@vi"] == "queued" and statuses["asr"] == "done" and statuses["translate@vi"] == "done"
    assert job["langs"] == ["vi"]
    overrides = json.loads(ctx.store.job_row(job_id)["overrides"])
    assert overrides["mux"] == {"enabled": True, "driver": "fake"} and overrides["asr"] == {"driver": "fake"}
    run_job(ctx.store, job_id)
    assert {t["key"]: t["status"] for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}["mux@vi"] == "done"
    assert len(ctx.store.attempts_of(asr["id"])) == attempts
    bad = client.post(f"/api/jobs/{job_id}/replan", json={"overrides": {"dub": {"driver": "nope"}}})
    assert bad.status_code == 400


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
    assert client.get(f"/api/attempts/{attempt['id']}/requests").json() == []
    assert client.get("/api/attempts/nope/requests").status_code == 404


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
    renamed = client.patch(f"/api/jobs/{job_id}", json={"title": "  Bài giảng 3 "}).json()
    assert renamed["title"] == "Bài giảng 3" and renamed["project_id"] == project["id"]
    assert client.patch(f"/api/jobs/{job_id}", json={"project_id": None}).json()["title"] == "Bài giảng 3"
    assert client.patch(f"/api/jobs/{job_id}", json={"title": "  "}).status_code == 400
    assert client.patch(f"/api/jobs/{job_id}", json={"title": "x" * 201}).status_code == 422
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


def test_edit_brief_makes_the_translation_stale(app, monkeypatch):
    import fake_drivers
    client, ctx = app
    monkeypatch.setattr(fake_drivers.FakeTranslate, "consumes", {"transcript": "transcript.src", "brief": "brief?"})
    [job_id] = create(client, ctx, langs=["vi"],
                      overrides={**FAKE, "brief": {"params": {"summary": "Video về nấu ăn"}}})
    run_job(ctx.store, job_id)
    brief = client.get(f"/api/jobs/{job_id}/brief").json()
    assert "Video về nấu ăn" in brief["text"] and not brief["edited"]
    saved = client.put(f"/api/jobs/{job_id}/brief", json={"text": "Video về làm bánh"}).json()
    assert saved["edited"] and saved["text"] == "Video về làm bánh\n"
    tasks = {t["key"]: t for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}
    assert tasks["translate@vi"]["status"] == "stale"
    assert client.get(f"/api/jobs/{job_id}/brief").json()["edited"]


def test_transcript_edit_keeps_the_brief(app, monkeypatch):
    import fake_drivers
    client, ctx = app
    monkeypatch.setattr(fake_drivers.FakeTranslate, "consumes", {"transcript": "transcript.src", "brief": "brief?"})
    [job_id] = create(client, ctx, langs=["vi"],
                      overrides={**FAKE, "brief": {"params": {"summary": "Video về nấu ăn"}}})
    run_job(ctx.store, job_id)
    cues = client.get(f"/api/jobs/{job_id}/transcript").json()["cues"]
    cues[0]["text"] = "hi."
    client.put(f"/api/jobs/{job_id}/transcript", json={"cues": cues})
    tasks = {t["key"]: t["status"] for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}
    assert tasks["brief"] == "done" and tasks["translate@vi"] == "stale"


def test_delete_cue_in_editor(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    cues = client.get(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}).json()["cues"]
    saved = client.put(f"/api/jobs/{job_id}/transcript", params={"lang": "vi"}, json={"cues": cues[1:]}).json()
    assert saved["texts"] == 1 and saved["sources"] == 1 and saved["source_text_changed"]
    assert [c["id"] for c in client.get(f"/api/jobs/{job_id}/transcript").json()["cues"]] == [c["id"] for c in cues[1:]]
    tasks = {t["key"]: t["status"] for t in client.get(f"/api/jobs/{job_id}").json()["tasks"]}
    assert tasks["translate@vi"] == "done" and tasks["dub@vi"] == "stale"


def test_voice_library(app):
    import numpy as np
    import soundfile as sf
    client, ctx = app
    rate = 16000
    t = np.arange(rate * 20) / rate
    audio = np.where((t > 5) & (t < 11), 0.3 * np.sin(2 * np.pi * 440 * t), 0.0).astype(np.float32)
    sf.write(ctx.root / "talk.wav", audio, rate)

    analysis = client.post("/api/voices/analyze", json={"path": str(ctx.root / "talk.wav")}).json()
    assert analysis["duration"] == pytest.approx(20, abs=0.1)
    best = analysis["candidates"][0]
    assert best["start"] == pytest.approx(5, abs=0.1) and best["end"] == pytest.approx(11, abs=0.1)
    assert client.get("/api/voices/segment", params={"source": analysis["source"], "start": 5, "end": 9}).status_code == 200
    assert client.post("/api/voices/analyze", json={}).status_code == 400

    project = client.post("/api/projects", json={"name": "P"}).json()["id"]
    body = {"name": "Thầy giáo", "project_id": project, "source": analysis["source"], "start": 5, "end": 11}
    assert client.post("/api/voices", json={**body, "end": 20}).status_code == 400
    assert client.post("/api/voices", json={**body, "project_id": "nope"}).status_code == 404
    voice = client.post("/api/voices", json=body).json()
    assert voice["id"].startswith("voc_") and voice["project_id"] == project and voice["origin"] == "talk.wav"
    shared = client.post("/api/voices", json={**body, "name": "Chung", "project_id": None}).json()
    other = client.post("/api/projects", json={"name": "Q"}).json()["id"]
    assert [v["name"] for v in client.get("/api/voices", params={"project": project}).json()] == ["Thầy giáo", "Chung"]
    assert [v["name"] for v in client.get("/api/voices", params={"project": other}).json()] == ["Chung"]
    assert client.get(f"/api/voices/{voice['id']}/audio").status_code == 200

    renamed = client.patch(f"/api/voices/{voice['id']}", json={"name": "Cô giáo", "project_id": None}).json()
    assert renamed["name"] == "Cô giáo" and renamed["project_id"] is None

    options = {"stages": {"dub": {"params": {"vieneu": {"ref_audio": shared["path"]}}}}}
    client.put(f"/api/projects/{project}", json={"name": "P", "options": options})
    assert client.delete(f"/api/voices/{shared['id']}").status_code == 409
    assert client.delete(f"/api/voices/{voice['id']}").status_code == 204
    assert client.get(f"/api/voices/{voice['id']}/audio").status_code == 404
    assert not (ctx.voices / f"{voice['id']}.wav").exists()


def test_voice_library_survives_its_project(app):
    client, ctx = app
    project = client.post("/api/projects", json={"name": "P"}).json()["id"]
    path = ctx.voices / "x.wav"
    ctx.store.create_voice("voc_x", "X", path, project)
    client.delete(f"/api/projects/{project}")
    assert ctx.store.voice("voc_x")["project_id"] is None


def make_jobs(client, ctx, names, **body):
    """One job per file name (created under the root), in that order."""
    ids = []
    for name in names:
        (ctx.root / name).write_bytes(b"video")
        ids += create(client, ctx, sources=[str(ctx.root / name)], **body)
    return ids


def listed(client, **params):
    """Every job of the list, page by page."""
    jobs, cursor = [], None
    while True:
        page = client.get("/api/jobs", params={**params, **({"cursor": cursor} if cursor else {})})
        assert page.status_code == 200, page.text
        jobs += page.json()["jobs"]
        cursor = page.json()["next_cursor"]
        if not cursor:
            return [j["id"] for j in jobs]


def test_list_filters_by_status_group_and_counts_match(app):
    client, ctx = app
    project = client.post("/api/projects", json={"name": "P"}).json()["id"]
    done, failed, stopped, queued, stale = make_jobs(client, ctx, ["d.mp4", "f.mp4", "s.mp4", "q.mp4", "st.mp4"])
    run_job(ctx.store, done)
    ctx.store.x("update tasks set params = json_set(params, '$.fail', 'user') where job_id = ? and stage = 'asr'", failed)
    run_job(ctx.store, failed)
    client.post(f"/api/jobs/{stopped}/cancel")
    run_job(ctx.store, stale)
    cues = client.get(f"/api/jobs/{stale}/transcript").json()["cues"]
    cues[0]["text"] = "sửa"
    client.put(f"/api/jobs/{stale}/transcript", json={"cues": cues})
    client.post("/api/jobs/move", json={"ids": [done, failed], "project_id": project})
    statuses = {j["id"]: j["status"] for j in client.get("/api/jobs").json()["jobs"]}
    assert statuses == {done: "done", failed: "failed", stopped: "cancelled", queued: "queued", stale: "stale"}

    groups = {"attention": {failed, stale}, "active": {queued}, "stopped": {stopped}, "done": {done}}
    for group, ids in groups.items():
        assert set(listed(client, status=group, limit=1)) == ids
        assert set(client.get("/api/jobs/ids", params={"status": group}).json()) == ids
    assert set(listed(client, project=project)) == {done, failed}
    assert set(listed(client, project="none", status="attention")) == {stale}
    assert client.get("/api/jobs", params={"status": "nope"}).status_code == 422

    counts = client.get("/api/jobs/counts").json()
    assert counts["status"] == {g: len(ids) for g, ids in groups.items()} and counts["total"] == counts["all"] == 5
    assert counts["projects"] == {project: 2, "none": 3} and counts["active"] == 1
    assert counts["queue"] == len([t for t in ctx.store.tasks_of(queued) if t["status"] == "queued"])
    # the failed job still has steps waiting after the failed one
    assert counts["pausable"] == 2 and counts["resumable"] == 1 and not counts["has_cost"]
    mine = client.get("/api/jobs/counts", params={"project": project}).json()
    assert mine["total"] == 2 and mine["status"] == {"attention": 1, "active": 0, "stopped": 0, "done": 1}
    assert mine["all"] == 5   # the sidebar's numbers are of every job
    assert client.get("/api/jobs/counts", params={"status": "stopped"}).json()["pausable"] == 0


def test_search_without_marks_on_title_and_source(app):
    client, ctx = app
    tap2, tap10, other = make_jobs(client, ctx, ["Tập 2.mp4", "Tập 10.mp4", "Đà Lạt.mp4"])
    assert set(listed(client, q="tap")) == {tap2, tap10}
    assert listed(client, q="  TAP   2") == [tap2]
    assert listed(client, q="da lat") == [other]
    client.patch(f"/api/jobs/{other}", json={"title": "Phố núi"})
    assert listed(client, q="đà lạt") == [other]   # by its source, once renamed
    assert listed(client, q="pho nui") == [other]
    run_job(ctx.store, tap2)
    assert listed(client, q="tap", status="done") == [tap2]
    assert client.get("/api/jobs/counts", params={"q": "tap"}).json()["status"]["done"] == 1
    assert client.get("/api/jobs/ids", params={"q": "tap"}).json() == [tap10, tap2]


def test_sorts_and_their_pages(app):
    client, ctx = app
    ids = make_jobs(client, ctx, ["Tập 10.mp4", "anh.mp4", "Tập 2.mp4", "Ánh.mp4", "Bé.mp4"])
    t10, anh, t2, anh2, be = ids
    ctx.store.x("insert into events (job_id, ts, level, type, data) values (?, 0, 'info', 'log', ?)", t2,
                '{"cost_usd": 0.5}')
    ctx.store.x("insert into events (job_id, ts, level, type, data) values (?, 0, 'info', 'log', ?)", be,
                '{"cost_usd": 0.1}')
    assert listed(client) == ids[::-1]
    assert listed(client, sort="created_asc", limit=2) == ids
    by_title = listed(client, sort="title", limit=2)
    assert by_title[-3:] == [be, t2, t10] and set(by_title[:2]) == {anh, anh2}
    cost = listed(client, sort="cost_desc", limit=2)
    assert cost[:2] == [t2, be] and set(cost[2:]) == {t10, anh, anh2}
    assert client.get("/api/jobs/counts").json()["has_cost"]
    assert client.get("/api/jobs", params={"sort": "nope"}).status_code == 422


def test_pages_neither_repeat_nor_miss_when_jobs_are_added(app):
    client, ctx = app
    ids = make_jobs(client, ctx, [f"v{i}.mp4" for i in range(5)])
    first = client.get("/api/jobs", params={"limit": 2}).json()
    assert [j["id"] for j in first["jobs"]] == ids[:2:-1][:2] and first["next_cursor"]
    make_jobs(client, ctx, ["new.mp4"])
    rest = listed(client, cursor=first["next_cursor"], limit=2)
    assert [j["id"] for j in first["jobs"]] + rest == ids[::-1]
    assert client.get("/api/jobs", params={"limit": 5}).json()["next_cursor"]
    assert client.get("/api/jobs", params={"cursor": "garbage"}).status_code == 400
    other_sort = client.get("/api/jobs", params={"limit": 2, "sort": "title"}).json()["next_cursor"]
    assert client.get("/api/jobs", params={"cursor": other_sort}).status_code == 400
    assert client.get("/api/jobs", params={"limit": 1000}).status_code == 422


def test_updated_at_follows_changes(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["updated_at"] == job["created_at"]

    def bumped(before):
        now = client.get(f"/api/jobs/{job_id}").json()["updated_at"]
        assert now > before
        return now

    run_job(ctx.store, job_id)
    stamp = bumped(job["updated_at"])
    client.patch(f"/api/jobs/{job_id}", json={"title": "Đổi tên"})
    stamp = bumped(stamp)
    cues = client.get(f"/api/jobs/{job_id}/transcript").json()["cues"]
    cues[0]["text"] = "sửa"
    client.put(f"/api/jobs/{job_id}/transcript", json={"cues": cues})
    stamp = bumped(stamp)
    [other] = make_jobs(client, ctx, ["b.mp4"])
    assert listed(client, sort="updated_desc") == [other, job_id]
    client.post(f"/api/jobs/{job_id}/langs", json={"langs": ["fr"]})
    bumped(stamp)
    assert listed(client, sort="updated_desc", limit=1) == [job_id, other]


def test_bulk_download_preview(app):
    client, ctx = app
    (ctx.root / "b.mp4").write_bytes(b"video b")
    muxed = {**FAKE, "mux": {"driver": "fake"}}
    [done] = create(client, ctx, langs=["vi", "ja"], overrides=muxed)
    [unfinished] = create(client, ctx, sources=[str(ctx.root / "b.mp4")], overrides=muxed)
    run_job(ctx.store, done)

    def preview(**body):
        response = client.post("/api/bulk-downloads/preview", json={"job_ids": [done, unfinished, "job_nope"], **body})
        assert response.status_code == 200, response.text
        return response.json()

    videos = preview()                                     # default: the dubbed videos, every language
    assert [f["name"] for f in videos["files"]] == ["a.dub.vi.mp4"]   # dub@ja skipped: no ja video
    assert videos["single"]["name"] == "a.dub.vi.mp4" and videos["size"] == videos["files"][0]["size"] > 0
    assert [(j["id"], j["status"]) for j in videos["unfinished"]] == [(unfinished, "queued")]
    assert videos["langs"] == ["vi"]

    both = preview(srt=True)
    assert [f["name"] for f in both["files"]] == ["a.dub.vi.mp4", "a.srt", "a.vi.srt", "a.ja.srt"]   # + the original
    assert both["single"] is None and both["langs"] == ["vi", "ja"]
    assert both["size"] == sum(f["size"] for f in both["files"])
    assert [f["name"] for f in preview(srt=True, langs=["ja"])["files"]] == ["a.srt", "a.ja.srt"]
    assert preview(video=False, srt=False)["files"] == []


def test_bulk_download_create_download_delete(app):
    import zipfile
    from io import BytesIO

    from dich.storage import bulk as bulk_zip
    client, ctx = app
    project = client.post("/api/projects", json={"name": "Khoá học/2"}).json()
    [job_id] = create(client, ctx, overrides={**FAKE, "mux": {"driver": "fake"}}, project=project["id"])
    assert client.post("/api/bulk-downloads", json={"job_ids": [job_id]}).status_code == 400   # nothing done yet
    run_job(ctx.store, job_id)

    made = client.post("/api/bulk-downloads", json={"job_ids": [job_id], "srt": True}).json()
    assert made["status"] == "queued" and made["files"] == 3
    assert re.fullmatch(r"Khoá học-2-\d{8}-\d{4}\.zip", made["name"])
    assert client.get("/api/bulk-downloads").json()[0]["id"] == made["id"]
    assert client.get(f"/api/bulk-downloads/{made['id']}/download").status_code == 409   # not made yet

    bulk_zip.run(ctx.store, ctx.store.claim_bulk_download())
    [ready] = client.get("/api/bulk-downloads").json()
    assert ready["status"] == "ready" and ready["size"] and ready["expires_at"]
    whole = client.get(f"/api/bulk-downloads/{made['id']}/download")
    assert whole.status_code == 200 and int(whole.headers["content-length"]) == ready["size"]
    assert "Kho%C3%A1%20h%E1%BB%8Dc-2-" in whole.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(whole.content)) as zf:
        assert sorted(zf.namelist()) == ["a.dub.vi.mp4", "a.srt", "a.vi.srt"]
    part = client.get(f"/api/bulk-downloads/{made['id']}/download", headers={"Range": "bytes=4-9"})
    assert part.status_code == 206 and part.content == whole.content[4:10]

    again = client.post("/api/bulk-downloads", json={"job_ids": [job_id], "srt": True}).json()
    assert again["id"] == made["id"]                          # the same files: the zip already made

    ctx.store.x("update bulk_downloads set expires_at = ? where id = ?", time.time() - 1, made["id"])
    assert client.get(f"/api/bulk-downloads/{made['id']}/download").status_code == 410

    assert client.delete(f"/api/bulk-downloads/{made['id']}").status_code == 204
    assert client.get("/api/bulk-downloads").json() == []
    assert client.delete(f"/api/bulk-downloads/{made['id']}").status_code == 404
    assert client.get(f"/api/bulk-downloads/{made['id']}/download").status_code == 404


def test_bulk_download_delete_cancels_first(app):
    client, ctx = app
    [job_id] = create(client, ctx)
    run_job(ctx.store, job_id)
    ask = {"job_ids": [job_id], "video": False, "srt": True}
    queued = client.post("/api/bulk-downloads", json=ask).json()
    assert queued["files"] == 2
    assert client.delete(f"/api/bulk-downloads/{queued['id']}").status_code == 204     # waiting: cancelled at once
    [cancelled] = client.get("/api/bulk-downloads").json()
    assert cancelled["status"] == "cancelled"
    assert client.get(f"/api/bulk-downloads/{queued['id']}/download").json()["detail"] == "Gói đã bị huỷ"

    running = client.post("/api/bulk-downloads", json=ask).json()
    assert running["pid"] is None
    ctx.store.claim_bulk_download(pid=4242)
    assert client.get("/api/bulk-downloads").json()[0]["pid"] == 4242                # the worker making it
    assert client.delete(f"/api/bulk-downloads/{running['id']}").status_code == 204    # being made: its worker stops
    assert ctx.store.bulk_heartbeat(running["id"]) is True
    assert client.delete(f"/api/bulk-downloads/{queued['id']}").status_code == 204     # stopped: deleted
    assert [b["id"] for b in client.get("/api/bulk-downloads").json()] == [running["id"]]
