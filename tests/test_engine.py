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



def test_rerun_fresh_skips_the_cache(store, video):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    store.rerun(asr["id"], "cascade")
    run_job(store, job_id)
    assert store.attempt(store.task_by_key(job_id, "asr")["current_attempt"])["origin"] == "cache"
    store.rerun(asr["id"], "cascade", fresh=True)
    assert store.task(asr["id"])["fresh"]
    before = dict(fake_drivers.RUNS)
    run_job(store, job_id)
    asr = store.task(asr["id"])
    assert store.attempt(asr["current_attempt"])["origin"] == "driver"
    assert not asr["fresh"]
    changed = {k for k in fake_drivers.RUNS if fake_drivers.RUNS[k] != before.get(k)}
    assert any("asr" in str(k) for k in changed) and not any("translate" in str(k) for k in changed)

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
    edited = tmp_path / "edit.jsonl"
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


def test_skipped_by_the_driver_can_run_again(store, video):
    job_id = make_job(store, video, asr={"fail": "skip"}, export=False)
    run_job(store, job_id)
    asr = store.task_by_key(job_id, "asr")
    assert asr["status"] == "skipped" and asr["message"] == "Không có gì để làm" and asr["error"] is None
    assert statuses(store, job_id)["translate@vi"] == "blocked"
    store.rerun(asr["id"], params={"fail": None})
    run_job(store, job_id)
    assert statuses(store, job_id)["translate@vi"] == "done"


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


def test_pause_and_unpause_many(store, video):
    a, b = make_job(store, video, export=False), make_job(store, video, export=False)
    gated = store.task_by_key(b, "dub@vi")
    store.x("update tasks set gate = 1 where id = ?", gated["id"])
    assert store.pause_jobs([a, b, "nope"]) == [a, b]
    assert set(statuses(store, a).values()) <= {"cancelled", "skipped"}
    assert store.pause_jobs([a]) == []                    # nothing left to pause
    assert store.unpause_jobs([a, b]) == [a, b]
    assert run_job(store, a)["status"] == "done"
    assert run_job(store, b)["status"] == "waiting_review"   # unpausing does not pass a review gate
    assert store.unpause_jobs([b]) == []


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


def rerun_dub(store, job_id):
    dub = store.task_by_key(job_id, "dub@vi")
    first = store.output_of(dub, "audio.dub.vi")
    store.x("update tasks set params = ? where id = ?", json.dumps({}), dub["id"])
    store.rerun(store.task_by_key(job_id, "translate@vi")["id"], "cascade")
    store.x("delete from attempts where fingerprint is not null and task_id in (select id from tasks where key like 'translate%')")
    run_job(store, job_id)
    return first


def test_keep_last_kept_for_days_then_pruned(store, video, monkeypatch):
    monkeypatch.setattr("dich.settings.KEEP_DAYS", 7)
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    first = rerun_dub(store, job_id)
    assert Path(first["path"]).exists()
    assert store.prune_old() == 0
    store.x("update attempts set finished_at = ? where id = ?", time.time() - 8 * 86400, first["attempt_id"])
    assert store.prune_old() == 1
    assert not Path(first["path"]).exists()
    current = store.output_of(store.task_by_key(job_id, "dub@vi"), "audio.dub.vi")
    assert Path(current["path"]).exists()


def test_use_earlier_attempt(store, video, monkeypatch):
    monkeypatch.setattr("dich.settings.KEEP_DAYS", 7)
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    translate = store.task_by_key(job_id, "translate@vi")
    old = translate["current_attempt"]
    store.rerun(translate["id"], "only", confirm=True)
    store.x("delete from attempts where fingerprint is not null and status != 'done'")
    run_job(store, job_id)
    assert store.task_by_key(job_id, "translate@vi")["current_attempt"] != old
    store.refresh(job_id)
    run_job(store, job_id)
    store.use_attempt(translate["id"], old)
    assert store.task_by_key(job_id, "translate@vi")["current_attempt"] == old
    assert statuses(store, job_id)["translate@vi"] == "done"
    assert statuses(store, job_id)["dub@vi"] == "stale"
    store.x("update attempts set status = 'failed' where id = ?", old)
    with pytest.raises(Busy):
        store.use_attempt(translate["id"], old)
    with pytest.raises(KeyError):
        store.use_attempt(translate["id"], "att_nope")


def test_keep_last_deletes_older_dub(store, video, monkeypatch):
    monkeypatch.setattr("dich.settings.KEEP_DAYS", 0)
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


