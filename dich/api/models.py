# Request/response schemas: the OpenAPI the web client is generated from (cd web && npm run api).
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["blocked", "queued", "running", "done", "failed", "cancelled", "skipped", "waiting_review",
                     "stale"]


class Task(BaseModel):
    id: str
    job_id: str
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
    current_attempt: str | None
    exports: list[str]
    updated_at: float
    artifacts: list["ArtifactInfo"] = Field(default_factory=list)


class ArtifactInfo(BaseModel):
    id: str
    name: str
    slot: str
    path: str
    size: int | None
    external: bool
    download_name: str = ""


class Job(BaseModel):
    id: str
    title: str
    source: str
    template: str
    langs: list[str]
    export_dir: str | None
    trace_id: str
    created_at: float
    project_id: str | None = None
    status: Literal["blocked", "queued", "running", "done", "failed", "cancelled", "waiting_review", "stale"]
    tasks: list[Task]
    cost_usd: float | None = Field(None, description="USD paid to paid APIs (OpenAI…) over all attempts")


class JobIds(BaseModel):
    ids: list[str] = Field(description="Jobs to act on; unknown ids are ignored")


class JobPatch(BaseModel):
    project_id: str | None = Field(description="Move the job into this project; null: no project")


class JobsMove(JobIds):
    project_id: str | None = Field(description="Move the jobs into this project; null: no project")


class Skipped(BaseModel):
    id: str
    title: str
    reason: str


class StageOverride(BaseModel):
    driver: str | None = None
    enabled: bool | None = None
    gate: str | None = None
    params: dict[str, Any] | None = None


class PresetStage(BaseModel):
    driver: str | None = None
    enabled: bool | None = None
    params: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Params per driver name")


class PresetBody(BaseModel):
    export: bool = True
    stages: dict[str, PresetStage] = Field(default_factory=dict)


class Preset(PresetBody):
    """Named advanced options for new jobs: per stage its driver, on/off and params (applied to any template)."""
    name: str


class ProjectBody(BaseModel):
    """What new videos of the project run with by default; the web form sends it with each job it creates."""
    name: str
    color: str = "#14a394"
    template: str = "default"
    langs: list[str] = Field(default_factory=lambda: ["vi"])
    options: PresetBody = Field(default_factory=PresetBody, description="Advanced options, shaped like a preset")
    preset: str | None = Field(None, description="The preset the options were taken from")
    export_dir: str | None = Field(None, description="Results go here instead of next to the source file")


class Project(ProjectBody):
    id: str
    created_at: float
    jobs: int = Field(description="Number of jobs in the project")


class JobCreate(BaseModel):
    sources: list[str] = Field(description="Files, folders (their videos), or URLs")
    langs: list[str] = Field(default_factory=lambda: ["vi"])
    template: str = "default"
    overrides: dict[str, StageOverride] = Field(default_factory=dict)
    recursive: bool = False
    use_subtitles: bool = Field(False, description="Use <name>.srt / <name>.<lang>.srt found next to each video")
    export: bool = Field(True, description="Copy results next to the source file (or the project's export folder)")
    project: str | None = Field(None, description="The project the jobs go into")


class JobsCreated(BaseModel):
    jobs: list[str]
    skipped: list[str]


class AddLangs(BaseModel):
    langs: list[str]


class Rerun(BaseModel):
    mode: Literal["only", "cascade"] = "cascade"
    params: dict[str, Any] | None = None
    driver: str | None = None
    confirm: bool = Field(False, description="Replace edits made by hand")


class JobsDone(BaseModel):
    jobs: list[Job] = Field(description="Jobs acted on, as they are now")
    skipped: list[Skipped]
    needs_confirm: list[str] = Field(description="Jobs left alone: they would lose edits made by hand")


class JobsDeleted(BaseModel):
    deleted: list[str]
    skipped: list[Skipped]


class JobsRerun(JobIds):
    stage: str = Field(description="Every step of this stage (each language) runs again")
    mode: Literal["only", "cascade"] = "cascade"
    params: dict[str, Any] | None = Field(None, description="Only what changes; a job keeps its other params "
                                                             "(unless the driver changes)")
    driver: str | None = None
    confirm: bool = Field(False, description="Replace edits made by hand")


class Attempt(BaseModel):
    id: str
    task_id: str
    n: int
    span_id: str
    origin: Literal["driver", "cache", "human"]
    status: str
    params: dict[str, Any]
    message: str | None
    error: str | None
    started_at: float
    finished_at: float | None
    artifacts: list[ArtifactInfo] = []


class Event(BaseModel):
    id: int
    job_id: str | None
    task_id: str | None
    attempt_id: str | None
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
    default_drivers: dict[str, str] = Field(description="stage → driver used for 'env' in templates")


class QueueItem(BaseModel):
    id: str = Field(description="task id")
    job_id: str
    title: str = Field(description="the job's title")
    project_id: str | None
    key: str
    stage: str
    driver: str
    lang: str | None
    lane: str
    status: Literal["running", "queued"]
    done: int | None
    total: int | None
    retries: int
    cancel_requested: bool
    message: str | None
    updated_at: float = Field(description="when it was queued, or taken by a worker")
    started_at: float | None = Field(None, description="the running attempt's start")
    pid: int | None = Field(None, description="the worker running it")


class Worker(BaseModel):
    pid: int | None
    alive: bool
    retiring: bool = Field(False, description="no longer wanted (fewer workers set): exits after its task")


class Lane(BaseModel):
    name: str
    workers: list[Worker] | None = Field(description="the processes this server started; None when it started none"
                                                     " (workers run by `dich worker` are not known here)")
    configured: int = Field(description="processes wanted on this lane (set in the UI, else DICH_WORKERS_…)")
    scalable: bool = Field(description="this server starts the lane's workers, so their number can be changed")
    running: list[QueueItem]
    queued: list[QueueItem]


class WorkerCount(BaseModel):
    count: int = Field(ge=0, le=16)


class QueueView(BaseModel):
    lanes: list[Lane]
    blocked: int = Field(description="tasks waiting for an earlier step")
    waiting_review: int


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


class LiveChunk(BaseModel):
    n: int
    start: float
    end: float


class LiveAudio(BaseModel):
    """Back-to-back pieces of a dub track that is still being made (GET /tasks/{id}/live/{n})."""
    attempt_id: str
    chunks: list[LiveChunk]
    ready_until: float = Field(description="Seconds of the track playable so far")
    duration: float | None = Field(description="Expected length: end of the last cue")
    finished: bool


class TranscriptView(BaseModel):
    lang: str | None
    task_id: str = Field(description="The task whose output is edited")
    source_task_id: str | None = Field(description="The task of the original transcript shown next to it")
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
    job_id: str | None = Field(None, description="Clone from / read this job's video and subtitles")
    lang: str = "vi"
    path: str | None = Field(None, description="Or a video (with <name>.srt / <name>.vi.srt next to it)")
