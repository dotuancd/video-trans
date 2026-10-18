# Artifacts: the files a task produced. Each attempt writes into its own folder
#   <data>/jobs/<job id>/<task key>/<attempt n>/outputs/
# next to its events.jsonl and system.log (and requests.jsonl for an LLM translation). A task is skipped when an earlier attempt (of any job) had the
# same fingerprint: its outputs are hard-linked into the new attempt instead of computed again.
import hashlib
import os
import shutil
from pathlib import Path

from dich.media import canonical, sha256


class Artifact(dict):
    """An artifact row; drivers use .path."""

    @property
    def path(self):
        return Path(self["path"])


def fingerprint(cls, params, inputs):
    """hash(driver, version, params, hashes of the inputs)."""
    data = {"driver": f"{cls.stage}/{cls.name}", "version": cls.version, "params": params,
            "inputs": {name: art["sha256"] if art else None for name, art in sorted(inputs.items())}}
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def link_or_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.link(source, tmp)
    except OSError:
        shutil.copy2(source, tmp)
    tmp.replace(target)
    return target


def describe(cls, task, outputs, outdir):
    """Artifact rows for the paths a driver returned. A path outside the attempt folder (a source file
    on the user's disk) is referenced, never deleted."""
    rows = []
    outdir = Path(outdir).resolve()
    for name, path in outputs.items():
        path = Path(path).resolve()
        if name not in task["outputs"]:
            raise RuntimeError(f"{cls.stage}/{cls.name} returned unknown output {name}")
        rows.append({"name": name, "slot": task["outputs"][name], "path": str(path), "sha256": sha256(path),
                     "size": path.stat().st_size, "keep": cls.keep.get(name, "always"),
                     "external": outdir not in path.parents})
    missing = set(task["outputs"]) - set(outputs)
    if missing:
        raise RuntimeError(f"{cls.stage}/{cls.name} did not write {', '.join(sorted(missing))}")
    return rows


def reuse(cached, task, outdir):
    """Artifact rows for a cache hit: the cached files hard-linked into this attempt (external ones
    referenced as they are)."""
    rows = []
    for art in cached:
        if art["name"] not in task["outputs"]:
            continue
        path = Path(art["path"])
        if not art["external"]:
            path = link_or_copy(path, Path(outdir) / path.name)
        rows.append({**art, "slot": task["outputs"][art["name"]], "path": str(path)})
    return rows
