import os
import time
import zipfile
from pathlib import Path

import pytest
from test_engine import make_job
from test_worker import wait

from dich import cli, settings
from dich.api.app import is_loopback
from dich.runtime.local import run_job
from dich.runtime.supervisor import Supervisor
from dich.storage import bulk as bulk_zip


def fake_zip(store, bulk):
    path = store.bulk_dir / f"{bulk['id']}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"zip")
    return path


def test_create_reuses_the_same_artifacts(store):
    first = store.create_bulk_download("a.zip", ["art_2", "art_1"], {"srt": True})
    assert first["status"] == "queued" and first["spec"]["artifact_ids"] == ["art_1", "art_2"]
    # being made: the same one
    assert store.create_bulk_download("b.zip", ["art_1", "art_2"], {"srt": True})["id"] == first["id"]
    # other options: a new one
    assert store.create_bulk_download("a.zip", ["art_1", "art_2"], {"srt": False})["id"] != first["id"]

    bulk = store.claim_bulk_download()
    assert bulk["id"] == first["id"] and bulk["status"] == "running"
    assert store.bulk_ready(bulk["id"], fake_zip(store, bulk), 3, ["x.mp4: không còn file"])
    ready = store.bulk_download(bulk["id"])
    assert ready["status"] == "ready" and ready["skipped"] == ["x.mp4: không còn file"] and ready["size"] == 3

    store.x("update bulk_downloads set expires_at = ? where id = ?", time.time() + 60, bulk["id"])
    again = store.create_bulk_download("c.zip", ["art_1", "art_2"], {"srt": True})
    assert again["id"] == bulk["id"]
    assert again["expires_at"] > time.time() + settings.BULK_DOWNLOAD_TTL_HOURS * 3600 - 60   # kept longer

    # the zip is gone: made again
    os.unlink(ready["path"])
    assert store.create_bulk_download("c.zip", ["art_1", "art_2"], {"srt": True})["id"] != bulk["id"]


def test_claim_heartbeat_cancel_and_delete(store):
    assert store.claim_bulk_download() is None
    first = store.create_bulk_download("a.zip", ["art_1"])
    second = store.create_bulk_download("b.zip", ["art_2"])
    third = store.create_bulk_download("c.zip", ["art_3"])
    assert store.cancel_bulk_download(second["id"])["status"] == "cancelled"   # queued: at once
    bulk = store.claim_bulk_download()
    assert bulk["id"] == first["id"]
    assert store.bulk_heartbeat(bulk["id"]) is False
    store.bulk_progress(bulk["id"], 10, 100)
    assert store.bulk_download(bulk["id"])["done"] == 10

    store.cancel_bulk_download(bulk["id"])                  # running: the worker stops it
    assert store.bulk_heartbeat(bulk["id"]) is True
    assert not store.bulk_ready(bulk["id"], fake_zip(store, bulk), 3)
    assert store.bulk_download(bulk["id"])["status"] == "cancelled"
    assert not (store.bulk_dir / f"{bulk['id']}.zip").exists()

    bulk = store.claim_bulk_download()
    assert bulk["id"] == third["id"]
    path = fake_zip(store, bulk)
    assert store.delete_bulk_download(bulk["id"])           # deleted while running
    assert store.bulk_heartbeat(bulk["id"]) is True
    assert not store.bulk_ready(bulk["id"], path, 3) and not path.exists()
    assert store.claim_bulk_download() is None
    assert not store.delete_bulk_download("bdl_missing") and store.cancel_bulk_download("bdl_missing") is None


def test_reap_starts_over_then_fails(store):
    bulk = store.create_bulk_download("a.zip", ["art_1"])
    for retry in range(3):
        claimed = store.claim_bulk_download(lease=60, pid=4242)
        assert claimed["id"] == bulk["id"]
        assert store.reap_bulk_downloads(alive=lambda pid: True) == 0     # lease still on, worker alive
        assert store.reap_bulk_downloads(alive=lambda pid: pid != 4242) == 1
        found = store.bulk_download(bulk["id"])
        if retry < 2:
            assert found["status"] == "queued" and found["retries"] == retry + 1
    assert found["status"] == "failed"

    other = store.create_bulk_download("b.zip", ["art_2"])
    store.claim_bulk_download(lease=-1)
    store.cancel_bulk_download(other["id"])
    assert store.reap_bulk_downloads() == 1                                # lease ran out, user asked to stop
    assert store.bulk_download(other["id"])["status"] == "cancelled"


