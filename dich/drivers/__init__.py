# Drivers: one implementation of one stage (asr/needle, translate/claude, dub/vieneu…).
#
# A driver only gets its inputs (artifacts by name) and returns its outputs (paths by name): it does not
# know about jobs, folders or file names, so it is easy to test and to swap. Third-party drivers register
# through the `dich.drivers` entry point group (each entry point is a module that uses @register).
import os
from importlib import import_module
from importlib.metadata import entry_points
from typing import ClassVar

from pydantic import BaseModel

from dich.logging.events import Skip, UserError  # noqa: F401  (re-exported for drivers)

BUILTIN = [
    "dich.drivers.source.file", "dich.drivers.source.url",
    "dich.drivers.asr.needle",
    "dich.drivers.refine.rules",
    "dich.drivers.brief.llm",
    "dich.drivers.mindmap.llm",
    "dich.drivers.translate.local", "dich.drivers.translate.llm",
    "dich.drivers.dub.vieneu",
    "dich.drivers.mux.ffmpeg",
]
KEEP_POLICIES = ("always", "last", "success_only", "none")


class NoParams(BaseModel):
    pass


class Driver:
    stage: ClassVar[str]
    name: ClassVar[str]
    version: ClassVar[str] = "1"             # bump it and cached results are not reused
    title: ClassVar[str] = ""                # shown in the UI
    Params: ClassVar[type[BaseModel]] = NoParams
    resources: ClassVar[set] = {"cpu"}       # {"cpu"} | {"gpu"} | {"net"}: the worker lane
    # input name → slot; "a|b" = the first one some stage produces, a trailing "?" = optional.
    # {lang} is the language of a per-language task.
    consumes: ClassVar[dict] = {}
    produces: ClassVar[dict] = {}            # output name → slot
    keep: ClassVar[dict] = {}                # output name → always | last | success_only | none (default always)
    langs: ClassVar[set | None] = None       # target languages it can make, None = any
    cache: ClassVar[bool] = True             # reuse the result of an attempt with the same fingerprint

    @classmethod
    def available(cls):
        """None when the driver can run here, else why not (missing package, API key…)."""
        return None

    @classmethod
    def outputs(cls, params, lang=None):
        """Output name → slot this task will produce (a source may depend on its params)."""
        return {name: slot.format(lang=lang) for name, slot in cls.produces.items()}

    @classmethod
    def per_lang(cls):
        return any("{lang}" in slot for slot in [*cls.produces.values(), *cls.consumes.values()])

    @classmethod
    def supports(cls, lang):
        return cls.langs is None or lang in cls.langs

    def load(self):
        """Called once per worker process before the first run; models stay loaded between tasks."""

    def run(self, ctx, inputs, params):
        """inputs: name → Artifact (has .path); returns name → Path of each output written."""
        raise NotImplementedError


REGISTRY = {}
_loaded = False


def register(cls):
    REGISTRY[(cls.stage, cls.name)] = cls
    return cls


def load_all():
    global _loaded
    if _loaded:
        return REGISTRY
    _loaded = True
    # $DICH_DRIVERS: more modules to import, comma separated (drivers in development, tests)
    extra = [m.strip() for m in os.environ.get("DICH_DRIVERS", "").split(",") if m.strip()]
    for module in BUILTIN + extra:
        import_module(module)
    for entry in entry_points(group="dich.drivers"):
        try:
            entry.load()
        except Exception as error:   # a broken plugin must not take the app down
            print(f"driver plugin {entry.name}: {error}")
    return REGISTRY


def get(stage, name):
    load_all()
    try:
        return REGISTRY[(stage, name)]
    except KeyError:
        raise KeyError(f"no driver {stage}/{name}") from None


def of_stage(stage):
    load_all()
    return [cls for (s, _), cls in REGISTRY.items() if s == stage]


def lane(cls):
    for name in ("gpu", "net"):
        if name in cls.resources:
            return name
    return "cpu"


def module_missing(*modules):
    """available() helper: the first module that can't be imported, as a message."""
    from importlib.util import find_spec
    for module in modules:
        if find_spec(module) is None:
            return f"Thiếu thư viện {module}"
    return None
