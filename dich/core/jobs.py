# Creating jobs and adding languages: shared by the API and the CLI.
from pathlib import Path

from dich.core.dag import plan
from dich.core.template import get_template, resolve


def is_url(source):
    return str(source).startswith(("http://", "https://"))


def plan_job(source, langs, template="default", overrides=None):
    return plan(resolve(get_template(template), source, overrides), langs)


def create_job(store, source, langs, template="default", overrides=None, export=True, use_subtitles=False,
               project_id=None):
    """A job for a file path or a URL, translated into langs. Results are exported unless export is False:
    into the project's export folder when it has one, else next to the file (or into the job's export folder
    for a URL). use_subtitles: the .srt files named like the video next to it are used instead of
    transcribing / translating."""
    langs = list(dict.fromkeys(langs))
    if use_subtitles and not is_url(source):
        from dich.drivers.source.file import siblings
        from dich.media import is_media
        found = siblings(source) if is_media(Path(source).name) else {}
        if found:
            overrides = dict(overrides or {})
            source_override = dict(overrides.get("source") or {})
            source_override["params"] = {**(source_override.get("params") or {}), "subtitles": found}
            overrides["source"] = source_override
    specs = plan_job(source, langs, template, overrides)
    title = str(source) if is_url(source) else Path(source).name
    project = store.project(project_id) if project_id else None
    if not export:
        export_dir = None
    elif project and project["export_dir"]:
        export_dir = Path(project["export_dir"]).expanduser()
    else:
        export_dir = None if is_url(source) else Path(source).parent
    job_id = store.create_job(title, source, template, langs, specs, overrides, export_dir, project_id)
    if export and not export_dir:
        folder = store.job_dir(job_id) / "export"
        store.x("update jobs set export_dir = ? where id = ?", str(folder), job_id)
    return job_id


def merge_overrides(current, change):
    """Per-stage overrides with change applied over current: its fields win, its params are added."""
    merged = {stage: dict(o) for stage, o in (current or {}).items()}
    for stage, o in (change or {}).items():
        stage_override = merged.setdefault(stage, {})
        params = {**(stage_override.get("params") or {}), **(o.get("params") or {})}
        stage_override.update({k: v for k, v in o.items() if k != "params"})
        if params:
            stage_override["params"] = params
    return merged


def replan(store, job_id, langs=(), overrides=None):
    """Plans the job again with more target languages and/or changed overrides (e.g. a stage that was off
    turned on). Only the tasks that are new get added: the existing ones and their results stay as they
    are, even when the change would plan them differently (run them again for that). Returns the new keys."""
    import json
    job = store.job_row(job_id)
    langs = list(dict.fromkeys(json.loads(job["langs"]) + list(langs)))
    overrides = merge_overrides(json.loads(job["overrides"]), overrides)
    specs = plan_job(job["source"], langs, job["template"], overrides)
    return store.replan(job_id, langs, overrides, specs)


def add_langs(store, job_id, langs):
    """Plans the job again with more target languages; finished tasks are kept and reused."""
    return replan(store, job_id, langs)
