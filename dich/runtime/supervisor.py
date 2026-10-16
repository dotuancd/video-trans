# Starts the worker processes of each lane and keeps them running: a worker that exits (task stopped by
# the user, crash, out of memory) is replaced, and its task goes back to the queue (see Store.reap).
# The number per lane can change while running (scale): extra workers finish their task, then exit.
import json
import multiprocessing
import os
import threading
import time

from dich import settings
from dich.queue.sqlite import Store
from dich.runtime.worker import worker_main

CHECK_EVERY = 0.5
REAP_EVERY = 2.0
PRUNE_EVERY = 3600.0
MAX_WORKERS = 16


def load_counts(path):
    """lane → count saved by the web UI (overrides settings.WORKERS); {} if none."""
    try:
        return {lane: int(count) for lane, count in json.loads(path.read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Supervisor:
    def __init__(self, db_path=None, data_dir=None, workers=None, counts_file=None):
        """workers: lane → number of processes (default: settings.WORKERS). counts_file: where scale() saves
        the counts, read back at the next start (the web server's; `dich worker` has none)."""
        self.db_path = db_path or settings.DB_PATH
        self.data_dir = data_dir or settings.DATA_DIR
        self.counts_file = counts_file
        self.workers = dict(workers or settings.WORKERS)
        if counts_file:
            self.workers.update(load_counts(counts_file))
        self.context = multiprocessing.get_context("spawn")
        self.processes = {}     # (lane, index) → Process
        self.retire = {}        # (lane, index) → Event that tells the worker to exit after its task
        self.retiring = []      # (lane, Process) told to exit, not gone yet
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _spawn(self, lane, index):
        log = os.path.join(self.data_dir, "logs", f"worker-{lane}-{index}.log")
        retire = self.context.Event()
        process = self.context.Process(target=worker_main,
                                       args=([lane], str(self.db_path), str(self.data_dir), log, retire),
                                       name=f"dich-{lane}-{index}", daemon=True)
        process.start()
        self.processes[(lane, index)] = process
        self.retire[(lane, index)] = retire

    def scale(self, lane, count):
        """Runs `count` workers on `lane` from now on: starts the missing ones; the extra ones finish their
        task, then exit. Saved to counts_file."""
        count = max(0, min(MAX_WORKERS, int(count)))
        with self._lock:
            for index in range(self.workers.get(lane, 0), count):
                self._spawn(lane, index)
            for index in range(count, self.workers.get(lane, 0)):
                process = self.processes.pop((lane, index), None)
                self.retire.pop((lane, index)).set()
                if process:
                    self.retiring.append((lane, process))
            self.workers[lane] = count
        if self.counts_file:
            self.counts_file.write_text(json.dumps(self.workers))
        return count

    def start(self):
        store = Store(self.db_path, self.data_dir)
        store.reap(alive=pid_alive)     # tasks left running by a previous server
        store.prune_old()
        store.close()
        for lane, count in self.workers.items():
            for index in range(count):
                self._spawn(lane, index)
        self._thread = threading.Thread(target=self._watch, name="dich-supervisor", daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        store = Store(self.db_path, self.data_dir)
        last_reap = last_prune = time.monotonic()
        while not self._stop.wait(CHECK_EVERY):
            with self._lock:
                for (lane, index), process in list(self.processes.items()):
                    if not process.is_alive() and not self._stop.is_set():
                        process.join(0)
                        self._spawn(lane, index)
                for item in [r for r in self.retiring if not r[1].is_alive()]:
                    item[1].join(0)
                    self.retiring.remove(item)
            if time.monotonic() - last_reap > REAP_EVERY:
                last_reap = time.monotonic()
                try:
                    store.reap(alive=pid_alive)
                except Exception as error:   # database locked for too long: try again next round
                    print(f"supervisor: {error}")
            if time.monotonic() - last_prune > PRUNE_EVERY:
                last_prune = time.monotonic()
                try:
                    store.prune_old()
                except Exception as error:
                    print(f"supervisor: {error}")
        store.close()

    def status(self):
        """lane → [(pid, alive, retiring)] of the processes started here."""
        lanes = {lane: [] for lane in self.workers}
        with self._lock:
            for (lane, _index), process in sorted(self.processes.items()):
                lanes[lane].append((process.pid, process.is_alive(), False))
            for lane, process in self.retiring:
                lanes.setdefault(lane, []).append((process.pid, process.is_alive(), True))
        return lanes

    def stop(self):
        self._stop.set()
        everyone = [*self.processes.values(), *(process for _lane, process in self.retiring)]
        for process in everyone:
            if process.is_alive():
                process.terminate()
        for process in everyone:
            process.join(3)
            if process.is_alive():
                process.kill()

    def wait(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
