import logging
import threading

from dich_server import steplog
from dich_server.steplog import StepLogs, clean


def step(step_id, job_id=1, attempt=1):
    return {"id": step_id, "job_id": job_id, "attempt": attempt}


def test_clean_keeps_last_redraw_of_a_line():
    assert clean("a\r 10%\r 50%\r100%\nb") == "100%\nb"


def test_threads_log_to_their_own_step(tmp_path):
    logs = StepLogs(tmp_path)
    ready, go = threading.Barrier(2), threading.Barrier(2)

    def run(step_id):
        writer = logs.open(step(step_id))
        ready.wait()
        for i in range(20):
            print(f"step {step_id} line {i}")
        logging.getLogger("dich").info("info of %d", step_id)
        go.wait()
        logs.close(writer)

    threads = [threading.Thread(target=run, args=(i,)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print("not in any step")
    for i in (1, 2):
        text = logs.read(1, i)[0]
        assert text.count(f"step {i} line") == 20
        assert f"step {3 - i}" not in text
        assert f"INFO dich: info of {i}" in text
        assert "not in any step" not in text


def test_attempts_append_and_live_updates(tmp_path):
    pieces = []
    logs = StepLogs(tmp_path, lambda s, offset, text: pieces.append((offset, text)))
    for attempt in (1, 2):
        writer = logs.open(step(5, attempt=attempt))
        writer.write("progress\r")
        writer.write("done\n")
        logs.close(writer)
    text, offset, size = logs.read(1, 5)
    assert offset == 0 and size == len(text.encode())
    assert "Lần 1" in text and "Lần 2" in text and text.count("done\n") == 2
    assert "".join(t for _, t in pieces) == text                    # every piece sent, in order
    assert all(o == len("".join(t for _, t in pieces[:i]).encode()) for i, (o, _) in enumerate(pieces))
    assert logs.read(1, 5, offset=size)[0] == ""


def test_long_log_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.setattr(steplog, "MAX_SIZE", 500)
    monkeypatch.setattr(steplog, "KEEP", 300)
    logs = StepLogs(tmp_path)
    for attempt in range(1, 4):
        writer = logs.open(step(1, attempt=attempt))
        for i in range(50):
            writer.line(f"line {i}")
        logs.close(writer)
    text = logs.read(1, 1)[0]
    assert text.startswith("[...]\n") and "Lần 3" in text and len(text) < 1000


def test_remove_and_prune(tmp_path):
    logs = StepLogs(tmp_path)
    for job_id in (1, 2, 3):
        logs.close(logs.open(step(1, job_id)))
    logs.remove(1)
    logs.prune([3])
    assert [p.name for p in tmp_path.iterdir()] == ["3"]
