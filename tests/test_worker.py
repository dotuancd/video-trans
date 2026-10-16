import time
from pathlib import Path

from dich.runtime.supervisor import Supervisor
from test_engine import make_job, statuses


def wait(predicate, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_workers_run_cancel_and_capture_fd(store, video, tmp_path):
    supervisor = Supervisor(store.path, store.data_dir, {"cpu": 1, "net": 1}).start()
    try:
        job_id = make_job(store, video, export=False)
        assert wait(lambda: store.job(job_id)["status"] == "done"), statuses(store, job_id)
        asr = store.task_by_key(job_id, "asr")
        attempt = store.attempt(asr["current_attempt"])
        log = (Path(attempt["dir"]) / "system.log").read_text()
        assert "fake asr stdout line" in log and "fd2 c-level line" in log

        # stop in the middle of a long step: the worker process is killed and replaced
        store.rerun(asr["id"], "only", params={"sleep": 60})
        assert wait(lambda: store.task(asr["id"])["status"] == "running")
        pid = store.attempts_of(asr["id"])[0]["pid"]
        store.cancel_job(job_id)
        assert wait(lambda: store.task(asr["id"])["status"] == "cancelled", 10)
        assert wait(lambda: all(p.is_alive() and p.pid != pid for p in supervisor.processes.values()), 10)

        store.rerun(asr["id"], "only", params={"sleep": 0})
        assert wait(lambda: store.task(asr["id"])["status"] == "done")
    finally:
        supervisor.stop()


def test_scale_up_and_down_while_running(store, video, tmp_path):
    counts = tmp_path / "workers.json"
    supervisor = Supervisor(store.path, store.data_dir, {"cpu": 1, "net": 1}, counts_file=counts).start()
    try:
        assert supervisor.scale("cpu", 3) == 3
        assert len([k for k in supervisor.processes if k[0] == "cpu"]) == 3
        assert Supervisor(store.path, store.data_dir, {"cpu": 1}, counts_file=counts).workers["cpu"] == 3

        # a busy worker that is no longer wanted finishes its step, then exits (not killed, not retried)
        job_id = make_job(store, video, export=False)
        asr = store.task_by_key(job_id, "asr")
        store.rerun(asr["id"], "only", params={"sleep": 2})
        assert wait(lambda: store.task(asr["id"])["status"] == "running")
        supervisor.scale("cpu", 0)
        assert len(supervisor.retiring) == 3 and all(r for *_, r in supervisor.status()["cpu"])
        assert wait(lambda: not supervisor.retiring, 15)
        task = store.task(asr["id"])
        assert task["status"] == "done" and task["retries"] == 0
    finally:
        supervisor.stop()


def test_old_database_gets_project_column(tmp_path):
    import sqlite3
    from dich.queue.sqlite import Store
    path = tmp_path / "dich.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table jobs (id text primary key, title text not null, source text not null, template text not null,"
               " langs text not null, overrides text not null default '{}', export_dir text, trace_id text not null,"
               " created_at real not null)")
    db.execute("insert into jobs values ('job_old', 'a.mp4', '/a.mp4', 'default', '[\"vi\"]', '{}', null, 't', 0)")
    db.commit()
    db.close()
    store = Store(path)
    try:
        assert store.job("job_old")["project_id"] is None
    finally:
        store.close()
