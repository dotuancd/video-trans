# Starts the worker processes of each lane and keeps them running: a worker that exits (task stopped by
# the user, crash, out of memory) is replaced, and its task goes back to the queue (see Store.reap).
import multiprocessing
import os
import threading
import time

from dich import settings
from dich.queue.sqlite import Store
from dich.runtime.worker import worker_main

CHECK_EVERY = 0.5
REAP_EVERY = 2.0


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Supervisor:
    def __init__(self, db_path=None, data_dir=None, workers=None):
        """workers: lane → number of processes (default: settings.WORKERS)."""
        self.db_path = db_path or settings.DB_PATH
        self.data_dir = data_dir or settings.DATA_DIR
        self.workers = workers or settings.WORKERS
        self.context = multiprocessing.get_context("spawn")
        self.processes = {}     # (lane, index) → Process
        self._stop = threading.Event()
        self._thread = None

    def _spawn(self, lane, index):
        log = os.path.join(self.data_dir, "logs", f"worker-{lane}-{index}.log")
        process = self.context.Process(target=worker_main, args=([lane], str(self.db_path), str(self.data_dir), log),
                                       name=f"dich-{lane}-{index}", daemon=True)
        process.start()
        self.processes[(lane, index)] = process

    def start(self):
        store = Store(self.db_path, self.data_dir)
        store.reap(alive=pid_alive)     # tasks left running by a previous server
        store.close()
        for lane, count in self.workers.items():
            for index in range(count):
                self._spawn(lane, index)
        self._thread = threading.Thread(target=self._watch, name="dich-supervisor", daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        store = Store(self.db_path, self.data_dir)
        last_reap = 0.0
        while not self._stop.wait(CHECK_EVERY):
            for (lane, index), process in list(self.processes.items()):
                if not process.is_alive() and not self._stop.is_set():
                    process.join(0)
                    self._spawn(lane, index)
            if time.monotonic() - last_reap > REAP_EVERY:
                last_reap = time.monotonic()
                try:
                    store.reap(alive=pid_alive)
                except Exception as error:   # database locked for too long: try again next round
                    print(f"supervisor: {error}")
        store.close()

    def stop(self):
        self._stop.set()
        for process in self.processes.values():
            if process.is_alive():
                process.terminate()
        for process in self.processes.values():
            process.join(3)
            if process.is_alive():
                process.kill()

    def wait(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
