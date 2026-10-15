# Pipeline state for the web UI: one job per source file, one row per step, kept in SQLite so
# the list survives a restart. Steps run in job order, each only once every step before it is
# done, one worker thread per lane: "cpu" for the local models (big, they share the CPU, so one
# step at a time) and "net" for translating through OpenAI/Claude, which mostly waits on the network
# and so runs alongside dubbing.
#
# Step status: queued → running → done | failed | cancelled.  A step that is run again marks
# the steps after it queued (cascade) or stale (their output came from the old result).
# What each step prints and logs is kept per step, see steplog.py.
import json
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import settings
from dich import StepSpec, Steps, plan_steps, step_options
from dich_server.steplog import StepLogs

DEFAULT_DB = settings.DATA_DIR / "jobs.sqlite"
SCHEMA = """
create table if not exists jobs (
    id integer primary key,
    source text not null,
    start text not null,
    created_at real not null
);
create table if not exists steps (
    id integer primary key,
    job_id integer not null references jobs(id) on delete cascade,
    position integer not null,
    name text not null,
    inputs text not null,
    output text not null,
    params text not null,
    status text not null,
    message text,
    error text,
    done integer,
    total integer,
    attempt integer not null default 0,
    started_at real,
    finished_at real
);
create index if not exists steps_job on steps(job_id, position);
"""
ACTIVE = ("queued", "running")
LANES = ("cpu", "net")
REMOTE_ENGINES = ("openai", "claude")


def lane(name, params):
    """The worker lane a step runs in."""
    return "net" if name == "translate" and params.get("engine") in REMOTE_ENGINES else "cpu"


class Cancelled(Exception):
    pass


