# Jobs, tasks, attempts, artifacts and events in one SQLite file (WAL), shared by the API process and
# the worker processes; each process opens its own connection.
#
# Task status: blocked → queued → running → done | failed | cancelled, plus
#   skipped         the stage can't or needn't run for this job (unsupported language, input from the source)
#   waiting_review  a gate: the job waits for the user before this task
#   stale           done, but an input changed since (rerun "only", or an edit by hand before it)
# fresh: the next attempt ignores the cached result, the checkpoint and the driver's own caches (rerun
# with fresh); cleared when that attempt starts, so a retry after a crash goes on from it.
# A worker claims a queued task atomically and holds a lease it renews; a task whose lease runs out
# (worker crashed, killed) goes back to queued, at most MAX_RETRIES times.
import base64
import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dich import settings
from dich.core.ids import new_id
from dich.core.text import fold, natural_key
from dich.logging.trace import new_span_id, new_trace_id
from dich.queue.base import Busy, NeedsConfirm
from dich.storage.artifacts import Artifact

SCHEMA = """
create table if not exists jobs (
    id text primary key,
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
    id text primary key,
    job_id text not null references jobs(id) on delete cascade,
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
    fresh integer not null default 0,
    current_attempt text,
    exports text not null default '[]',
    updated_at real not null,
    unique (job_id, key)
);
create index if not exists tasks_claim on tasks(status, lane, priority, id);
create table if not exists attempts (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
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
    id text primary key,
    job_id text not null references jobs(id) on delete cascade,
    task_id text not null references tasks(id) on delete cascade,
    attempt_id text not null references attempts(id) on delete cascade,
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
    job_id text,
    task_id text,
    attempt_id text,
    ts real not null,
    level text not null,
    type text not null,
    message text,
    data text
);
create index if not exists events_job on events(job_id, id);
create table if not exists projects (
    id text primary key,
    name text not null,
    color text not null,
    template text not null,
    langs text not null,
    options text not null default '{}',
    preset text,
    export_dir text,
    created_at real not null
);
create table if not exists voices (
    id text primary key,
    name text not null,
    project_id text references projects(id) on delete set null,
    path text not null,
    origin text,
    clip_start real,
    clip_end real,
    created_at real not null
);
create table if not exists calls (
    id text primary key,
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
# A job's status is not stored: it is the first of JOB_STATUS_ORDER among its tasks' (else done). In SQL, its
# rank: the index in JOB_STATUS_ORDER, len(JOB_STATUS_ORDER) for done.
JOB_RANK = ("coalesce((select min(case t.status " + " ".join(f"when '{s}' then {i}" for i, s in enumerate(JOB_STATUS_ORDER))
            + f" end) from tasks t where t.job_id = j.id), {len(JOB_STATUS_ORDER)})")
# The groups the job list is filtered by
STATUS_GROUPS = {"attention": ("failed", "waiting_review", "stale"), "active": ("running", "queued", "blocked"),
                 "stopped": ("cancelled",), "done": ("done",)}
GROUP_RANKS = {group: [(JOB_STATUS_ORDER + ("done",)).index(s) for s in statuses]
               for group, statuses in STATUS_GROUPS.items()}
BILLED = "type = 'log' and data like '%cost_usd%' and json_extract(data, '$.billed') is not 0"   # events
JOB_COSTS = f"select job_id, sum(json_extract(data, '$.cost_usd')) as cost from events where {BILLED}"
# sort → (key, direction); ties are broken by id in the same direction, so the order is total
JOB_SORTS = {"created_desc": ("j.created_at", "desc"), "created_asc": ("j.created_at", "asc"),
             "title": ("natural_key(j.title)", "asc"), "updated_desc": ("j.updated_at", "desc"),
             "cost_desc": ("coalesce(c.cost, -1)", "desc")}
PAUSABLE = ("exists (select 1 from tasks t where t.job_id = j.id and (t.status in ('queued', 'blocked')"
            " or (t.status = 'running' and t.cancel_requested = 0)))")
RESUMABLE = ("exists (select 1 from tasks t where t.job_id = j.id and (t.status = 'cancelled'"
             " or (t.status = 'running' and t.cancel_requested = 1)))")


def loads(value, default=None):
    return json.loads(value) if value else default


class Store:
    def __init__(self, path=None, data_dir=None):
        path = Path(path or settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.data_dir = Path(data_dir or path.parent)
        self._retire_numeric_ids()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("fold", 1, fold, deterministic=True)
        self.db.create_function("natural_key", 1, natural_key, deterministic=True)
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("pragma journal_mode=wal")
            self.db.execute("pragma foreign_keys=on")
            self.db.execute("pragma synchronous=normal")
            self.db.executescript(SCHEMA)
            if not any(c[1] == "project_id" for c in self.db.execute("pragma table_info(jobs)")):
                self.db.execute("alter table jobs add column project_id text references projects(id) on delete set null")
            if not any(c[1] == "fresh" for c in self.db.execute("pragma table_info(tasks)")):
                self.db.execute("alter table tasks add column fresh integer not null default 0")
            if not any(c[1] == "updated_at" for c in self.db.execute("pragma table_info(jobs)")):
                self.db.execute("alter table jobs add column updated_at real")
                self.db.execute(
                    "update jobs set updated_at = max(created_at, coalesce((select max(max(a.started_at,"
                    " coalesce(a.finished_at, 0))) from attempts a join tasks t on a.task_id = t.id"
                    " where t.job_id = jobs.id), 0))")

    def _retire_numeric_ids(self):
        """A database from before job_…/task_… ids (integer ids) is set aside as *.old, with its job folders."""
        if not self.path.exists():
            return
        db = sqlite3.connect(str(self.path))
        try:
            numeric = any(c[1] == "id" and c[2].lower() == "integer" for c in db.execute("pragma table_info(jobs)"))
        finally:
            db.close()
        if not numeric:
            return
        for suffix in ("", "-wal", "-shm"):
            old = Path(f"{self.path}{suffix}")
            if old.exists():
                old.replace(f"{old}.old")
        jobs = self.data_dir / "jobs"
        if jobs.exists():
            shutil.rmtree(jobs.with_name("jobs.old"), ignore_errors=True)
            jobs.replace(jobs.with_name("jobs.old"))
        print(f"Cơ sở dữ liệu cũ (id dạng số) đã được cất sang {self.path}.old — bắt đầu dữ liệu mới")

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

    def create_job(self, title, source, template, langs, specs, overrides=None, export_dir=None, project_id=None):
        with self.tx():
            job_id = new_id("job")
            now = time.time()
            self.x("insert into jobs (id, title, source, template, langs, overrides, export_dir, trace_id, created_at,"
                   " project_id, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", job_id, title, str(source),
                   template, json.dumps(langs), json.dumps(overrides or {}), str(export_dir) if export_dir else None,
                   new_trace_id(), now, project_id, now)
            self._insert_tasks(job_id, specs)
            self.event(job_id, "job", data={"created": True})
            self.advance(job_id)
        return job_id

    def _insert_tasks(self, job_id, specs, start=0):
        for position, spec in enumerate(specs, start):
            self.x("insert into tasks (id, job_id, key, position, stage, driver, lang, params, inputs, outputs, deps,"
                   " lane, gate, status, message, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   new_id("task"), job_id, spec.key, position, spec.stage, spec.driver, spec.lang, json.dumps(spec.params),
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
            self.x("update jobs set langs = ?, updated_at = ? where id = ?", json.dumps(langs), time.time(), job_id)
            self.event(job_id, "job", data={"langs": langs})
            self.advance(job_id)
        return [s.key for s in new]

    def touch(self, job_id):
        """The job changed (an attempt started or ended, it was renamed, moved, edited, run again)."""
        self.x("update jobs set updated_at = ? where id = ?", time.time(), job_id)

    def set_title(self, job_id, title):
        self.x("update jobs set title = ?, updated_at = ? where id = ?", title, time.time(), job_id)
        self.event(job_id, "job", data={"title": title})

    def set_auto_title(self, job_id, title):
        """A title found by a driver (the page's title): only while the job is still named after its URL,
        so a title the user gave stays."""
        if self.x("update jobs set title = ?, updated_at = ? where id = ? and title = source", title, time.time(),
                  job_id).rowcount:
            self.event(job_id, "job", data={"title": title})

    def jobs(self):
        return self._with_tasks(self.q("select * from jobs order by rowid desc"))

    def _with_tasks(self, rows):
        """Job dicts of job rows, with their tasks and costs."""
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        tasks = {}
        for t in self.q(f"select * from tasks where job_id in ({marks}) order by job_id, position", *ids):
            tasks.setdefault(t["job_id"], []).append(task_dict(t))
        costs = {r["job_id"]: r["cost"] for r in self.q(f"{JOB_COSTS} and job_id in ({marks}) group by job_id", *ids)
                 if r["cost"] is not None}
        return [job_dict(r, tasks.get(r["id"], []), costs.get(r["id"])) for r in rows]

    @staticmethod
    def _job_filter(project=None, status=None, q=None):
        """SQL conditions (on jobs j) and their args. project: an id, "none" (no project) or None (any);
        status: a key of STATUS_GROUPS; q: in the title or the source, without marks and case."""
        where, args = [], []
        if project == "none":
            where.append("j.project_id is null")
        elif project:
            where.append("j.project_id = ?")
            args.append(project)
        if status:
            where.append(f"{JOB_RANK} in ({','.join(map(str, GROUP_RANKS[status]))})")
        needle = fold(" ".join((q or "").split()))
        if needle:
            where.append("(instr(fold(j.title), ?) > 0 or instr(fold(j.source), ?) > 0)")
            args += [needle, needle]
        return where, args

    def jobs_page(self, project=None, status=None, q=None, sort="created_desc", cursor=None, limit=50):
        """(jobs, next cursor or None): one page of the jobs that match, in `sort` order. The cursor is the
        sort key and id of the last job given (keyset), so jobs added meanwhile shift nothing.
        Raises ValueError for a cursor that is not one this gave for `sort`."""
        key, direction = JOB_SORTS[sort]
        where, args = self._job_filter(project, status, q)
        if cursor:
            after_key, after_id = decode_cursor(cursor, sort)
            op = "<" if direction == "desc" else ">"
            where.append(f"({key} {op} ? or ({key} = ? and j.id {op} ?))")
            args += [after_key, after_key, after_id]
        costs = f" left join ({JOB_COSTS} group by job_id) c on c.job_id = j.id" if "c.cost" in key else ""
        rows = self.q(f"select j.*, {key} as sort_key from jobs j{costs}"
                      + (f" where {' and '.join(where)}" if where else "")
                      + f" order by sort_key {direction}, j.id {direction} limit ?", *args, limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(sort, rows[-1]["sort_key"], rows[-1]["id"]) if more else None
        return self._with_tasks(rows), next_cursor

    def job_ids(self, project=None, status=None, q=None):
        """Every job that matches, newest first."""
        where, args = self._job_filter(project, status, q)
        return [r["id"] for r in self.q("select j.id from jobs j" + (f" where {' and '.join(where)}" if where else "")
                                        + " order by j.created_at desc, j.id desc", *args)]

    def job_counts(self, project=None, q=None, status=None):
        """Numbers about the jobs: of those matching project and q, how many there are and in each status
        group, and (also matching status) how many can be paused or continued; of every job, how many are
        active and in each project ("none": no project), how many tasks the workers run or wait for, and
        whether any job paid for an API."""
        where, args = self._job_filter(project, None, q)
        cond = f" where {' and '.join(where)}" if where else ""
        ranks = {r["rank"]: r["n"] for r in self.q(f"select {JOB_RANK} as rank, count(*) as n from jobs j{cond}"
                                                   " group by rank", *args)}
        groups = {group: sum(ranks.get(r, 0) for r in found) for group, found in GROUP_RANKS.items()}
        where, args = self._job_filter(project, status, q)
        cond = f" where {' and '.join(where)}" if where else ""
        actions = self.one(f"select coalesce(sum({PAUSABLE}), 0) as pausable, coalesce(sum({RESUMABLE}), 0) as resumable"
                           f" from jobs j{cond}", *args)
        projects = {r["project_id"] or "none": r["n"]
                    for r in self.q("select project_id, count(*) as n from jobs group by project_id")}
        active = self.one(f"select count(*) as n from jobs j where {JOB_RANK} in"
                          f" ({','.join(map(str, GROUP_RANKS['active']))})")["n"]
        return {"total": sum(ranks.values()), "status": groups, "pausable": actions["pausable"],
                "resumable": actions["resumable"], "all": sum(projects.values()), "active": active,
                "projects": projects,
                "queue": self.one("select count(*) as n from tasks where status in ('running', 'queued')")["n"],
                "has_cost": bool(self.one(f"select 1 from events where {BILLED} and json_extract(data, '$.cost_usd') > 0 limit 1"))}

    def job(self, job_id):
        row = self.one("select * from jobs where id = ?", job_id)
        if not row:
            return None
        return job_dict(row, [task_dict(t) for t in self.q("select * from tasks where job_id = ? order by position",
                                                           job_id)], self.job_costs(job_id).get(job_id))

    def job_costs(self, job_id=None):
        """job id → USD paid for paid APIs, over every attempt (failed ones and reruns too): the sum of
        the cost_usd that drivers log with their usage summary."""
        rows = self.q(JOB_COSTS + (" and job_id = ?" if job_id else "") + " group by job_id", *([job_id] if job_id else []))
        return {r["job_id"]: r["cost"] for r in rows if r["cost"] is not None}

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

    def set_job_project(self, job_id, project_id):
        self.x("update jobs set project_id = ?, updated_at = ? where id = ?", project_id, time.time(), job_id)
        self.event(job_id, "job", data={"project_id": project_id})

    def job_dir(self, job_id):
        return self.data_dir / "jobs" / str(job_id)

    # --- projects ---

    def projects(self):
        counts = {r["project_id"]: r["n"] for r in self.q("select project_id, count(*) as n from jobs group by project_id")}
        return [project_dict(r, counts.get(r["id"], 0)) for r in self.q("select * from projects order by created_at")]

    def project(self, project_id):
        row = self.one("select * from projects where id = ?", project_id)
        if not row:
            return None
        return project_dict(row, self.one("select count(*) as n from jobs where project_id = ?", project_id)["n"])

    def create_project(self, fields):
        project_id = new_id("prj")
        self.x("insert into projects (id, name, color, template, langs, options, preset, export_dir, created_at)"
               " values (?, ?, ?, ?, ?, ?, ?, ?, ?)", project_id, *project_row(fields), time.time())
        self.event(None, "project", data={"id": project_id})
        return project_id

    def update_project(self, project_id, fields):
        self.x("update projects set name = ?, color = ?, template = ?, langs = ?, options = ?, preset = ?,"
               " export_dir = ? where id = ?", *project_row(fields), project_id)
        self.event(None, "project", data={"id": project_id})

    def delete_project(self, project_id):
        """Its jobs stay, as jobs of no project."""
        with self.tx():
            self.x("update jobs set project_id = null where project_id = ?", project_id)
            self.x("delete from projects where id = ?", project_id)
            self.event(None, "project", data={"id": project_id, "deleted": True})

    # --- voices: named clips to clone a dub voice from, of every project (project_id null) or one ---

    def voices(self, project_id=None):
        """Every voice; with project_id, that project's first, then the shared ones."""
        if project_id:
            rows = self.q("select * from voices where project_id = ? or project_id is null"
                          " order by project_id is null, created_at", project_id)
        else:
            rows = self.q("select * from voices order by created_at")
        return [voice_dict(r) for r in rows]

    def voice(self, voice_id):
        row = self.one("select * from voices where id = ?", voice_id)
        return voice_dict(row) if row else None

    def create_voice(self, voice_id, name, path, project_id=None, origin=None, start=None, end=None):
        self.x("insert into voices (id, name, project_id, path, origin, clip_start, clip_end, created_at)"
               " values (?, ?, ?, ?, ?, ?, ?, ?)", voice_id, name, project_id, str(path), origin, start, end, time.time())
        self.event(None, "voice", data={"id": voice_id})
        return voice_id

    def update_voice(self, voice_id, name, project_id):
        self.x("update voices set name = ?, project_id = ? where id = ?", name, project_id, voice_id)
        self.event(None, "voice", data={"id": voice_id})

    def delete_voice(self, voice_id):
        """The row; the caller removes the file."""
        self.x("delete from voices where id = ?", voice_id)
        self.event(None, "voice", data={"id": voice_id, "deleted": True})

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
                f" and lane in ({marks}){only} order by priority desc, rowid limit 1) returning *",
                time.time() + lease, time.time(), *lanes, *([job_id] if job_id is not None else []))
            if row is None:
                return None
            task = task_dict(row)
            self.event(task["job_id"], "task", data={"key": task["key"], "status": "running"}, task_id=task["id"])
            return task

    def queue(self):
        """The running and queued tasks, in the order workers take them (running first), with their job's
        title and, when running, the attempt (start time, worker pid); plus how many wait on something else."""
        rows = self.q(
            "select t.id, t.job_id, j.title, j.project_id, t.key, t.stage, t.driver, t.lang, t.lane, t.status,"
            " t.done, t.total, t.retries, t.cancel_requested, t.message, t.updated_at, a.started_at, a.pid"
            " from tasks t join jobs j on j.id = t.job_id"
            " left join attempts a on a.task_id = t.id and a.status = 'running'"
            " where t.status in ('running', 'queued')"
            " order by t.status = 'queued', t.priority desc, t.rowid")
        counts = {r["status"]: r["n"] for r in self.q(
            "select status, count(*) as n from tasks where status in ('blocked', 'waiting_review') group by status")}
        items = [{**dict(r), "cancel_requested": bool(r["cancel_requested"])} for r in rows]
        return items, counts.get("blocked", 0), counts.get("waiting_review", 0)

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
            attempt_id = new_id("att")
            self.x(
                "insert into attempts (id, task_id, n, span_id, origin, status, fingerprint, params, pid, dir, started_at)"
                " values (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)", attempt_id, task["id"], n, new_span_id(), origin, fingerprint,
                json.dumps(params if params is not None else task["params"]), pid, str(folder), time.time())
            self.x("update tasks set fresh = 0 where id = ?", task["id"])
            self.touch(task["job_id"])
        (folder / "outputs").mkdir(parents=True, exist_ok=True)
        return self.attempt(attempt_id)

    def attempt(self, attempt_id):
        row = self.one("select * from attempts where id = ?", attempt_id)
        return attempt_dict(row) if row else None

    def attempts_of(self, task_id):
        return [attempt_dict(r) for r in self.q("select * from attempts where task_id = ? order by n desc", task_id)]

    def _finish_attempt(self, attempt_id, status, message=None, error=None):
        now = time.time()
        self.x("update attempts set status = ?, message = ?, error = ?, finished_at = ? where id = ?",
               status, message, error, now, attempt_id)
        self.x("update jobs set updated_at = ? where id = (select t.job_id from attempts a join tasks t"
               " on a.task_id = t.id where a.id = ?)", now, attempt_id)

    def complete(self, task_id, attempt_id, outputs, message=None, exports=None):
        """outputs: [{"name", "slot", "path", "sha256", "size", "keep", "external"}]."""
        with self.tx():
            task = self.task(task_id)
            for out in outputs:
                self.x("insert into artifacts (id, job_id, task_id, attempt_id, name, slot, path, sha256, size, keep,"
                       " external, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", new_id("art"),
                       task["job_id"], task_id,
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

    def pause_jobs(self, job_ids):
        """Pause many jobs: like cancel_job, but a job waiting for review keeps waiting (it uses nothing).
        Returns the ids of the jobs that had something to pause."""
        paused = []
        with self.tx():
            for job_id in job_ids:
                hit = False
                for task in self.tasks_of(job_id):
                    if task["status"] in ("queued", "blocked"):
                        self._status(task, "cancelled", message="Tạm dừng")
                        hit = True
                    elif task["status"] == "running" and not task["cancel_requested"]:
                        self.x("update tasks set cancel_requested = 1 where id = ?", task["id"])
                        hit = True
                if hit:
                    paused.append(job_id)
        return paused

    def unpause_jobs(self, job_ids):
        """Continue many jobs from their stopped tasks. Unlike resume, review gates and failed tasks are
        left alone: those need the user to look at each job. Returns the ids of the jobs continued."""
        resumed = []
        with self.tx():
            for job_id in job_ids:
                tasks = self.tasks_of(job_id)
                stopped = [t for t in tasks if t["status"] == "cancelled"]
                for task in stopped:
                    self._status(task, "blocked", retries=0, message=None, error=None)
                # paused a moment ago and its worker has not stopped it yet: let it run on
                stopping = [t for t in tasks if t["status"] == "running" and t["cancel_requested"]]
                for task in stopping:
                    self.x("update tasks set cancel_requested = 0 where id = ?", task["id"])
                if stopped or stopping:
                    self.advance(job_id)
                    resumed.append(job_id)
        return resumed

    def refresh(self, job_id):
        """Run the stale tasks again with their new inputs."""
        with self.tx():
            for task in self.tasks_of(job_id):
                if task["status"] == "stale":
                    self._status(task, "blocked", message=None)
            self.touch(job_id)
            self.advance(job_id)

    def human_conflicts(self, tasks):
        keys = []
        for task in tasks:
            if task["current_attempt"]:
                attempt = self.attempt(task["current_attempt"])
                if attempt and attempt["origin"] == "human":
                    keys.append(task["key"])
        return keys

    def rerun(self, task_id, mode="only", params=None, driver=None, confirm=False, fresh=False):
        """Run a task again (with new params / driver). mode "only": tasks after it that were done become
        stale; "cascade": they run again too. fresh: this task computes from scratch, no cache; the tasks
        after it reuse theirs when their inputs come out the same. Raises NeedsConfirm when that replaces
        edits made by hand."""
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
            self.x("update tasks set driver = ?, params = ?, lane = ?, retries = 0, fresh = ? where id = ?",
                   driver, json.dumps(options), drivers.lane(cls), int(fresh), task_id)
            self._status(task, "blocked", gate_passed=1, message=None, error=None, done=None, total=None)
            for other in later:
                if other["status"] == "skipped":
                    continue
                if mode == "cascade":
                    self._status(other, "blocked", message=None, error=None)
                elif other["status"] == "done":
                    self._status(other, "stale", message="Đầu vào đã đổi")
            self.touch(task["job_id"])
            self.advance(task["job_id"])

    def save_human(self, task_id, name, path, sha256=None, size=None):
        """An edit by hand of a task's output `name`: a new attempt (origin human) that becomes current;
        tasks after it that were done become stale. A stale, failed, cancelled or gated task is done with
        the edit as its result, so the steps after it can run without running it again (and losing the edit)."""
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
                self.x("insert into artifacts (id, job_id, task_id, attempt_id, name, slot, path, sha256, size, keep,"
                       " external, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", new_id("art"),
                       task["job_id"], task_id,
                       attempt["id"], out["name"], out["slot"], str(out["path"]), out.get("sha256"), out.get("size"),
                       "always", 1 if out is not outputs[-1] else 0, time.time())
            self._finish_attempt(attempt["id"], "done", "Sửa tay")
            self.x("update tasks set current_attempt = ?, updated_at = ? where id = ?", attempt["id"], time.time(),
                   task_id)
            settled = task["status"] in ("done", "stale", "failed", "cancelled", "waiting_review")
            if settled and task["status"] != "done":
                self._status(task, "done", gate_passed=1, message="Sửa tay", error=None, retries=0)
            else:
                self.event(task["job_id"], "task", data={"key": task["key"], "status": task["status"], "edited": True},
                           task_id=task_id)
            # blocked steps after a done one only wait for "Cập nhật phía sau", like the done ones
            for other in self.descendants(task["job_id"], task["key"]):
                if other["status"] == "done" or (settled and other["status"] == "blocked"):
                    self._status(other, "stale", message="Đầu vào đã sửa tay")
        return attempt

    def set_exports(self, task_id, paths):
        self.x("update tasks set exports = ? where id = ?", json.dumps([str(p) for p in paths]), task_id)

    # --- artifacts ---

    def artifacts_of(self, attempt_id):
        return [artifact_dict(r) for r in self.q("select * from artifacts where attempt_id = ? and deleted = 0",
                                                 attempt_id)]

    def current_artifacts(self, job_ids=None):
        """task id → artifacts of its current attempt, for every task (of the jobs job_ids)."""
        job_ids = list(job_ids) if job_ids is not None else None
        sql = ("select a.* from artifacts a join tasks t on a.attempt_id = t.current_attempt where a.deleted = 0"
               + (f" and t.job_id in ({','.join('?' * len(job_ids))})" if job_ids is not None else ""))
        found = {}
        for row in self.q(sql, *(job_ids or [])):
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
        for row in self.q("select id from attempts where fingerprint = ? and status = 'done' order by rowid desc",
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
        """keep "last": outputs of older attempts are deleted once a newer one is done — at once when
        KEEP_DAYS is 0, otherwise by prune_old after KEEP_DAYS days."""
        if settings.KEEP_DAYS > 0:
            return
        task = self.task(task_id)
        for attempt in self.attempts_of(task_id):
            if attempt["id"] != task["current_attempt"]:
                self.drop_outputs(attempt["id"], "last")

    def prune_old(self, days=None):
        """Delete keep "last" outputs of attempts that are no longer current and finished over `days` ago."""
        days = settings.KEEP_DAYS if days is None else days
        rows = self.q("select distinct a.attempt_id from artifacts a join attempts t on a.attempt_id = t.id"
                      " join tasks k on a.task_id = k.id where a.keep = 'last' and a.deleted = 0"
                      " and a.external = 0 and a.attempt_id != coalesce(k.current_attempt, '')"
                      " and coalesce(t.finished_at, t.started_at) < ?", time.time() - days * 86400)
        for row in rows:
            self.drop_outputs(row["attempt_id"], "last")
        return len(rows)

    def use_attempt(self, task_id, attempt_id):
        """An earlier done attempt becomes the task's result again; tasks after it that were done become stale."""
        with self.tx():
            task = self.task(task_id)
            attempt = self.attempt(attempt_id)
            if task is None or attempt is None or attempt["task_id"] != task_id:
                raise KeyError(attempt_id)
            if task["status"] == "running":
                raise Busy("Bước này đang chạy")
            if attempt["status"] != "done":
                raise Busy("Lần chạy này chưa xong")
            artifacts = self.artifacts_of(attempt_id)
            if not artifacts or not all(Path(a["path"]).exists() for a in artifacts):
                raise Busy("File của lần chạy này không còn đủ")
            if attempt_id == task["current_attempt"] and task["status"] == "done":
                return attempt
            self.x("update tasks set current_attempt = ? where id = ?", attempt_id, task_id)
            self._status(task, "done", gate_passed=1, message=f"Dùng lần {attempt['n']}", error=None, retries=0)
            for other in self.descendants(task["job_id"], task["key"]):
                if other["status"] in ("done", "blocked"):
                    self._status(other, "stale", message="Đầu vào đã đổi")
            self.advance(task["job_id"])
        return attempt

    # --- calls: short requests answered by a worker (voice preview, voice list) ---

    def call(self, kind, args):
        with self.tx():
            call_id = new_id("call")
            self.x("insert into calls (id, kind, args, status, created_at) values (?, ?, ?, 'queued', ?)",
                   call_id, kind, json.dumps(args, ensure_ascii=False), time.time())
            return call_id

    def claim_call(self):
        with self.tx():
            row = self.one("update calls set status = 'running' where id = (select id from calls where"
                           " status = 'queued' order by rowid limit 1) returning *")
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
        "fresh": bool(row["fresh"]) if "fresh" in keys else False,
        "updated_at": row["updated_at"], "lease_until": row["lease_until"] if "lease_until" in keys else None,
    }


def job_status(tasks):
    statuses = {t["status"] for t in tasks}
    for status in JOB_STATUS_ORDER:
        if status in statuses:
            return status
    return "done"


def job_dict(row, tasks, cost_usd=None):
    return {"id": row["id"], "title": row["title"], "source": row["source"], "template": row["template"],
            "langs": loads(row["langs"], []), "export_dir": row["export_dir"], "trace_id": row["trace_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "project_id": row["project_id"],
            "status": job_status(tasks), "tasks": tasks, "cost_usd": cost_usd}


def encode_cursor(sort, key, job_id):
    return base64.urlsafe_b64encode(json.dumps([sort, key, job_id]).encode()).decode().rstrip("=")


def decode_cursor(cursor, sort):
    """(sort key, job id) of a cursor from encode_cursor for this sort; ValueError otherwise."""
    try:
        found, key, job_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as error:
        raise ValueError("Cursor không hợp lệ") from error
    if found != sort or not isinstance(job_id, str) or not isinstance(key, (int, float, str)):
        raise ValueError("Cursor không hợp lệ")
    return key, job_id


def project_row(fields):
    return (fields["name"], fields["color"], fields["template"], json.dumps(fields["langs"]),
            json.dumps(fields.get("options") or {}, ensure_ascii=False), fields.get("preset"), fields.get("export_dir"))


def project_dict(row, jobs=0):
    return {"id": row["id"], "name": row["name"], "color": row["color"], "template": row["template"],
            "langs": loads(row["langs"], []), "options": loads(row["options"], {}), "preset": row["preset"],
            "export_dir": row["export_dir"], "created_at": row["created_at"], "jobs": jobs}


def voice_dict(row):
    return {"id": row["id"], "name": row["name"], "project_id": row["project_id"], "path": row["path"],
            "origin": row["origin"], "start": row["clip_start"], "end": row["clip_end"], "created_at": row["created_at"]}


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
