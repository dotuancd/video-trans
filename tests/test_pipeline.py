import threading
import time

import pytest

from dich_server.pipeline import Busy, Pipeline, Store


class FakeSteps:
    """Writes each output; fails the steps named in fail; blocks on gate when set."""

    def __init__(self):
        self.fail = set()
        self.calls = []
        self.gate = None

    def __call__(self, spec, params, progress):
        self.calls.append((spec.name, params))
        if self.gate:
            for i in range(1, 100):
                progress(i, 100)
                if self.gate.wait(0.01):
                    break
        if spec.name in self.fail:
            raise RuntimeError(f"{spec.name} broke")
        spec.output.write_text(spec.name)
        return f"{spec.name} ok"


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def statuses(pipeline, job_id):
    return [step["status"] for step in pipeline.store.job(job_id)["steps"]]


@pytest.fixture
def setup(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"")
    fake = FakeSteps()
    pipeline = Pipeline(Store(":memory:"), run_step=fake)
    pipeline.start()
    yield pipeline, fake, media, tmp_path
    pipeline.stop()


def test_runs_all_steps_in_order(setup):
    pipeline, fake, media, root = setup
    [job_id], _ = pipeline.add([media], "media", root, dub=True, mux=True, params={"engine": "openai"})
    wait_for(lambda: pipeline.store.job(job_id)["status"] == "done")
    assert [name for name, _ in fake.calls] == ["transcribe", "translate", "dub", "mux"]
    assert fake.calls[1][1]["engine"] == "openai"


def test_existing_outputs_are_done_unless_overwrite(setup):
    pipeline, fake, media, root = setup
    (root / "a.srt").write_text("x")
    [job_id], _ = pipeline.add([media], "media", root)
    wait_for(lambda: pipeline.store.job(job_id)["status"] == "done")
    assert [name for name, _ in fake.calls] == ["translate"]


def test_failed_step_waits_then_retry_continues(setup):
    pipeline, fake, media, root = setup
    fake.fail.add("translate")
    [job_id], _ = pipeline.add([media], "media", root, dub=True)
    wait_for(lambda: statuses(pipeline, job_id) == ["done", "failed", "queued"])
    assert "translate broke" in pipeline.store.job(job_id)["steps"][1]["error"]
    fake.fail.clear()
    pipeline.rerun(job_id, "translate")
    wait_for(lambda: statuses(pipeline, job_id) == ["done", "done", "done"])


def test_rerun_with_new_params(setup):
    pipeline, fake, media, root = setup
    [job_id], _ = pipeline.add([media], "media", root, dub=True)
    wait_for(lambda: pipeline.store.job(job_id)["status"] == "done")
    pipeline.store.rerun(job_id, "translate", {"engine": "claude"}, cascade=False)
    # nothing else running: steps after it are stale until the worker reruns translate
    assert pipeline.store.job(job_id)["steps"][2]["status"] == "stale"
    pipeline.wake()
    wait_for(lambda: statuses(pipeline, job_id) == ["done", "done", "stale"])
    assert fake.calls[-1] == ("translate", {**fake.calls[1][1], "engine": "claude"})


def test_cancel_running_step(setup):
    pipeline, fake, media, root = setup
    fake.gate = threading.Event()
    [job_id], _ = pipeline.add([media], "media", root, dub=True)
    wait_for(lambda: statuses(pipeline, job_id)[0] == "running")
    assert pipeline.add([media], "media", root) == ([], ["a.mp4 đang được xử lý"])
    with pytest.raises(Busy):
        pipeline.delete(job_id)
    pipeline.cancel(job_id)
    wait_for(lambda: statuses(pipeline, job_id) == ["cancelled", "cancelled", "cancelled"])


def test_recover_marks_running_failed(tmp_path):
    store = Store(tmp_path / "jobs.sqlite")
    media = tmp_path / "a.mp4"
    media.write_bytes(b"")
    pipeline = Pipeline(store, run_step=FakeSteps())
    pipeline.add([media], "media", tmp_path)
    assert store.claim_next()["name"] == "transcribe"
    store2 = Store(tmp_path / "jobs.sqlite")
    store2.recover()
    assert store2.jobs()[0]["steps"][0]["status"] == "failed"


def test_remote_translate_runs_alongside_cpu_steps(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"")
    b.write_bytes(b"")
    (tmp_path / "b.srt").write_text("x")
    dubbing, release = threading.Event(), threading.Event()

    def run_step(spec, params, progress):
        if spec.name == "transcribe":
            dubbing.set()
            assert release.wait(5)
        spec.output.write_text(spec.name)

    pipeline = Pipeline(Store(":memory:"), run_step=run_step)
    pipeline.start()
    try:
        [job_a], _ = pipeline.add([a], "media", tmp_path)
        assert dubbing.wait(5)
        [job_b], _ = pipeline.add([b], "media", tmp_path, params={"engine": "openai"})
        wait_for(lambda: pipeline.store.job(job_b)["status"] == "done")   # while a still holds the cpu lane
        assert statuses(pipeline, job_a) == ["running", "queued"]
        [job_c], _ = pipeline.add([b], "media", tmp_path, overwrite=True, params={"engine": "local"})
        time.sleep(0.1)
        assert statuses(pipeline, job_c)[0] == "queued"                    # local engine waits for the cpu
        release.set()
        wait_for(lambda: pipeline.store.job(job_c)["status"] == "done")
        wait_for(lambda: pipeline.store.job(job_a)["status"] == "done")
    finally:
        release.set()
        pipeline.stop()


def test_step_log_has_options_progress_and_traceback(setup):
    pipeline, fake, media, root = setup
    fake.fail.add("translate")
    events = []
    pipeline.listeners.add(events.append)
    [job_id], _ = pipeline.add([media], "media", root, params={"engine": "openai"})
    wait_for(lambda: statuses(pipeline, job_id) == ["done", "failed"])
    steps = pipeline.store.job(job_id)["steps"]
    done, failed = (pipeline.logs.read(job_id, s["id"])[0] for s in steps)
    assert "Xong sau" in done and "transcribe ok" in done
    assert '"engine": "openai"' in failed
    assert "Traceback" in failed and "RuntimeError: translate broke" in failed
    wait_for(lambda: "Lỗi sau" in "".join(e["text"] for e in events if e["type"] == "log"))
    pipeline.delete(job_id)
    assert not (pipeline.logs.folder / str(job_id)).exists()
