# Running a job in this process, without workers: `dich run` and tests. Same engine as the workers.
from dich.runtime.engine import Engine

ALL_LANES = ("cpu", "gpu", "net")


def run_job(store, job_id, echo=None):
    """Runs the job's tasks until none is queued (done, failed, or waiting at a gate)."""
    engine = Engine(store, capture=False, echo=echo)
    while True:
        task = store.claim(ALL_LANES, job_id=job_id)
        if task is None:
            return store.job(job_id)
        engine.run(task)
