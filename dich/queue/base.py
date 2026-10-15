# What workers need from the queue. SQLite (sqlite.py) is enough for one machine; another backend
# (Redis, Postgres) implements the same methods and drivers do not change.
from typing import Protocol


class Busy(Exception):
    """The request conflicts with a running task."""


class NeedsConfirm(Exception):
    """Running this would replace edits made by hand; .keys lists the tasks."""

    def __init__(self, keys):
        super().__init__("Chạy lại sẽ thay bản đã sửa tay: " + ", ".join(keys))
        self.keys = keys


class Queue(Protocol):
    def claim(self, lanes, lease): ...
    def heartbeat(self, task_id, lease): ...
    def complete(self, task_id, attempt_id, outputs, message=None): ...
    def fail(self, task_id, attempt_id, message, error=None): ...
    def cancel_job(self, job_id): ...
