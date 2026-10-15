import time

import pytest
from fastapi.testclient import TestClient

from dich_server import api


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    saved = client.put("/api/subtitles", json={"path": path, "texts": ["Chào bạn"], "sources": ["Hi"]}).json()
    assert saved == {"texts": 1, "sources": 1}
    assert "Chào bạn" in (root / "a.vi.srt").read_text(encoding="utf-8")
    assert "Hi" in (root / "a.srt").read_text(encoding="utf-8")
    assert client.get("/api/subtitles/vtt", params={"path": path}).text.startswith("WEBVTT")


def test_original_subtitles_alone(client):
    client, root = client
    path = str(root / "a.srt")
    subs = client.get("/api/subtitles", params={"path": path}).json()
    assert subs["source_path"] is None and subs["cues"][0]["text"] == "Hello"
    assert client.put("/api/subtitles", json={"path": path, "texts": ["Hey"]}).json() == {"texts": 1, "sources": 0}
    assert client.put("/api/subtitles", json={"path": path, "texts": ["Hey"], "sources": ["x"]}).status_code == 400


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