def test_ids_are_prefixed(store, video):
    from dich.core.ids import is_id
    job_id = make_job(store, video)
    run_job(store, job_id)
    assert is_id(job_id, "job") and not is_id(job_id, "task")
    task = store.tasks_of(job_id)[0]
    attempt = store.attempts_of(task["id"])[0]
    assert is_id(task["id"], "task") and is_id(attempt["id"], "att")
    assert is_id(store.artifacts_of(attempt["id"])[0]["id"], "art")
    assert is_id(store.call("voices", {}), "call")


def test_numeric_id_database_is_set_aside(tmp_path):
    import sqlite3
    from dich.queue.sqlite import Store
    path = tmp_path / "dich.sqlite"
    old = sqlite3.connect(str(path))
    old.execute("create table jobs (id integer primary key, title text)")
    old.close()
    (tmp_path / "jobs" / "1").mkdir(parents=True)
    store = Store(path)
    store.close()
    assert (tmp_path / "dich.sqlite.old").exists() and (tmp_path / "jobs.old" / "1").is_dir()
    assert not (tmp_path / "jobs").exists()


def test_human_edit_of_stale_task_runs_only_later_steps(store, video, tmp_path):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    store.rerun(store.task_by_key(job_id, "asr")["id"], "only")
    run_job(store, job_id)
    assert statuses(store, job_id)["translate@vi"] == "stale"
    runs = dict(fake_drivers.RUNS)
    edited = tmp_path / "edit.jsonl"
    Transcript("vi", []).save(edited)
    store.save_human(store.task_by_key(job_id, "translate@vi")["id"], "transcript", edited)
    assert statuses(store, job_id)["translate@vi"] == "done"
    assert statuses(store, job_id)["dub@vi"] == "stale"
    store.refresh(job_id)
    run_job(store, job_id)
    assert set(statuses(store, job_id).values()) == {"done"}
    translate = store.task_by_key(job_id, "translate@vi")
    assert store.attempt(translate["current_attempt"])["origin"] == "human"
    assert fake_drivers.RUNS.get("translate") == runs.get("translate")


def test_human_edit_of_failed_task_unblocks_later_steps(store, video, tmp_path):
    job_id = make_job(store, video, export=False)
    run_job(store, job_id)
    translate = store.task_by_key(job_id, "translate@vi")
    store.rerun(translate["id"], "cascade")
    task = store.claim(["cpu", "gpu", "net"], job_id=job_id)
    attempt = store.start_attempt(task)
    store.fail(task["id"], attempt["id"], "boom")
    assert statuses(store, job_id)["dub@vi"] == "blocked"
    edited = tmp_path / "edit.jsonl"
    Transcript("vi", []).save(edited)
    store.save_human(translate["id"], "transcript", edited)
    assert statuses(store, job_id)["translate@vi"] == "done"
    store.refresh(job_id)
    assert run_job(store, job_id)["status"] == "done"


def test_job_cost_sums_paid_usage_logs(store, video):
    job = make_job(store, video)
    store.event(job, "log", "Dịch xong", {"cost_usd": 0.002, "billed": True})
    store.event(job, "log", "Đã dùng trước khi lỗi", {"cost_usd": 0.001})
    store.event(job, "log", "Claude", {"cost_usd": 5.0, "billed": False})   # plan usage, not paid
    store.event(job, "log", "Không có giá", {"cost_usd": None, "billed": True})
    assert store.job(job)["cost_usd"] == pytest.approx(0.003)
    assert [j["cost_usd"] for j in store.jobs() if j["id"] == job] == [pytest.approx(0.003)]
    other = make_job(store, video, export=False)
    assert store.job(other)["cost_usd"] is None


def test_checkpoint_kept_across_attempts_until_done(store, video):
    from dich.runtime.context import TaskContext

    job_id = make_job(store, video, export=False)
    task = store.task_by_key(job_id, "translate@vi")
    attempt = store.start_attempt(task, fingerprint="fp")
    TaskContext(store, task, attempt).checkpoint.put("chunk", {"0": "xin chào"})
    later = TaskContext(store, task, store.start_attempt(task, fingerprint="fp"))
    assert later.checkpoint.resumed == 1
    assert run_job(store, job_id)["status"] == "done"
    assert not (later.dir.parent / "checkpoint").exists()


def test_page_title_names_a_job_only_while_it_is_named_after_its_url(store):
    url = "https://youtu.be/xyz"
    job_id = store.create_job(url, url, "test", ["vi"], [])
    store.set_auto_title(job_id, "Tiêu đề trang")
    assert store.job(job_id)["title"] == "Tiêu đề trang"
    store.set_title(job_id, "Bài giảng 3")
    store.set_auto_title(job_id, "Tiêu đề trang")
    assert store.job(job_id)["title"] == "Bài giảng 3"
