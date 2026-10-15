# Jobs, tasks, attempts, artifacts and events in one SQLite file (WAL), shared by the API process and
# the worker processes; each process opens its own connection.
#
# Task status: blocked → queued → running → done | failed | cancelled, plus
#   skipped         the stage can't or needn't run for this job (unsupported language, input from the source)
#   waiting_review  a gate: the job waits for the user before this task
#   stale           done, but an input changed since (rerun "only", or an edit by hand before it)
# A worker claims a queued task atomically and holds a lease it renews; a task whose lease runs out
# (worker crashed, killed) goes back to queued, at most MAX_RETRIES times.
import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dich import settings
from dich.logging.trace import new_span_id, new_trace_id
from dich.queue.base import Busy, NeedsConfirm
from dich.storage.artifacts import Artifact

SCHEMA = """
create table if not exists jobs (
    id integer primary key,
    title text not null,
    source text not null,
    template text not null,
    langs text not null,
    overrides text not null default '{}',
    export_dir text,
    trace_id text not null,
    created_at real not null
);
create table if not exists tasks (
    id integer primary key,
    job_id integer not null references jobs(id) on delete cascade,
    key text not null,
    position integer not null,
    stage text not null,
    driver text not null,
    lang text,
    params text not null,
    inputs text not null,
    outputs text not null,
    deps text not null,
    lane text not null,
    gate integer not null default 0,
    gate_passed integer not null default 0,
    status text not null,
    message text,
    error text,
    done integer,
    total integer,
    priority integer not null default 0,
    retries integer not null default 0,
    lease_until real,
    cancel_requested integer not null default 0,
    current_attempt integer,
    exports text not null default '[]',
    updated_at real not null,
    unique (job_id, key)
);
create index if not exists tasks_claim on tasks(status, lane, priority, id);
create table if not exists attempts (
    id integer primary key,
    task_id integer not null references tasks(id) on delete cascade,
    n integer not null,
    span_id text not null,
    origin text not null,
    status text not null,
    fingerprint text,
    params text,
    pid integer,
    dir text not null,
    message text,
    error text,
    started_at real not null,
    finished_at real
);
create index if not exists attempts_fp on attempts(fingerprint, status);
create table if not exists artifacts (
    id integer primary key,
    job_id integer not null references jobs(id) on delete cascade,
    task_id integer not null references tasks(id) on delete cascade,
    attempt_id integer not null references attempts(id) on delete cascade,
    name text not null,
    slot text not null,
    path text not null,
    sha256 text,
    size integer,
    keep text not null default 'always',
    external integer not null default 0,
    deleted integer not null default 0,
    created_at real not null
);
create index if not exists artifacts_attempt on artifacts(attempt_id);
create table if not exists events (
    id integer primary key,
    job_id integer,
    task_id integer,
    attempt_id integer,
    ts real not null,
    level text not null,
    type text not null,
    message text,
    data text
);
create index if not exists events_job on events(job_id, id);
create table if not exists calls (
    id integer primary key,
    kind text not null,
    args text not null,
    status text not null,
    result text,
    error text,
    created_at real not null
);
"""
MAX_RETRIES = 2
LEASE = 30.0
ACTIVE = ("queued", "running")
JOB_STATUS_ORDER = ("running", "failed", "waiting_review", "queued", "blocked", "cancelled", "stale")


def loads(value, default=None):
    return json.loads(value) if value else default


