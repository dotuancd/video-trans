# Templates: which stages a job runs, with which driver and default params (YAML, picked when creating a
# job). Built-in ones live next to this file; more can be dropped into <data dir>/templates.
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from dich import drivers, settings

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "templates"


class StageConf(BaseModel):
    stage: str
    driver: str
    enabled: bool = True
    gate: str | None = None            # "review": the job waits for the user before this stage
    params: dict = Field(default_factory=dict)


class Template(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    stages: list[StageConf]


def template_dirs():
    return [BUILTIN_DIR, settings.DATA_DIR / "templates"]


def load_templates():
    found = {}
    for folder in template_dirs():
        for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
            template = Template(**yaml.safe_load(path.read_text(encoding="utf-8")))
            found[template.name] = template
    return found


def get_template(name):
    templates = load_templates()
    if name not in templates:
        raise KeyError(f"Không có template {name}")
    return templates[name]


def source_driver(source):
    """The source driver and its params for a path or a URL."""
    if str(source).startswith(("http://", "https://")):
        return "url", {"url": str(source)}
    return "file", {"path": str(source)}


def resolve(template, source, overrides=None):
    """The template's stages with drivers chosen ("auto" source, "env" translate) and per-stage
    overrides applied: overrides = {stage: {"driver", "enabled", "gate", "params"}}."""
    overrides = overrides or {}
    stages = []
    for conf in template.stages:
        conf = conf.model_copy(deep=True)
        change = overrides.get(conf.stage) or {}
        if "driver" in change and change["driver"]:
            if change["driver"] != conf.driver:
                conf.params = {}
            conf.driver = change["driver"]
        for field in ("enabled", "gate"):
            if field in change:
                setattr(conf, field, change[field])
        conf.params = {**conf.params, **(change.get("params") or {})}
        if conf.stage == "source":
            conf.driver, params = source_driver(source)
            conf.params = {**conf.params, **params}
        elif conf.driver == "env" and conf.stage == "translate":
            conf.driver = settings.ENGINE
        if conf.enabled:
            cls = drivers.get(conf.stage, conf.driver)   # KeyError for an unknown driver
            conf.params = cls.Params(**conf.params).model_dump(mode="json")
        stages.append(conf)
    return stages