def test_prune_deletes_expired_zips_and_strays(store):
    kept = store.create_bulk_download("a.zip", ["art_1"])
    expired = store.create_bulk_download("b.zip", ["art_2"])
    for _ in range(2):
        bulk = store.claim_bulk_download()
        store.bulk_ready(bulk["id"], fake_zip(store, bulk), 3)
    queued = store.create_bulk_download("c.zip", ["art_3"])
    (store.bulk_dir / f"{queued['id']}.zip.part").write_bytes(b"z")        # a lost worker's, made again later
    stray = store.bulk_dir / "bdl_gone.zip"
    stray.write_bytes(b"z")
    store.x("update bulk_downloads set expires_at = ? where id = ?", time.time() - 1, expired["id"])
    expired_path = store.bulk_download(expired["id"])["path"]

    assert store.prune_bulk_downloads() == 1
    assert store.bulk_download(expired["id"]) is None and not os.path.exists(expired_path)
    assert not stray.exists()
    assert os.path.exists(store.bulk_download(kept["id"])["path"])
    assert (store.bulk_dir / f"{queued['id']}.zip.part").exists()


def test_serve_host():
    assert cli.build_parser().parse_args(["serve"]).host == settings.UI_HOST == "127.0.0.1"
    assert cli.build_parser().parse_args(["serve", "--host", "0.0.0.0"]).host == "0.0.0.0"
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("100.64.1.2") and not is_loopback("my-mac")


@pytest.fixture
def results(store, tmp_path):
    """Current artifacts (by id) of two done jobs whose sources are both named a.mp4."""
    arts = []
    for folder in ("one", "two"):
        video = tmp_path / folder / "a.mp4"
        video.parent.mkdir()
        video.write_bytes(b"video")
        job_id = make_job(store, video, export=False)
        run_job(store, job_id)
        arts += [a for found in store.current_artifacts([job_id]).values() for a in found]
    return arts


def ids(arts, *slots):
    return [a["id"] for a in arts if a["slot"] in slots]


def test_zip_is_flat_stored_and_named_like_downloads(store, results):
    bulk = store.create_bulk_download("a.zip", ids(results, "transcript.vi", "audio.dub.vi"))
    bulk_zip.run(store, store.claim_bulk_download())
    done = store.bulk_download(bulk["id"])
    assert done["status"] == "ready" and done["skipped"] == []
    assert done["path"] == str(store.bulk_dir / f"{bulk['id']}.zip") and done["size"] == os.path.getsize(done["path"])
    assert not (store.bulk_dir / f"{bulk['id']}.zip.part").exists()
    with zipfile.ZipFile(done["path"]) as zf:
        assert sorted(zf.namelist()) == ["a.vi (2).srt", "a.vi (2).wav", "a.vi.srt", "a.vi.wav"]
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}
        assert "-->" in zf.read("a.vi.srt").decode()        # transcripts as .srt
        wav = next(a for a in results if a["slot"] == "audio.dub.vi")
        assert zf.read("a.vi.wav") == Path(wav["path"]).read_bytes()


def test_missing_files_are_skipped(store, results):
    gone, kept = ids(results, "audio.dub.vi")
    os.unlink(store.artifact(gone)["path"])
    bulk = store.create_bulk_download("a.zip", [gone, kept, "art_missing"])
    bulk_zip.run(store, store.claim_bulk_download())
    done = store.bulk_download(bulk["id"])
    assert done["status"] == "ready" and len(done["skipped"]) == 2 and any("a.vi.wav" in s for s in done["skipped"])
    with zipfile.ZipFile(done["path"]) as zf:
        assert zf.namelist() == ["a.vi.wav"]

    nothing = store.create_bulk_download("b.zip", ["art_missing"])
    bulk_zip.run(store, store.claim_bulk_download())
    failed = store.bulk_download(nothing["id"])
    assert failed["status"] == "failed" and failed["error"] and not list(store.bulk_dir.glob(f"{nothing['id']}*"))


