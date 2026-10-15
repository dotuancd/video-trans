# Fix recurring ASR mistakes with a replacement list (names, terms): "cloud code" → "Claude Code".
import re

from pydantic import BaseModel, Field

from dich.drivers import Driver, register
from dich.formats.transcript import Transcript


class Params(BaseModel):
    replacements: dict[str, str] = Field(default_factory=dict,
                                         description="Cụm sai → cụm đúng, không phân biệt hoa thường")


def apply(text, rules):
    for wrong, right in rules:
        text = wrong.sub(right, text)
    return text


@register
class RulesRefine(Driver):
    stage = "refine"
    name = "rules"
    title = "Từ điển thay thế"
    Params = Params
    consumes = {"transcript": "transcript.src"}
    produces = {"transcript": "transcript.src"}

    def run(self, ctx, inputs, params):
        transcript = Transcript.load(inputs["transcript"].path)
        rules = [(re.compile(rf"\b{re.escape(wrong)}\b", re.IGNORECASE), right)
                 for wrong, right in params.replacements.items() if wrong.strip()]
        changed = 0
        for cue in transcript.cues:
            text = apply(cue.text, rules)
            if text != cue.text:
                changed += 1
                if cue.words and len(text.split()) != len(cue.words):
                    cue.words = None    # timings of words no longer match
                elif cue.words:
                    for word, new in zip(cue.words, text.split()):
                        word["w"] = new
                cue.text = text
        ctx.log.info(f"Sửa {changed}/{len(transcript.cues)} câu")
        return {"transcript": transcript.save(ctx.output("transcript.json"))}