class Busy(Exception):
    """The request conflicts with a running step."""


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = None if path == ":memory:" else Path(path)
        if self.path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("pragma journal_mode=wal")
            self.db.execute("pragma foreign_keys=on")
            self.db.executescript(SCHEMA)

    def _query(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _write(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args)

    # --- reading ---

    def jobs(self):
        with self.lock:
            rows = self._query("select * from jobs order by id")
            steps = self._query("select * from steps order by job_id, position")
        by_job = {}
        for step in steps:
            by_job.setdefault(step["job_id"], []).append(step_dict(step))
        return [job_dict(job, by_job.get(job["id"], [])) for job in rows]

    def job(self, job_id):
        with self.lock:
            rows = self._query("select * from jobs where id = ?", job_id)
            if not rows:
                return None
            steps = self._query("select * from steps where job_id = ? order by position", job_id)
        return job_dict(rows[0], [step_dict(step) for step in steps])

    def step(self, job_id, name):
        rows = self._query("select * from steps where job_id = ? and name = ?", job_id, name)
        return step_dict(rows[0]) if rows else None

    # --- writing ---

    def add_job(self, source, start, specs, params, overwrite=False):
        """A job for source; steps whose output exists are done already unless overwrite.
        An older job for the same source is replaced; raises Busy while it is still queued/running."""
        source = str(source)
        with self.lock:
            old = self._query("select id from jobs where source = ?", source)
            for (old_id,) in old:
                if self._query(f"select 1 from steps where job_id = ? and status in {ACTIVE}", old_id):
                    raise Busy(f"{Path(source).name} đang được xử lý")
            self._write("begin")
            try:
                for (old_id,) in old:
                    self._write("delete from jobs where id = ?", old_id)
                job_id = self._write("insert into jobs (source, start, created_at) values (?, ?, ?)",
                                     source, start, time.time()).lastrowid
                now = time.time()
                for position, spec in enumerate(specs):
                    exists = not overwrite and spec.output.exists()
                    self._write(
                        "insert into steps (job_id, position, name, inputs, output, params, status, message,"
                        " finished_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        job_id, position, spec.name, json.dumps([str(p) for p in spec.inputs]), str(spec.output),
                        json.dumps(step_options(spec.name, params)), "done" if exists else "queued",
                        "Dùng file có sẵn" if exists else None, now if exists else None)
                self._write("commit")
            except BaseException:
                self._write("rollback")
                raise
        return job_id

    def claim_next(self, lane_name=None):
        """The first queued step (of lane_name, if given) whose earlier steps are all done, now marked running."""
        with self.lock:
            rows = self._query(
                "select * from steps s where status = 'queued' and not exists ("
                " select 1 from steps p where p.job_id = s.job_id and p.position < s.position"
                " and p.status != 'done') order by job_id, position")
            row = next((row for row in rows
                        if lane_name is None or lane(row["name"], json.loads(row["params"])) == lane_name), None)
            if row is None:
                return None
            self._write("update steps set status = 'running', started_at = ?, finished_at = null, done = null,"
                        " total = null, message = null, error = null, attempt = attempt + 1 where id = ?",
                        time.time(), row["id"])
            return step_dict(self._query("select * from steps where id = ?", row["id"])[0])

    def job_ids(self):
        return [row["id"] for row in self._query("select id from jobs")]

    def finish(self, step_id, status, message=None, error=None):
        self._write("update steps set status = ?, message = ?, error = ?, finished_at = ? where id = ?",
                    status, message, error, time.time(), step_id)

    def progress(self, step_id, done, total):
        self._write("update steps set done = ?, total = ? where id = ?", done, total, step_id)

    def rerun(self, job_id, name, params=None, cascade=True):
        """Queue step `name` again (with params merged into its options); the steps after it are
        queued too when cascade, otherwise those already done become stale."""
        with self.lock:
            steps = self._query("select * from steps where job_id = ? order by position", job_id)
            index = next((i for i, step in enumerate(steps) if step["name"] == name), None)
            if index is None:
                raise KeyError(name)
            if any(step["status"] == "running" for step in steps[index:]):
                raise Busy("Bước này hoặc bước sau đang chạy")
            step = steps[index]
            options = json.loads(step["params"])
            if params:
                options = step_options(name, {**options, **params})
            self._write("begin")
            self._write("update steps set status = 'queued', params = ?, message = null, error = null,"
                        " done = null, total = null where id = ?", json.dumps(options), step["id"])
            for later in steps[index + 1:]:
                if cascade:
                    self._write("update steps set status = 'queued', message = null, error = null, done = null,"
                                " total = null where id = ?", later["id"])
                elif later["status"] == "done":
                    self._write("update steps set status = 'stale' where id = ?", later["id"])
            self._write("commit")

    def cancel(self, job_id):
        """Queued steps of the job become cancelled; the caller interrupts a running one."""
        self._write("update steps set status = 'cancelled', finished_at = ? where job_id = ? and status = 'queued'",
                    time.time(), job_id)

    def delete(self, job_id):
        if self._query("select 1 from steps where job_id = ? and status = 'running'", job_id):
            raise Busy("Job đang chạy, hãy dừng trước")
        self._write("delete from jobs where id = ?", job_id)

    def recover(self):
        """After a restart: steps left running were interrupted."""
        self._write("update steps set status = 'failed', error = 'Bị gián đoạn (server đã khởi động lại)',"
                    " finished_at = ? where status = 'running'", time.time())


def step_dict(row):
    return {"id": row["id"], "job_id": row["job_id"], "position": row["position"], "name": row["name"],
            "inputs": json.loads(row["inputs"]), "output": row["output"], "params": json.loads(row["params"]),
            "status": row["status"], "exists": Path(row["output"]).exists(), "message": row["message"], "error": row["error"],
            "done": row["done"], "total": row["total"], "attempt": row["attempt"],
            "started_at": row["started_at"], "finished_at": row["finished_at"]}


def job_dict(row, steps):
    return {"id": row["id"], "source": row["source"], "start": row["start"], "created_at": row["created_at"],
            "status": job_status(steps), "steps": steps}


def job_status(steps):
    statuses = {step["status"] for step in steps}
    for status in ("running", "failed", "queued", "cancelled", "stale"):
        if status in statuses:
            return status
    return "done"


class Pipeline:
    """The store plus a worker thread per lane that runs queued steps. listeners get every change as
    {"type": "job", "job": ...}, {"type": "progress", ...}, {"type": "log", ...} (new text of a step's
    log, starting at byte `offset` of it) or {"type": "deleted", "id": ...}.
    Logs go to log_dir, by default next to the database (a temporary folder for an in-memory one)."""

    def __init__(self, store, run_step=None, log_dir=None):
        self.store = store
        log_dir = log_dir or (store.path.parent / "logs" if store.path else tempfile.mkdtemp(prefix="dich-logs-"))
        self.logs = StepLogs(log_dir, self._emit_log)
        self.steps = Steps()
        self.run_step = run_step or self.steps.run
        self.listeners = set()
        self._stop = False
        self._wake = {name: threading.Event() for name in LANES}
        self._running = {name: None for name in LANES}      # step dict being run in each lane
        self._cancel = {name: threading.Event() for name in LANES}

    def start(self):
        self.store.recover()
        self.logs.prune(self.store.job_ids())
        for name in LANES:
            threading.Thread(target=self._work, args=(name,), name=f"pipeline-{name}", daemon=True).start()

    def stop(self):
        self._stop = True
        for name in LANES:
            self._cancel[name].set()
        self.wake()

    def wake(self):
        for event in self._wake.values():
            event.set()

    def emit(self, event):
        for listener in list(self.listeners):
            listener(event)

    def _emit_log(self, step, offset, text):
        self.emit({"type": "log", "job_id": step["job_id"], "step_id": step["id"], "offset": offset, "text": text})

    def emit_job(self, job_id):
        job = self.store.job(job_id)
        if job:
            self.emit({"type": "job", "job": job})

    # --- actions (called from the API) ---

    def add(self, sources, start, root, out=None, translate=True, dub=False, mux=False, overwrite=False,
            params=None):
        """A job per source; returns (job ids, [skipped messages])."""
        added, skipped = [], []
        for source in sources:
            notes = []
            specs = plan_steps(source, start, root, out, translate, dub, mux, warn=notes.append)
            try:
                job_id = self.store.add_job(source, start, specs, params or {}, overwrite)
            except Busy as error:
                skipped.append(str(error))
                continue
            skipped += [f"{source.name}: không tìm thấy video để ghép" for _ in notes]
            self.logs.remove(job_id)        # a reused id: logs of a deleted job
            added.append(job_id)
            self.emit_job(job_id)
        self.logs.prune(self.store.job_ids())   # jobs replaced by these
        self.wake()
        return added, skipped

    def rerun(self, job_id, name, params=None, cascade=True):
        self.store.rerun(job_id, name, params, cascade)
        self.emit_job(job_id)
        self.wake()

    def cancel(self, job_id):
        self.store.cancel(job_id)
        for name, running in self._running.items():
            if running and running["job_id"] == job_id:
                self._cancel[name].set()
        self.emit_job(job_id)

    def delete(self, job_id):
        self.store.delete(job_id)
        self.logs.remove(job_id)
        self.emit({"type": "deleted", "id": job_id})

    # --- worker ---

    def _work(self, lane_name):
        wake, cancel = self._wake[lane_name], self._cancel[lane_name]
        while not self._stop:
            wake.clear()
            step = self.store.claim_next(lane_name)
            if step is None:
                wake.wait()
                continue
            self._running[lane_name] = step
            cancel.clear()
            log = self.logs.open(step)
            log.line(f"{step['name']}: {', '.join(Path(p).name for p in step['inputs'])} → {Path(step['output']).name}")
            log.line(f"options: {json.dumps(step['params'], ensure_ascii=False)}")
            self.emit_job(step["job_id"])
            spec = StepSpec(step["name"], tuple(Path(p) for p in step["inputs"]), Path(step["output"]))
            last = [0.0, 0]     # time of the last progress event, last tenth logged

            def progress(done, total, step=step, log=log):
                if cancel.is_set():
                    raise Cancelled()
                self.store.progress(step["id"], done, total)
                now = time.monotonic()
                if now - last[0] > 0.2 or done == total:
                    last[0] = now
                    self.emit({"type": "progress", "job_id": step["job_id"], "step_id": step["id"],
                               "done": done, "total": total})
                tenth = 10 * done // total if total else 0
                if tenth > last[1]:
                    last[1] = tenth
                    log.line(f"{done}/{total}")

            started = time.monotonic()
            try:
                message = self.run_step(spec, step["params"], progress)
                self.store.finish(step["id"], "done", message)
                log.line(f"Xong sau {time.monotonic() - started:.1f}s — {message}")
            except Cancelled:
                self.store.finish(step["id"], "cancelled", "Đã dừng")
                log.line(f"Đã dừng sau {time.monotonic() - started:.1f}s")
            except Exception as error:   # any failure stays on the step, retryable from the UI
                log.write(traceback.format_exc())
                traceback.print_exc(file=sys.__stderr__)
                self.store.finish(step["id"], "failed", error=f"{type(error).__name__}: {error}")
                log.line(f"Lỗi sau {time.monotonic() - started:.1f}s")
            finally:
                self.logs.close(log)
                self._running[lane_name] = None
            if cancel.is_set():
                self.store.cancel(step["job_id"])
            self.emit_job(step["job_id"])
            self.wake()                 # the job's next step may belong to the other lane
