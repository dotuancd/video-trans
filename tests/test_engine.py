import json
import time
from pathlib import Path

import pytest

import fake_drivers
from dich.core.dag import plan
from dich.core.template import StageConf
from dich.queue.base import Busy, NeedsConfirm
from dich.runtime.local import run_job
from dich.formats.transcript import Transcript


def stages(asr=None, gate=None):
    return [StageConf(stage="source", driver="file", params={}),
            StageConf(stage="asr", driver="fake", params=asr or {}),
            StageConf(stage="translate", driver="fake"),
            StageConf(stage="dub", driver="fake", gate=gate)]


def make_job(store, video, langs=("vi",), export=True, **options):
    specs = plan([s.model_copy(update={"params": {"path": str(video)}}) if s.stage == "source" else s
                  for s in stages(**options)], list(langs))
    return store.create_job(video.name, str(video), "test", list(langs), specs,
                            export_dir=video.parent if export else None)


def statuses(store, job_id):
    return {t["key"]: t["status"] for t in store.tasks_of(job_id)}


def test_runs_through_and_exports(store, video):
    job_id = make_job(store, video, ("vi", "ja"))
    job = run_job(store, job_id)
    assert statuses(store, job_id) == {"source": "done", "asr": "done", "translate@vi": "done",
                                       "translate@ja": "done", "dub@vi": "done", "dub@ja": "skipped"}
    assert job["status"] == "done"
    assert "vi:hello." in (video.parent / "a.vi.srt").read_text(encoding="utf-8")
    assert (video.parent / "a.srt").exists() and (video.parent / "a.ja.srt").exists()
    assert (video.parent / "a.vi.wav").exists()
    translate = store.task_by_key(job_id, "translate@vi")
    assert translate["message"] is None or isinstance(translate["message"], str)
    out = store.output_of(translate, "transcript.vi")
    assert Transcript.load(out.path).cues[0].src_id == "c_0001"


def test_same_input_is_cached(store, video):
    run_job(store, make_job(store, video, export=False))
    before = dict(fake_drivers.RUNS)
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    assert fake_drivers.RUNS == before
    attempt = store.attempt(store.task_by_key(job_id, "asr")["current_attempt"])
    assert attempt["origin"] == "cache"
    assert Path(store.output_of(store.task_by_key(job_id, "asr"), "transcript.src").path).is_relative_to(store.data_dir)


def test_rerun_only_makes_later_stale_then_refresh(store, video):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    store.rerun(asr["id"], "only", params={"sleep": 0.01})
    assert statuses(store, job_id)["asr"] == "queued"
    assert statuses(store, job_id)["translate@vi"] == "stale"
    run_job(store, job_id)
    assert statuses(store, job_id)["dub@vi"] == "stale"
    store.refresh(job_id)
    run_job(store, job_id)
    assert set(statuses(store, job_id).values()) == {"done"}


def test_rerun_cascade_and_busy(store, video):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    store.rerun(asr["id"], "cascade")
    assert statuses(store, job_id)["dub@vi"] == "blocked"
    task = store.claim(["cpu"], job_id=job_id)
    assert task["key"] == "asr"
    with pytest.raises(Busy):
        store.rerun(asr["id"], "only")


def test_human_edit_marks_later_stale_and_guards_rerun(store, video, tmp_path):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    translate = store.task_by_key(job_id, "translate@vi")
    edited = tmp_path / "edit.json"
    Transcript("vi", []).save(edited)
    store.save_human(translate["id"], "transcript", edited)
    assert statuses(store, job_id)["dub@vi"] == "stale"
    with pytest.raises(NeedsConfirm):
        store.rerun(translate["id"], "only")
    asr = store.task_by_key(job_id, "asr")
    store.rerun(asr["id"], "only")                 # only asr runs: the edit is safe
    store.rerun(store.task_by_key(job_id, "asr")["id"], "only")
    with pytest.raises(NeedsConfirm):
        store.rerun(asr["id"], "cascade")
    store.rerun(asr["id"], "cascade", confirm=True)


def test_gate_waits_for_review(store, video):
    job_id = make_job(store, video, gate="review", export=False)
    job = run_job(store, job_id)
    assert job["status"] == "waiting_review" and statuses(store, job_id)["dub@vi"] == "waiting_review"
    store.resume(job_id)
    assert run_job(store, job_id)["status"] == "done"


def test_user_and_system_errors(store, video):
    job_id = make_job(store, video, asr={"fail": "user"}, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    assert asr["status"] == "failed" and asr["message"] == "File hỏng"
    assert statuses(store, job_id)["translate@vi"] == "blocked"
    store.rerun(asr["id"], params={"fail": "system"})
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    attempt = store.attempts_of(asr["id"])[0]
    assert asr["message"] == f"Lỗi hệ thống (mã {attempt['span_id'][:8]})"
    assert "ValueError: boom" in (Path(attempt["dir"]) / "system.log").read_text()
    events = [e["message"] for e in store.attempt_events(attempt["id"])]
    assert asr["message"] in events and not any("boom" in (m or "") for m in events)


def test_lease_expiry_requeues_then_fails(store, video):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    store.rerun(asr["id"])
    for attempt in range(3):
        task = store.claim(["cpu"], lease=-1)
        store.start_attempt(task, pid=999999)
        assert store.reap() == 1
    assert store.task(asr["id"])["status"] == "failed"
    assert store.task(asr["id"])["retries"] == 2


def test_dead_worker_is_reaped(store, video):
    job_id = make_job(store, video, export=False)
    task = store.claim(["cpu"])
    store.start_attempt(task, pid=999999)
    assert store.reap(alive=lambda pid: True) == 0
    assert store.reap(alive=lambda pid: False) == 1
    assert store.task(task["id"])["status"] == "queued"


def test_cancel_and_resume(store, video):
    job_id = make_job(store, video, export=False)
    store.cancel_job(job_id)
    assert set(statuses(store, job_id).values()) <= {"cancelled", "skipped"}
    store.resume(job_id)
    assert run_job(store, job_id)["status"] == "done"


def test_add_language_reuses_done_tasks(store, video):
    from dich.core.jobs import add_langs
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    store.x("update jobs set template = 'default', overrides = ? where id = ?",
            json.dumps({"asr": {"driver": "fake"}, "translate": {"driver": "fake"}, "dub": {"driver": "fake"},
                        "mux": {"enabled": False}}), job_id)
    assert add_langs(store, job_id, ["fr"]) == ["translate@fr", "dub@fr"]
    assert statuses(store, job_id)["translate@fr"] == "queued"
    asr_attempts = len(store.attempts_of(store.task_by_key(job_id, "asr")["id"]))
    run_job(store, job_id)
    assert len(store.attempts_of(store.task_by_key(job_id, "asr")["id"])) == asr_attempts


def test_keep_last_deletes_older_dub(store, video):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    dub = store.task_by_key(job_id, "dub@vi")
    first = store.output_of(dub, "audio.dub.vi")
    store.x("update tasks set params = ? where id = ?", json.dumps({}), dub["id"])
    store.rerun(store.task_by_key(job_id, "translate@vi")["id"], "cascade")
    store.x("delete from attempts where fingerprint is not null and task_id in (select id from tasks where key like 'translate%')")
    run_job(store, job_id)
    dub = store.task_by_key(job_id, "dub@vi")
    assert store.output_of(dub, "audio.dub.vi")["id"] != first["id"]
    assert not Path(first["path"]).exists()
