# A worker process: claims tasks of its lanes one at a time and runs them, keeping loaded models between
# tasks. Started by the supervisor (dich serve / dich worker). Stopping a task = the worker exits and the
# supervisor starts a new one, so a task can be stopped in the middle of any step.
import os
import signal
import sys
import time
from pathlib import Path

from dich.logging import system
from dich.queue.sqlite import Store
from dich.runtime.engine import Engine

IDLE = 0.3   # seconds between polls when there is nothing to do


def worker_main(lanes, db_path=None, data_dir=None, log_path=None):
    if log_path:   # what the worker prints between tasks (model loading output goes to the task's log)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        file = open(log_path, "ab", buffering=0)
        os.dup2(file.fileno(), 1)
        os.dup2(file.fileno(), 2)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    system.setup_logging()
    store = Store(db_path, data_dir)

    def on_cancel(task, attempt):
        store.cancelled(task["id"], attempt["id"])
        sys.stdout.flush()
        os._exit(3)

    engine = Engine(store, capture=True, on_cancel=on_cancel)
    task_lanes = [lane for lane in lanes if lane != "interactive"]
    print(f"worker {os.getpid()} lanes={','.join(lanes)}", flush=True)
    while True:
        if "interactive" in lanes:
            call = store.claim_call()
            if call:
                engine.answer(call)
                continue
        task = store.claim(task_lanes) if task_lanes else None
        if task:
            engine.run(task)
            continue
        time.sleep(IDLE)
