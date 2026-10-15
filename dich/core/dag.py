# The task graph of a job, wired by slot: each driver says which slots it reads and writes, and a task
# depends on whichever earlier task produces the slot it reads. Adding or removing a stage needs no
# change to the others.
#
#   "a|b"  the first slot some earlier task produces (no `separate` → asr reads audio.original)
#   "x?"   optional input
#   {lang} per-language tasks, one per target language: translate@vi, dub@vi…
#   a stage that reads and writes the same slot (refine) slots into the chain: later readers get its
#   output, and without it they read the one before.
from dataclasses import dataclass, field

from dich import drivers


@dataclass
class TaskSpec:
    key: str
    stage: str
    driver: str
    lang: str | None
    params: dict
    inputs: dict = field(default_factory=dict)     # name → [producer key, slot]
    outputs: dict = field(default_factory=dict)    # name → slot
    deps: list = field(default_factory=list)       # producer keys
    gate: bool = False
    lane: str = "cpu"
    skip: str | None = None                        # why the task does not run


def task_key(stage, lang):
    return f"{stage}@{lang}" if lang else stage


def resolve_input(pattern, lang, producer):
    """(producer key, slot) for a consumes pattern, None when nothing produces it; optional flag."""
    optional = pattern.endswith("?")
    for slot in pattern.rstrip("?").split("|"):
        slot = slot.format(lang=lang)
        if slot in producer:
            return (producer[slot], slot), optional
    return None, optional


def plan(stages, langs):
    """TaskSpecs for resolved StageConfs (see template.resolve) and target languages, in stage order.
    Tasks that can't or needn't run are kept with `skip` set, so the user sees why."""
    producer = {}   # slot → key of the last task producing it
    specs = []
    for conf in stages:
        if not conf.enabled:
            continue
        cls = drivers.get(conf.stage, conf.driver)
        for lang in (langs if cls.per_lang() else [None]):
            spec = TaskSpec(task_key(conf.stage, lang), conf.stage, conf.driver, lang, dict(conf.params),
                            gate=conf.gate == "review", lane=drivers.lane(cls))
            specs.append(spec)
            if lang and not cls.supports(lang):
                spec.skip = f"{cls.title or cls.name} không hỗ trợ ngôn ngữ {lang}"
                continue
            missing = []
            for name, pattern in cls.consumes.items():
                found, optional = resolve_input(pattern, lang, producer)
                if found:
                    spec.inputs[name] = list(found)
                elif not optional:
                    missing.append(pattern.format(lang=lang))
            if missing:
                spec.skip = f"Thiếu đầu vào: {', '.join(missing)}"
                continue
            spec.outputs = cls.outputs(conf.params, lang)
            read = {slot for _key, slot in spec.inputs.values()}
            if spec.outputs and all(s in producer and s not in read for s in spec.outputs.values()):
                spec.skip = "Đã có sẵn từ nguồn"
                continue
            spec.deps = sorted({key for key, _slot in spec.inputs.values()})
            for slot in spec.outputs.values():
                producer[slot] = spec.key
    return specs


def final_producers(specs):
    """slot → key of the task whose output is the job's result for that slot."""
    producer = {}
    for spec in specs:
        if not spec.skip:
            for slot in spec.outputs.values():
                producer[slot] = spec.key
    return producer