def test_file_gone_while_zipping_is_skipped(store, results, monkeypatch):
    gone, kept = ids(results, "audio.dub.vi")
    bulk = store.create_bulk_download("a.zip", [gone, kept])
    found = bulk_zip.entries

    def then_delete(*args):
        files, skipped = found(*args)
        os.unlink(store.artifact(gone)["path"])
        return files, skipped
    monkeypatch.setattr(bulk_zip, "entries", then_delete)
    bulk_zip.run(store, store.claim_bulk_download())
    done = store.bulk_download(bulk["id"])
    assert done["status"] == "ready" and done["skipped"] == ["a.vi.wav: không còn file"]
    with zipfile.ZipFile(done["path"]) as zf:
        assert zf.namelist() == ["a.vi (2).wav"]


def test_system_error_shows_a_code(store, results, monkeypatch, capsys):
    bulk = store.create_bulk_download("a.zip", ids(results, "transcript.vi"))
    monkeypatch.setattr(bulk_zip, "to_srt", lambda _t: 1 / 0)
    bulk_zip.run(store, store.claim_bulk_download())
    failed = store.bulk_download(bulk["id"])
    assert failed["status"] == "failed" and failed["error"].startswith("Lỗi hệ thống (mã ")
    assert "ZeroDivisionError" in capsys.readouterr().out
    assert not list(store.bulk_dir.glob(f"{bulk['id']}*"))


def test_cancel_midway_removes_the_part(store, results, monkeypatch):
    bulk = store.create_bulk_download("a.zip", ids(results, "audio.dub.vi"))
    claimed = store.claim_bulk_download()
    monkeypatch.setattr(bulk_zip, "CHUNK", 1)              # a heartbeat between blocks
    monkeypatch.setattr(bulk_zip, "EVERY", 0)
    store.cancel_bulk_download(bulk["id"])
    bulk_zip.run(store, claimed)
    assert store.bulk_download(bulk["id"])["status"] == "cancelled"
    assert not list(store.bulk_dir.glob(f"{bulk['id']}*"))


def test_progress_is_reported(store, results, monkeypatch):
    store.create_bulk_download("a.zip", ids(results, "audio.dub.vi"))
    monkeypatch.setattr(bulk_zip, "EVERY", 0)
    bulk_zip.run(store, store.claim_bulk_download())
    progress = [e["data"] for e in store.q("select data from events where type = 'bulk_download'")
                if '"done"' in e["data"]]
    assert progress


def test_lost_worker_starts_over(store, results):
    bulk = store.create_bulk_download("a.zip", ids(results, "audio.dub.vi"))
    store.claim_bulk_download(lease=-1, pid=4242)
    part = store.bulk_dir / f"{bulk['id']}.zip.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"half a zip")                         # what the lost worker left
    assert store.reap_bulk_downloads() == 1
    bulk_zip.run(store, store.claim_bulk_download())
    done = store.bulk_download(bulk["id"])
    assert done["status"] == "ready" and done["retries"] == 1 and not part.exists()
    with zipfile.ZipFile(done["path"]) as zf:
        assert zf.namelist() == ["a.vi.wav", "a.vi (2).wav"]


def test_unique_names():
    taken = set()
    assert [bulk_zip.unique("a.vi.srt", taken) for _ in range(3)] == ["a.vi.srt", "a.vi (2).srt", "a.vi (3).srt"]
    assert bulk_zip.unique("A.VI.SRT", taken) == "A.VI (4).SRT"     # case-insensitive filesystems
    assert bulk_zip.unique("README", set()) == "README"


def test_bulk_download_worker(store, results):
    assert settings.WORKERS["bulk_download"] == 1
    supervisor = Supervisor(store.path, store.data_dir, {"bulk_download": 1}).start()
    try:
        bulk = store.create_bulk_download("a.zip", ids(results, "audio.dub.vi"))
        assert wait(lambda: store.bulk_download(bulk["id"])["status"] == "ready")
    finally:
        supervisor.stop()
