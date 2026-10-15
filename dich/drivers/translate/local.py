# Translate with a small local NMT model on CTranslate2 (int8, CPU). English → Vietnamese only.
#
# NMT models translate whole sentences, but a sentence may be split over several cues, so cues are first
# joined into sentences, translated, then the translation is split back over the cues by character length.
from typing import Literal

from pydantic import BaseModel, Field

from dich import settings
from dich.drivers import Driver, UserError, module_missing, register
from dich.drivers.translate.common import clean, group_sentences, split_like, translated
from dich.formats.transcript import Transcript

MODELS = {
    # name: (Hugging Face id, tokenizer options, target prefix)
    "vinai": ("vinai/vinai-translate-en2vi-v2", {"src_lang": "en_XX"}, ["vi_VN"]),
    "opus": ("Helsinki-NLP/opus-mt-en-vi", {}, None),
}
BATCH_SENTENCES = 64   # sentences per translate_batch call, between progress reports


class Translator:
    def __init__(self, name, beam_size=4, log=print):
        import ctranslate2
        import transformers

        repo, tokenizer_options, self.prefix = MODELS[name]
        model_dir = settings.MODELS_DIR / name
        if not model_dir.exists():
            log(f"Chuyển {repo} sang CTranslate2 int8 (một lần)…")
            converter = ctranslate2.converters.TransformersConverter(repo)
            converter.convert(str(model_dir), quantization="int8")
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(repo, **tokenizer_options)
        self.model = ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8")
        self.beam_size = beam_size

    def translate(self, sentences):
        if not sentences:
            return []
        sources = [self.tokenizer.convert_ids_to_tokens(self.tokenizer.encode(s)) for s in sentences]
        prefixes = [self.prefix] * len(sources) if self.prefix else None
        results = self.model.translate_batch(sources, target_prefix=prefixes, beam_size=self.beam_size,
                                             max_batch_size=32)
        outputs = []
        for result in results:
            tokens = [t for t in result.hypotheses[0] if t not in (self.prefix or [])]
            ids = self.tokenizer.convert_tokens_to_ids(tokens)
            outputs.append(self.tokenizer.decode(ids, skip_special_tokens=True).strip())
        return outputs

    def translate_cues(self, cues, progress=None):
        """Texts for cues; progress(done, total) after each batch of sentences."""
        groups = group_sentences(cues)
        texts = [" ".join(clean(cues[i].text) for i in group) for group in groups]
        translations = []
        for start in range(0, len(texts), BATCH_SENTENCES):
            translations += self.translate(texts[start:start + BATCH_SENTENCES])
            if progress:
                progress(len(translations), len(texts))
        out = [""] * len(cues)
        for group, translation in zip(groups, translations):
            for index, part in zip(group, split_like(translation, [clean(cues[i].text) for i in group])):
                out[index] = part
        return out


class Params(BaseModel):
    model: Literal["vinai", "opus"] = Field(settings.LOCAL_MODEL if settings.LOCAL_MODEL in MODELS else "vinai",
                                            description="vinai: tốt hơn; opus: nhẹ hơn")
    beam: int = Field(4, ge=1, le=8, description="1 = nhanh nhất")


@register
class LocalTranslate(Driver):
    stage = "translate"
    name = "local"
    title = "Model local (CTranslate2, Anh → Việt)"
    Params = Params
    consumes = {"transcript": "transcript.src"}
    produces = {"transcript": "transcript.{lang}"}
    langs = {"vi"}

    def __init__(self):
        self.translators = {}

    @classmethod
    def available(cls):
        return module_missing("ctranslate2", "transformers", "sentencepiece") and "Chưa cài: uv sync --extra local"

    def run(self, ctx, inputs, params):
        source = Transcript.load(inputs["transcript"].path)
        if source.lang not in (None, "en"):
            raise UserError(f"Model local chỉ dịch từ tiếng Anh (phụ đề gốc là {source.lang})")
        key = (params.model, params.beam)
        if key not in self.translators:
            ctx.log.info(f"Tải model dịch {params.model}…")
            self.translators[key] = Translator(params.model, params.beam, log=ctx.log.info)
        ctx.log.info(f"Dịch {len(source.cues)} câu")
        texts = self.translators[key].translate_cues(source.cues, ctx.progress)
        return {"transcript": translated(source, texts, ctx.lang).save(ctx.output("transcript.json"))}
