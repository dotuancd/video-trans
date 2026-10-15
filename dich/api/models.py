# Request/response schemas: the OpenAPI the web client is generated from (cd web && npm run api).
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["blocked", "queued", "running", "done", "failed", "cancelled", "skipped", "waiting_review",
                     "stale"]


class Task(BaseModel):
    id: int
    job_id: int
    key: str
    position: int
    stage: str
    driver: str
    lang: str | None
    params: dict[str, Any]
    inputs: dict[str, list[str]]
    outputs: dict[str, str]
    deps: list[str]
    lane: str
    gate: bool
    status: TaskStatus
    message: str | None
    error: str | None
    done: int | None
    total: int | None
    retries: int
    cancel_requested: bool
    current_attempt: int | None
    exports: list[str]
    updated_at: float
    artifacts: list["ArtifactInfo"] = Field(default_factory=list)


class ArtifactInfo(BaseModel):
    id: int
    name: str
    slot: str
    path: str
    size: int | None
    external: bool


class Job(BaseModel):
    id: int
    title: str
    source: str
    template: str
    langs: list[str]
    export_dir: str | None
    trace_id: str
    created_at: float
    status: Literal["blocked", "queued", "running", "done", "failed", "cancelled", "waiting_review", "stale"]
    tasks: list[Task]


class StageOverride(BaseModel):
    driver: str | None = None
    enabled: bool | None = None
    gate: str | None = None
    params: dict[str, Any] | None = None


class JobCreate(BaseModel):
    sources: list[str] = Field(description="Files, folders (their videos), or URLs")
    langs: list[str] = Field(default_factory=lambda: ["vi"])
    template: str = "default"
    overrides: dict[str, StageOverride] = Field(default_factory=dict)
    recursive: bool = False
    export: bool = Field(True, description="Copy results next to the source file")


class JobsCreated(BaseModel):
    jobs: list[int]
    skipped: list[str]


class AddLangs(BaseModel):
    langs: list[str]


class Rerun(BaseModel):
    mode: Literal["only", "cascade"] = "cascade"
    params: dict[str, Any] | None = None
    driver: str | None = None
    confirm: bool = Field(False, description="Replace edits made by hand")


class Attempt(BaseModel):
    id: int
    task_id: int
    n: int
    span_id: str
    origin: Literal["driver", "cache", "human"]
    status: str
    params: dict[str, Any]
    message: str | None
    error: str | None
    started_at: float
    finished_at: float | None


class Event(BaseModel):
    id: int
    job_id: int | None
    task_id: int | None
    attempt_id: int | None
    ts: float
    level: str
    type: str
    message: str | None
    data: Any | None


class DriverInfo(BaseModel):
    stage: str
    name: str
    title: str
    params_schema: dict[str, Any]
    available: str | None = Field(description="Why it can't run here, or null")
    langs: list[str] | None
    per_lang: bool
    lane: str


class TemplateInfo(BaseModel):
    name: str
    title: str
    description: str
    stages: list[dict[str, Any]]


class Language(BaseModel):
    code: str
    name: str


class Config(BaseModel):
    root: str
    languages: list[Language]
    default_langs: list[str]


class EditCue(BaseModel):
    id: str | None = None
    start: float
    end: float
    text: str
    source: str | None = None


class Word(BaseModel):
    word: str
    start: float
    end: float


class TranscriptView(BaseModel):
    lang: str | None
    task_id: int = Field(description="The task whose output is edited")
    source_task_id: int | None = Field(description="The task of the original transcript shown next to it")
    cues: list[EditCue]
    words: list[Word] | None = Field(description="Timed words of the original, for cutting cues where words are spoken")
    edited: bool


class TranscriptSave(BaseModel):
    cues: list[EditCue]


class TranscriptSaved(BaseModel):
    texts: int
    sources: int
    source_text_changed: bool


class Entry(BaseModel):
    name: str
    path: str
    dir: bool
    kind: Literal["dir", "media", "subtitle", "other"]


class Listing(BaseModel):
    path: str
    parent: str | None
    entries: list[Entry]


class Uploaded(BaseModel):
    id: str
    path: str
    kind: Literal["media", "subtitle", "other"]


class PathBody(BaseModel):
    path: str


class Voice(BaseModel):
    label: str
    name: str


class Preview(BaseModel):
    voice: str = "source"
    ref_audio: str | None = None
    int8: bool = False
    text: str | None = None
    job_id: int | None = Field(None, description="Clone from / read this job's video and subtitles")
    lang: str = "vi"
    path: str | None = Field(None, description="Or a video (with <name>.srt / <name>.vi.srt next to it)")