class Store:
    def __init__(self, path=None, data_dir=None):
        path = Path(path or settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.data_dir = Path(data_dir or path.parent)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("pragma journal_mode=wal")
            self.db.execute("pragma foreign_keys=on")
            self.db.execute("pragma synchronous=normal")
            self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    @contextmanager
    def tx(self):
        """One write transaction; nested calls join the outer one."""
        with self.lock:
            if self.db.in_transaction:
                yield self.db
                return
            self.db.execute("begin immediate")
            try:
                yield self.db
                self.db.execute("commit")
            except BaseException:
                self.db.execute("rollback")
                raise

    def q(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def x(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args)

    # --- events ---

    def event(self, job_id, type_, message=None, data=None, level="info", task_id=None, attempt_id=None):
        return self.x("insert into events (job_id, task_id, attempt_id, ts, level, type, message, data)"
                      " values (?, ?, ?, ?, ?, ?, ?, ?)", job_id, task_id, attempt_id, time.time(), level, type_,
                      message, json.dumps(data, ensure_ascii=False) if data is not None else None).lastrowid

    def events_after(self, after, limit=500, job_id=None):
        sql = "select * from events where id > ?" + (" and job_id = ?" if job_id else "") + " order by id limit ?"
        args = (after, job_id, limit) if job_id else (after, limit)
        return [event_dict(r) for r in self.q(sql, *args)]

    def last_event_id(self):
        return self.one("select coalesce(max(id), 0) as id from events")["id"]

    def attempt_events(self, attempt_id, limit=2000):
        rows = self.q("select * from events where attempt_id = ? and type != 'progress' order by id desc limit ?",
                      attempt_id, limit)
        return [event_dict(r) for r in reversed(rows)]

    def _status(self, task, status, **fields):
        """Set a task's status (and fields) and record the change as an event."""
        sets = ", ".join(f"{k} = ?" for k in ("status", "updated_at", *fields))
        self.x(f"update tasks set {sets} where id = ?", status, time.time(), *fields.values(), task["id"])
        self.event(task["job_id"], "task", data={"key": task["key"], "status": status}, task_id=task["id"])

    # --- jobs ---

    def create_job(self, title, source, template, langs, specs, overrides=None, export_dir=None):
        with self.tx():
            job_id = self.x("insert into jobs (title, source, template, langs, overrides, export_dir, trace_id,"
                            " created_at) values (?, ?, ?, ?, ?, ?, ?, ?)", title, str(source), template,
                            json.dumps(langs), json.dumps(overrides or {}), str(export_dir) if export_dir else None,
                            new_trace_id(), time.time()).lastrowid
            self._insert_tasks(job_id, specs)
            self.event(job_id, "job", data={"created": True})
            self.advance(job_id)
        return job_id

    def _insert_tasks(self, job_id, specs, start=0):
        for position, spec in enumerate(specs, start):
            self.x("insert into tasks (job_id, key, position, stage, driver, lang, params, inputs, outputs, deps,"
                   " lane, gate, status, message, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   job_id, spec.key, position, spec.stage, spec.driver, spec.lang, json.dumps(spec.params),
                   json.dumps(spec.inputs), json.dumps(spec.outputs), json.dumps(spec.deps), spec.lane,
                   int(spec.gate), "skipped" if spec.skip else "blocked", spec.skip, time.time())

    def add_langs(self, job_id, langs, specs):
        """New tasks of a re-planned job (more languages); existing tasks and their results stay."""
        with self.tx():
            existing = {r["key"] for r in self.q("select key from tasks where job_id = ?", job_id)}
            new = [s for s in specs if s.key not in existing]
            count = self.one("select count(*) as n from tasks where job_id = ?", job_id)["n"]
            positions = {s.key: i for i, s in enumerate(specs)}
            self._insert_tasks(job_id, new, count)
            for spec in new:   # keep stage order for display
                self.x("update tasks set position = ? where job_id = ? and key = ?", positions[spec.key], job_id, spec.key)
            for key, position in positions.items():
                self.x("update tasks set position = ? where job_id = ? and key = ?", position, job_id, key)
            self.x("update jobs set langs = ? where id = ?", json.dumps(langs), job_id)
            self.event(job_id, "job", data={"langs": langs})
            self.advance(job_id)
        return [s.key for s in new]

    def set_title(self, job_id, title):
        self.x("update jobs set title = ? where id = ?", title, job_id)
        self.event(job_id, "job", data={"title": title})

    def jobs(self):
        rows = self.q("select * from jobs order by id desc")
        tasks = {}
        for t in self.q("select * from tasks order by job_id, position"):
            tasks.setdefault(t["job_id"], []).append(task_dict(t))
        return [job_dict(r, tasks.get(r["id"], [])) for r in rows]

    def job(self, job_id):
        row = self.one("select * from jobs where id = ?", job_id)
        if not row:
            return None
        return job_dict(row, [task_dict(t) for t in self.q("select * from tasks where job_id = ? order by position",
                                                           job_id)])

    def job_row(self, job_id):
        return self.one("select * from jobs where id = ?", job_id)

    def delete_job(self, job_id):
        with self.tx():
            if self.one("select 1 from tasks where job_id = ? and status = 'running'", job_id):
                raise Busy("Job đang chạy, hãy dừng trước")
            self.x("delete from events where job_id = ?", job_id)
            self.x("delete from jobs where id = ?", job_id)
            self.event(job_id, "job", data={"deleted": True})
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def job_dir(self, job_id):
        return self.data_dir / "jobs" / str(job_id)

    # --- tasks ---

    def task(self, task_id):
        row = self.one("select * from tasks where id = ?", task_id)
        return task_dict(row) if row else None

    def task_by_key(self, job_id, key):
        row = self.one("select * from tasks where job_id = ? and key = ?", job_id, key)
        return task_dict(row) if row else None

    def tasks_of(self, job_id):
        return [task_dict(t) for t in self.q("select * from tasks where job_id = ? order by position", job_id)]

    def advance(self, job_id):
        """Blocked tasks whose inputs are all done become queued (or wait at their gate)."""
        with self.tx():
            tasks = self.tasks_of(job_id)
            status = {t["key"]: t["status"] for t in tasks}
            for task in tasks:
                if task["status"] == "blocked" and all(status.get(d) == "done" for d in task["deps"]):
                    new = "waiting_review" if task["gate"] and not task["gate_passed"] else "queued"
                    self._status(task, new)
                    status[task["key"]] = new

    def descendants(self, job_id, key):
        tasks = self.tasks_of(job_id)
        found, frontier = [], {key}
        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task["key"] not in frontier and frontier & set(task["deps"]):
                    frontier.add(task["key"])
                    found.append(task)
                    changed = True
        return sorted(found, key=lambda t: t["position"])

    def claim(self, lanes, lease=LEASE, job_id=None):
        """The next queued task of one of `lanes` (of job_id only, if given), now running under a lease;
        None if there is none."""
        marks = ",".join("?" * len(lanes))
        only = " and job_id = ?" if job_id is not None else ""
        with self.tx():
            row = self.one(
                f"update tasks set status = 'running', lease_until = ?, updated_at = ?, done = null, total = null,"
                f" message = null, error = null where id = (select id from tasks where status = 'queued'"
                f" and lane in ({marks}){only} order by priority desc, id limit 1) returning *",
                time.time() + lease, time.time(), *lanes, *([job_id] if job_id is not None else []))
            if row is None:
                return None
            task = task_dict(row)
            self.event(task["job_id"], "task", data={"key": task["key"], "status": "running"}, task_id=task["id"])
            return task

    def heartbeat(self, task_id, lease=LEASE):
        """Renews the lease; returns True when the user asked to stop the task."""
        self.x("update tasks set lease_until = ? where id = ? and status = 'running'", time.time() + lease, task_id)
        row = self.one("select cancel_requested from tasks where id = ?", task_id)
        return bool(row and row["cancel_requested"])

    def progress(self, task, attempt_id, done, total):
        self.x("update tasks set done = ?, total = ? where id = ?", done, total, task["id"])
        self.event(task["job_id"], "progress", data={"key": task["key"], "done": done, "total": total},
                   task_id=task["id"], attempt_id=attempt_id)

    # --- attempts ---

    def start_attempt(self, task, origin="driver", fingerprint=None, pid=None, params=None):
        with self.tx():
            n = self.one("select coalesce(max(n), 0) + 1 as n from attempts where task_id = ?", task["id"])["n"]
            folder = self.job_dir(task["job_id"]) / task["key"].replace("@", "_") / str(n)
            attempt_id = self.x(
                "insert into attempts (task_id, n, span_id, origin, status, fingerprint, params, pid, dir, started_at)"
                " values (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)", task["id"], n, new_span_id(), origin, fingerprint,
                json.dumps(params if params is not None else task["params"]), pid, str(folder), time.time()).lastrowid
        (folder / "outputs").mkdir(parents=True, exist_ok=True)
        return self.attempt(attempt_id)

    def attempt(self, attempt_id):
        row = self.one("select * from attempts where id = ?", attempt_id)
        return attempt_dict(row) if row else None

    def attempts_of(self, task_id):
        return [attempt_dict(r) for r in self.q("select * from attempts where task_id = ? order by n desc", task_id)]

    def _finish_attempt(self, attempt_id, status, message=None, error=None):
        self.x("update attempts set status = ?, message = ?, error = ?, finished_at = ? where id = ?",
               status, message, error, time.time(), attempt_id)

    def complete(self, task_id, attempt_id, outputs, message=None, exports=None):
        """outputs: [{"name", "slot", "path", "sha256", "size", "keep", "external"}]."""
        with self.tx():
            task = self.task(task_id)
            for out in outputs:
                self.x("insert into artifacts (job_id, task_id, attempt_id, name, slot, path, sha256, size, keep,"
                       " external, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", task["job_id"], task_id,
                       attempt_id, out["name"], out["slot"], str(out["path"]), out.get("sha256"), out.get("size"),
                       out.get("keep", "always"), int(out.get("external", False)), time.time())
            self._finish_attempt(attempt_id, "done", message)
            self._status(task, "done", current_attempt=attempt_id, message=message, error=None, lease_until=None,
                         cancel_requested=0, retries=0)
            self.advance(task["job_id"])
        self.apply_keep(task_id)

    def fail(self, task_id, attempt_id, message, error=None):
        with self.tx():
            task = self.task(task_id)
            self._finish_attempt(attempt_id, "failed", message, error)
            self._status(task, "failed", message=message, error=error, lease_until=None, cancel_requested=0)
        self.drop_outputs(attempt_id, "success_only")

    def cancelled(self, task_id, attempt_id):
        with self.tx():
            task = self.task(task_id)
            if attempt_id:
                self._finish_attempt(attempt_id, "cancelled", "Đã dừng")
            self._status(task, "cancelled", message="Đã dừng", lease_until=None, cancel_requested=0)

    def reap(self, alive=None):
        """Running tasks whose lease ran out, or whose worker process is gone (alive(pid) is False), go back
        to queued (or failed after MAX_RETRIES, cancelled if the user asked). Returns how many."""
        count = 0
        with self.tx():
            for row in self.q("select t.*, a.id as attempt_id, a.pid as pid from tasks t left join attempts a"
                              " on a.task_id = t.id and a.status = 'running' where t.status = 'running'"):
                expired = (row["lease_until"] or 0) < time.time()
                gone = alive is not None and row["pid"] and not alive(row["pid"])
                if not (expired or gone):
                    continue
                task = task_dict(row)
                if row["attempt_id"]:
                    self._finish_attempt(row["attempt_id"], "failed", "Worker dừng đột ngột", "lost")
                if task["cancel_requested"]:
                    self._status(task, "cancelled", message="Đã dừng", lease_until=None, cancel_requested=0)
                elif task["retries"] < MAX_RETRIES:
                    self._status(task, "queued", retries=task["retries"] + 1, lease_until=None,
                                 message="Chạy lại sau khi worker dừng đột ngột")
                else:
                    self._status(task, "failed", lease_until=None,
                                 message="Worker dừng đột ngột nhiều lần (thiếu RAM?)")
                count += 1
        return count

    # --- user actions ---

    def cancel_job(self, job_id):
        """Waiting tasks are cancelled; running ones are flagged, their worker stops them."""
        with self.tx():
            for task in self.tasks_of(job_id):
                if task["status"] in ("queued", "blocked", "waiting_review"):
                    self._status(task, "cancelled", message="Đã dừng")
                elif task["status"] == "running":
                    self.x("update tasks set cancel_requested = 1 where id = ?", task["id"])

    def resume(self, job_id):
        """Continue a job: past its gates, and again from cancelled or failed tasks."""
        with self.tx():
            for task in self.tasks_of(job_id):
                if task["status"] == "waiting_review":
                    self._status(task, "queued", gate_passed=1)
                elif task["status"] in ("cancelled", "failed"):
                    self._status(task, "blocked", gate_passed=1, retries=0, message=None, error=None)
            self.advance(job_id)

    def refresh(self, job_id):
        """Run the stale tasks again with their new inputs."""
        with self.tx():
            for task in self.tasks_of(job_id):
                if task["status"] == "stale":
                    self._status(task, "blocked", message=None)
            self.advance(job_id)

    def human_conflicts(self, tasks):
        keys = []
        for task in tasks:
            if task["current_attempt"]:
                attempt = self.attempt(task["current_attempt"])
                if attempt and attempt["origin"] == "human":
                    keys.append(task["key"])
        return keys

    def rerun(self, task_id, mode="only", params=None, driver=None, confirm=False):
        """Run a task again (with new params / driver). mode "only": tasks after it that were done become
        stale; "cascade": they run again too. Raises NeedsConfirm when that replaces edits made by hand."""
        from dich import drivers
        with self.tx():
            task = self.task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task["status"] == "skipped":
                raise Busy("Bước này không chạy được cho job này")
            later = self.descendants(task["job_id"], task["key"])
            if task["status"] == "running" or (mode == "cascade" and any(t["status"] == "running" for t in later)):
                raise Busy("Bước này hoặc bước sau đang chạy")
            if not confirm:
                conflicts = self.human_conflicts([task] + (later if mode == "cascade" else []))
                if conflicts:
                    raise NeedsConfirm(conflicts)
            driver = driver or task["driver"]
            cls = drivers.get(task["stage"], driver)
            base = task["params"] if driver == task["driver"] else {}
            options = cls.Params(**{**base, **(params or {})}).model_dump(mode="json")
            self.x("update tasks set driver = ?, params = ?, lane = ?, retries = 0 where id = ?",
                   driver, json.dumps(options), drivers.lane(cls), task_id)
            self._status(task, "blocked", gate_passed=1, message=None, error=None, done=None, total=None)
            for other in later:
                if other["status"] == "skipped":
                    continue
                if mode == "cascade":
                    self._status(other, "blocked", message=None, error=None)
                elif other["status"] == "done":
                    self._status(other, "stale", message="Đầu vào đã đổi")
            self.advance(task["job_id"])

    def save_human(self, task_id, name, path, sha256=None, size=None):
        """An edit by hand of a task's output `name`: a new attempt (origin human) that becomes current;
        tasks after it that were done become stale."""
        with self.tx():
            task = self.task(task_id)
            if task["status"] == "running":
                raise Busy("Bước này đang chạy")
            attempt = self.start_attempt(task, origin="human", params={})
            slot = task["outputs"][name]
            current = self.artifacts_of(task["current_attempt"]) if task["current_attempt"] else []
            outputs = [a for a in current if a["name"] != name]   # other outputs carry over
            outputs.append({"name": name, "slot": slot, "path": str(path), "sha256": sha256, "size": size})
            for out in outputs:
                self.x("insert into artifacts (job_id, task_id, attempt_id, name, slot, path, sha256, size, keep,"
                       " external, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", task["job_id"], task_id,
                       attempt["id"], out["name"], out["slot"], str(out["path"]), out.get("sha256"), out.get("size"),
                       "always", 1 if out is not outputs[-1] else 0, time.time())
            self._finish_attempt(attempt["id"], "done", "Sửa tay")
            self.x("update tasks set current_attempt = ?, updated_at = ? where id = ?", attempt["id"], time.time(),
                   task_id)
            self.event(task["job_id"], "task", data={"key": task["key"], "status": task["status"], "edited": True},
                       task_id=task_id)
            for other in self.descendants(task["job_id"], task["key"]):
                if other["status"] == "done":
                    self._status(other, "stale", message="Đầu vào đã sửa tay")
        return attempt

    def set_exports(self, task_id, paths):
        self.x("update tasks set exports = ? where id = ?", json.dumps([str(p) for p in paths]), task_id)

    # --- artifacts ---

    def artifacts_of(self, attempt_id):
        return [artifact_dict(r) for r in self.q("select * from artifacts where attempt_id = ? and deleted = 0",
                                                 attempt_id)]

    def current_artifacts(self, job_id=None):
        """task id → artifacts of its current attempt, for every task (of job_id)."""
        sql = ("select a.* from artifacts a join tasks t on a.attempt_id = t.current_attempt where a.deleted = 0"
               + (" and t.job_id = ?" if job_id is not None else ""))
        found = {}
        for row in self.q(sql, *([job_id] if job_id is not None else [])):
            found.setdefault(row["task_id"], []).append(artifact_dict(row))
        return found

    def jobs_of_source(self, source):
        return [r["id"] for r in self.q("select id from jobs where source = ?", str(source))]

    def artifact(self, artifact_id):
        row = self.one("select * from artifacts where id = ?", artifact_id)
        return artifact_dict(row) if row else None

    def output_of(self, task, slot):
        """The current artifact of `task` for `slot`, or None."""
        if not task or not task["current_attempt"]:
            return None
        row = self.one("select * from artifacts where attempt_id = ? and slot = ? and deleted = 0",
                       task["current_attempt"], slot)
        return artifact_dict(row) if row else None

    def slot_artifact(self, job_id, slot):
        """The job's current result for slot: the last task producing it that has one."""
        for task in reversed(self.tasks_of(job_id)):
            if slot in task["outputs"].values():
                found = self.output_of(task, slot)
                if found:
                    return task, found
        return None, None

    def inputs_of(self, task):
        """name → artifact for the task's inputs (None when the producer has no current output)."""
        return {name: self.output_of(self.task_by_key(task["job_id"], key), slot)
                for name, (key, slot) in task["inputs"].items()}

    def find_cached(self, fingerprint):
        """Artifacts of a finished attempt with this fingerprint whose files all still exist, or None."""
        for row in self.q("select id from attempts where fingerprint = ? and status = 'done' order by id desc",
                          fingerprint):
            artifacts = self.artifacts_of(row["id"])
            if artifacts and all(Path(a["path"]).exists() for a in artifacts):
                return artifacts
        return None

    def drop_outputs(self, attempt_id, policy):
        for art in self.artifacts_of(attempt_id):
            if art["keep"] == policy and not art["external"]:
                Path(art["path"]).unlink(missing_ok=True)
                self.x("update artifacts set deleted = 1 where id = ?", art["id"])
        if policy == "success_only":
            attempt = self.attempt(attempt_id)
            if attempt:
                shutil.rmtree(Path(attempt["dir"]) / "outputs", ignore_errors=True)

    def apply_keep(self, task_id):
        """keep "last": outputs of older attempts are deleted once a newer one is done."""
        task = self.task(task_id)
        for attempt in self.attempts_of(task_id):
            if attempt["id"] != task["current_attempt"]:
                self.drop_outputs(attempt["id"], "last")

    # --- calls: short requests answered by a worker (voice preview, voice list) ---

    def call(self, kind, args):
        with self.tx():
            return self.x("insert into calls (kind, args, status, created_at) values (?, ?, 'queued', ?)",
                          kind, json.dumps(args, ensure_ascii=False), time.time()).lastrowid

    def claim_call(self):
        with self.tx():
            row = self.one("update calls set status = 'running' where id = (select id from calls where"
                           " status = 'queued' order by id limit 1) returning *")
            return dict(row, args=loads(row["args"], {})) if row else None

    def answer_call(self, call_id, result=None, error=None):
        self.x("update calls set status = ?, result = ?, error = ? where id = ?", "failed" if error else "done",
               json.dumps(result, ensure_ascii=False) if result is not None else None, error, call_id)

    def wait_call(self, call_id, timeout=120.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.one("select * from calls where id = ?", call_id)
            if row["status"] in ("done", "failed"):
                self.x("delete from calls where id = ? or created_at < ?", call_id, time.time() - 3600)
                return loads(row["result"]), row["error"]
            time.sleep(0.1)
        self.x("delete from calls where id = ?", call_id)
        return None, "Hết thời gian chờ (worker đang bận?)"


def task_dict(row):
    keys = row.keys()
    return {
        "id": row["id"], "job_id": row["job_id"], "key": row["key"], "position": row["position"],
        "stage": row["stage"], "driver": row["driver"], "lang": row["lang"], "params": loads(row["params"], {}),
        "inputs": loads(row["inputs"], {}), "outputs": loads(row["outputs"], {}), "deps": loads(row["deps"], []),
        "lane": row["lane"], "gate": bool(row["gate"]), "gate_passed": bool(row["gate_passed"]),
        "status": row["status"], "message": row["message"], "error": row["error"], "done": row["done"],
        "total": row["total"], "retries": row["retries"], "cancel_requested": bool(row["cancel_requested"]),
        "current_attempt": row["current_attempt"], "exports": loads(row["exports"], []),
        "updated_at": row["updated_at"], "lease_until": row["lease_until"] if "lease_until" in keys else None,
    }


def job_status(tasks):
    statuses = {t["status"] for t in tasks}
    for status in JOB_STATUS_ORDER:
        if status in statuses:
            return status
    return "done"


def job_dict(row, tasks):
    return {"id": row["id"], "title": row["title"], "source": row["source"], "template": row["template"],
            "langs": loads(row["langs"], []), "export_dir": row["export_dir"], "trace_id": row["trace_id"],
            "created_at": row["created_at"], "status": job_status(tasks), "tasks": tasks}


def attempt_dict(row):
    return {"id": row["id"], "task_id": row["task_id"], "n": row["n"], "span_id": row["span_id"],
            "origin": row["origin"], "status": row["status"], "fingerprint": row["fingerprint"],
            "params": loads(row["params"], {}), "pid": row["pid"], "dir": row["dir"], "message": row["message"],
            "error": row["error"], "started_at": row["started_at"], "finished_at": row["finished_at"]}


def artifact_dict(row):
    return Artifact({"id": row["id"], "job_id": row["job_id"], "task_id": row["task_id"], "attempt_id": row["attempt_id"],
            "name": row["name"], "slot": row["slot"], "path": row["path"], "sha256": row["sha256"],
            "size": row["size"], "keep": row["keep"], "external": bool(row["external"])})


def event_dict(row):
    return {"id": row["id"], "job_id": row["job_id"], "task_id": row["task_id"], "attempt_id": row["attempt_id"],
            "ts": row["ts"], "level": row["level"], "type": row["type"], "message": row["message"],
            "data": loads(row["data"])}
