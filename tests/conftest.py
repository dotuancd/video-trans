import os

import pytest

os.environ.setdefault("DICH_DRIVERS", "fake_drivers")


@pytest.fixture
def store(tmp_path):
    from dich.queue.sqlite import Store
    s = Store(tmp_path / "data" / "dich.sqlite")
    yield s
    s.close()


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "media" / "a.mp4"
    path.parent.mkdir()
    path.write_bytes(b"not really a video")
    return path
