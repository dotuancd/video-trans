# Speech recognition with needle-whistle (local, ~70× real time on CPU), with word timestamps.
from pydantic import BaseModel, Field

from dich.drivers import Driver, module_missing, register
from dich.formats import transcript as tr
from dich.media import ASR_SAMPLE_RATE, load_audio


class Params(BaseModel):
    language: str | None = Field(None, title="Ngôn ngữ nói", description="Ngôn ngữ nói, VD en; để trống = tự nhận")
    max_chars: int = Field(tr.MAX_CHARS, title="Ký tự tối đa mỗi câu", ge=20, le=200, description="Số ký tự tối đa một câu phụ đề")
    max_duration: float = Field(tr.MAX_DURATION, title="Thời lượng tối đa mỗi câu", ge=1, le=20, description="Thời lượng tối đa một câu (giây)")
    min_chars: int = Field(tr.MIN_CHARS, title="Câu ngắn tối thiểu", ge=0, le=60, description="Câu ngắn hơn thì ghép vào câu bên cạnh")


@register
class NeedleAsr(Driver):
    stage = "asr"
    name = "needle"
    title = "Needle Whistle (local)"
    Params = Params
    consumes = {"audio": "audio.speech|audio.original"}
    produces = {"transcript": "transcript.src"}

    @classmethod
    def available(cls):
        return module_missing("needle")

    def run(self, ctx, inputs, params):
        """One needle call, no progress: splitting the audio changes the transcript (3–9% of words,
        sentence-ending punctuation most) because the model loses context."""
        import needle

        audio = load_audio(inputs["audio"].path)
        seconds = len(audio) / 4 / ASR_SAMPLE_RATE
        ctx.log.info(f"Nhận dạng {seconds / 60:.1f} phút audio…")
        # needle writes the JSON result into a fixed buffer (256 KB by default) and cuts it off there:
        # ~40 min of words with timestamps. Words take ~250 B per second of speech; allow 1 KB.
        whistle = needle.Whistle(buffer_size=max(1 << 18, int(seconds) * 1024))
        result = whistle.transcribe(audio, language=params.language or None, word_timestamps=True)
        transcript = tr.from_words(result["words"], params.language or result.get("language") or "en",
                                   max_chars=params.max_chars, max_duration=params.max_duration,
                                   min_chars=params.min_chars)
        ctx.log.info(f"{len(transcript.cues)} câu phụ đề")
        return {"transcript": transcript.save(ctx.output("transcript.json"))}
