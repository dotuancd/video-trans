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
