# Creating jobs and adding languages: shared by the API and the CLI.
from pathlib import Path

from dich.core.dag import plan
from dich.core.template import get_template, resolve


def is_url(source):
    return str(source).startswith(("http://", "https://"))


def plan_job(source, langs, template="default", overrides=None):
    return plan(resolve(get_template(template), source, overrides), langs)


def create_job(store, source, langs, template="default", overrides=None, export=True):
    """A job for a file path or a URL, translated into langs. Results are exported next to the file
    (or into the job's export folder for a URL) unless export is False."""
    langs = list(dict.fromkeys(langs))
    specs = plan_job(source, langs, template, overrides)
    title = str(source) if is_url(source) else Path(source).name
    export_dir = None if not export or is_url(source) else Path(source).parent
    job_id = store.create_job(title, source, template, langs, specs, overrides, export_dir)
    if export and is_url(source):
        folder = store.job_dir(job_id) / "export"
        store.x("update jobs set export_dir = ? where id = ?", str(folder), job_id)
    return job_id


def add_langs(store, job_id, langs):
    """Plans the job again with more target languages; finished tasks are kept and reused."""
    import json
    job = store.job_row(job_id)
    current = json.loads(job["langs"])
    langs = list(dict.fromkeys(current + list(langs)))
    specs = plan_job(job["source"], langs, job["template"], json.loads(job["overrides"]))
    return store.add_langs(job_id, langs, specs)
