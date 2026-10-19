# Bulk downloads ("Gói tải về"): a bulk_download worker zips the chosen artifacts into
# <data>/bulk_downloads/<id>.zip. Flat and not compressed (videos do not shrink), each file named like its
# download, a name taken twice gets " (2)"; transcripts become .srt. Written to <id>.zip.part and renamed
# when done, so a lost worker leaves no half zip behind: the next one starts over.
import time
import traceback
import zipfile
from pathlib import Path

from dich.formats.transcript import Transcript, to_srt
from dich.logging.trace import error_code
from dich.storage.export import download_name

CHUNK = 1 << 20   # bytes copied between checks
EVERY = 1.0       # seconds between heartbeats (and progress reports)


class Cancelled(Exception):
    pass


class NothingLeft(Exception):
    pass


def unique(name, taken):
    """name, or "name (2).ext", "name (3).ext"… if taken has it (any case); added to taken."""
    path = Path(name)
    candidate, n = name, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{path.stem} ({n}){path.suffix}"
    taken.add(candidate.lower())
    return candidate


def entries(store, artifact_ids):
    """[(name in the zip, path, transcript?)] of the artifacts still there, and notes on the others."""
    found, skipped, taken = [], [], set()
    for artifact_id in artifact_ids:
        art = store.artifact(artifact_id)
        job = art and store.job_row(art["job_id"])
        if not job:
            skipped.append(f"{artifact_id}: không còn file")
            continue
        name = download_name(job["source"], art)
        path = Path(art["path"])
        if not path.is_file():
            skipped.append(f"{name}: không còn file")
            continue
        found.append((unique(name, taken), path, art["slot"].startswith("transcript.")))
    return found, skipped


def write(store, bulk_id, part, files, skipped):
    """Zips files into part, renewing the lease as it goes; a file gone meanwhile is noted in skipped.
    Progress counts the bytes of the files read (a transcript: its own size, not its .srt's)."""
    sizes = {path: path.stat().st_size for _name, path, _transcript in files if path.is_file()}
    total, done, last = sum(sizes.values()), 0, time.monotonic()

    def advance(size):
        nonlocal done, last
        done += size
        if time.monotonic() - last >= EVERY:
            if store.bulk_heartbeat(bulk_id):
                raise Cancelled
            store.bulk_progress(bulk_id, done, total)
            last = time.monotonic()

    with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as zf:
        for name, path, transcript in files:
            try:
                info = zipfile.ZipInfo.from_file(path, name, strict_timestamps=False)
                src = path.open("rb")
            except FileNotFoundError:
                skipped.append(f"{name}: không còn file")
                continue
            info.compress_type = zipfile.ZIP_STORED
            with src:
                if transcript:
                    zf.writestr(info, to_srt(Transcript.load(path)).encode())
                    advance(sizes.get(path, 0))
                    continue
                with zf.open(info, "w") as dst:
                    while block := src.read(CHUNK):
                        dst.write(block)
                        advance(len(block))
        if not zf.filelist:
            raise NothingLeft


def run(store, bulk):
    """Makes the zip of a claimed bulk download; ready, failed or cancelled after."""
    target = store.bulk_dir / f"{bulk['id']}.zip"
    part = target.with_name(target.name + ".part")
    try:
        files, skipped = entries(store, bulk["spec"]["artifact_ids"])
        if not files:
            raise NothingLeft
        part.parent.mkdir(parents=True, exist_ok=True)
        write(store, bulk["id"], part, files, skipped)
        part.replace(target)
    except Cancelled:
        part.unlink(missing_ok=True)
        store.bulk_cancelled(bulk["id"])
        return
    except NothingLeft:
        part.unlink(missing_ok=True)
        store.bulk_failed(bulk["id"], "Không còn file nào để tải")
        return
    except Exception:   # the worker goes on; the traceback is in its log under the code shown
        part.unlink(missing_ok=True)
        code = error_code(bulk["id"].removeprefix("bdl_"))
        print(f"[{code}] bulk_download {bulk['id']}\n{traceback.format_exc()}", flush=True)
        store.bulk_failed(bulk["id"], f"Lỗi hệ thống (mã {code})")
        return
    store.bulk_ready(bulk["id"], target, target.stat().st_size, skipped)
