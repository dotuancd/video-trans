import time

import pytest
from fastapi.testclient import TestClient

from dich_server import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "UPLOADS_DIR", tmp_path / "storage" / "uploads")
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "a.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n", encoding="utf-8")
    (tmp_path / "a.vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n\n", encoding="utf-8")
    app = api.create_app(tmp_path, ":memory:")

    def fake_run(spec, params, progress):
        spec.output.write_text("x")
        return "ok"

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.pipeline, "run_step", fake_run)
        yield client, tmp_path


def test_listing_and_root_guard(client):
    client, root = client
    listing = client.get("/api/fs").json()
    assert {e["name"]: e["kind"] for e in listing["entries"]} == {"a.mp4": "media", "a.srt": "srt",
                                                                 "a.vi.srt": "vi_srt"}
    assert client.get("/api/fs", params={"path": "/etc"}).status_code == 403


def test_create_job_and_rerun(client):
    client, root = client
    created = client.post("/api/jobs", json={"path": str(root), "dub": True, "overwrite": True}).json()
    [job_id] = created["jobs"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
        time.sleep(0.01)
    assert [s["name"] for s in job["steps"]] == ["transcribe", "translate", "dub"]
    assert job["status"] == "done"
    job = client.post(f"/api/jobs/{job_id}/steps/translate/run",
                      json={"params": {"engine": "openai"}, "cascade": False}).json()
    assert job["steps"][1]["params"]["engine"] == "openai"


def test_subtitles_roundtrip(client):
    client, root = client
    path = str(root / "a.vi.srt")
    subs = client.get("/api/subtitles", params={"path": path}).json()
    assert subs["cues"][0] == {"index": 1, "start": 0.0, "end": 1.0, "source": "Hello", "text": "Xin chào"}
    assert subs["source_path"] == str(root / "a.srt")
    saved = client.put("/api/subtitles", json={"path": path, "cues": [
        {"start": 0, "end": 1, "text": "Chào bạn", "source": "Hi"}]}).json()
    assert saved == {"texts": 1, "sources": 1}
    assert "Chào bạn" in (root / "a.vi.srt").read_text(encoding="utf-8")
    assert "Hi" in (root / "a.srt").read_text(encoding="utf-8")
    assert client.get("/api/subtitles/vtt", params={"path": path}).text.startswith("WEBVTT")


def test_original_subtitles_alone(client):
    client, root = client
    path = str(root / "a.srt")
    subs = client.get("/api/subtitles", params={"path": path}).json()
    assert subs["source_path"] is None and subs["cues"][0]["text"] == "Hello"
    hey = {"start": 0, "end": 1, "text": "Hey"}
    assert client.put("/api/subtitles", json={"path": path, "cues": [hey]}).json() == {"texts": 1, "sources": 0}
    assert client.put("/api/subtitles", json={"path": path, "cues": [{**hey, "source": "x"}]}).status_code == 400


def test_subtitles_split_and_merge(client):
    client, root = client
    path = str(root / "a.vi.srt")
    split = [{"start": 0, "end": 0.4, "text": "Xin", "source": "Hel"},
             {"start": 0.4, "end": 1, "text": "chào", "source": "lo"}]
    assert client.put("/api/subtitles", json={"path": path, "cues": split}).json() == {"texts": 2, "sources": 2}
    subs = client.get("/api/subtitles", params={"path": path}).json()["cues"]
    assert [(c["start"], c["end"], c["source"], c["text"]) for c in subs] == [
        (0.0, 0.4, "Hel", "Xin"), (0.4, 1.0, "lo", "chào")]
    merged = [{"start": 0, "end": 1, "text": "Xin chào", "source": "Hello"}]
    assert client.put("/api/subtitles", json={"path": path, "cues": merged}).json() == {"texts": 1, "sources": 1}
    assert len(client.get("/api/subtitles", params={"path": path}).json()["cues"]) == 1
    bad = [{"start": 1, "end": 1, "text": "x"}]
    assert client.put("/api/subtitles", json={"path": path, "cues": bad}).status_code == 400


def test_step_log(client):
    client, root = client
    [job_id] = client.post("/api/jobs", json={"path": str(root / "a.mp4"), "overwrite": True}).json()["jobs"]
    for _ in range(200):
        if client.get(f"/api/jobs/{job_id}").json()["status"] == "done":
            break
        time.sleep(0.01)
    log = client.get(f"/api/jobs/{job_id}/steps/transcribe/log").json()
    assert "Lần 1" in log["text"] and log["offset"] == 0 and log["size"] == len(log["text"].encode())
    assert client.get(f"/api/jobs/{job_id}/steps/transcribe/log", params={"offset": log["size"]}).json()["text"] == ""
    assert client.get(f"/api/jobs/{job_id}/steps/mux/log").status_code == 404


def wait_done(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            return job
        time.sleep(0.01)
    return job


def test_upload_files_of_a_video_together(client):
    client, root = client
    video = client.put("/api/uploads", params={"name": "../b.mp4"}, content=b"video").json()
    assert video["kind"] == "media" and len(video["id"]) == 26
    folder = root / "storage" / "uploads" / video["id"]
    assert video["path"] == str(folder / "b.mp4") and (folder / "b.mp4").read_bytes() == b"video"
    subs = client.put("/api/uploads", params={"name": "b.srt", "id": video["id"]},
                      content="1\n00:00:00,000 --> 00:00:01,000\nHi\n\n".encode()).json()
    assert subs == {"id": video["id"], "path": str(folder / "b.srt"), "kind": "srt"}
    assert sorted(p.name for p in folder.iterdir()) == ["b.mp4", "b.srt"]

    assert client.put("/api/uploads", params={"name": "x.txt"}, content=b"").status_code == 400
    assert client.put("/api/uploads", params={"name": "c.srt", "id": "../../etc"}, content=b"").status_code == 400
    assert client.put("/api/uploads", params={"name": "c.srt", "id": "0" * 26}, content=b"").status_code == 400

    [job_id] = client.post("/api/jobs", json={"paths": [subs["path"]], "start": "srt", "dub": True, "mux": True}).json()["jobs"]
    job = wait_done(client, job_id)
    assert [s["name"] for s in job["steps"]] == ["translate", "dub", "mux"]
    assert job["steps"][0]["output"] == str(folder / "b.vi.srt")
    assert client.get("/api/subtitles", params={"path": subs["path"]}).status_code == 200
    assert client.post("/api/jobs", json={"paths": [video["path"]], "start": "srt"}).status_code == 400
    assert client.post("/api/jobs", json={}).status_code == 400


def test_subtitles_words(client):
    client, root = client
    get = lambda name: client.get("/api/subtitles", params={"path": str(root / name)}).json()["words"]
    assert get("a.vi.srt") is None
    (root / "a.words.json").write_text('[{"word": "Hello", "start": 0.1, "end": 0.9}]', encoding="utf-8")
    assert get("a.vi.srt") == get("a.srt") == [{"word": "Hello", "start": 0.1, "end": 0.9}]
    (root / "a.words.json").write_text("{broken", encoding="utf-8")
    assert get("a.srt") is None
