# Drivers for tests: no models, files written instantly. Imported through $DICH_DRIVERS by worker processes.
import time
from pathlib import Path

from pydantic import BaseModel

from dich.drivers import Driver, UserError, register
from dich.formats.transcript import Cue, Transcript

RUNS = {}


def count(name):
    RUNS[name] = RUNS.get(name, 0) + 1


class AsrParams(BaseModel):
    fail: str | None = None     # "user" | "system"
    sleep: float = 0


@register
class FakeAsr(Driver):
    stage = "asr"
    name = "fake"
    Params = AsrParams
    consumes = {"audio": "audio.speech|audio.original"}
    produces = {"transcript": "transcript.src"}

    def run(self, ctx, inputs, params):
        count("asr")
        print("fake asr stdout line", flush=True)
        import os
        os.write(2, b"fd2 c-level line\n")
        if params.sleep:
            ctx.log.info("sleeping")
            time.sleep(params.sleep)
        if params.fail == "user":
            raise UserError("File hỏng")
        if params.fail == "system":
            raise ValueError("boom")
        ctx.log.info("2 câu")
        t = Transcript("en", [Cue("c_0001", 0, 1, "hello."), Cue("c_0002", 1, 2, "world.")])
        return {"transcript": t.save(ctx.output("transcript.jsonl"))}


@register
class FakeTranslate(Driver):
    stage = "translate"
    name = "fake"
    resources = {"net"}
    consumes = {"transcript": "transcript.src"}
    produces = {"transcript": "transcript.{lang}"}

    def run(self, ctx, inputs, params):
        count("translate")
        source = Transcript.load(inputs["transcript"].path)
        ctx.progress(1, 1)
        t = Transcript(ctx.lang, [Cue(c.id, c.start, c.end, f"{ctx.lang}:{c.text}", src_id=c.id) for c in source.cues])
        return {"transcript": t.save(ctx.output("transcript.jsonl"))}


@register
class FakeDub(Driver):
    stage = "dub"
    name = "fake"
    consumes = {"transcript": "transcript.{lang}", "voice_from": "audio.speech|audio.original?"}
    produces = {"audio": "audio.dub.{lang}"}
    keep = {"audio": "last"}
    langs = {"vi"}

    def run(self, ctx, inputs, params):
        count("dub")
        path = ctx.output("dub.wav")
        path.write_bytes(Path(inputs["transcript"].path).read_bytes())
        return {"audio": path}


@register
class FakeMux(Driver):
    stage = "mux"
    name = "fake"
    consumes = {"video": "media.video", "dub": "audio.dub.{lang}"}
    produces = {"video": "media.out.{lang}"}

    def run(self, ctx, inputs, params):
        path = ctx.output("out.mp4")
        path.write_bytes(Path(inputs["video"].path).read_bytes() + Path(inputs["dub"].path).read_bytes())
        return {"video": path}


@register
class FakeMindmap(Driver):
    stage = "mindmap"
    name = "fake"
    resources = {"net"}
    consumes = {"transcript": "transcript.src"}
    produces = {"mindmap": "mindmap"}

    def run(self, ctx, inputs, params):
        count("mindmap")
        import json
        source = Transcript.load(inputs["transcript"].path)
        end = source.cues[-1].end
        root = {"id": "root", "label": "Video", "start": 0, "end": end,
                "children": [{"id": "s1", "label": c.text, "start": c.start, "end": c.end} for c in source.cues]}
        path = ctx.output("mindmap.json")
        path.write_text(json.dumps({"lang": "vi", "root": root}), encoding="utf-8")
        return {"mindmap": path}
