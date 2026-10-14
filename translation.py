# Translate SRT subtitles from English to Vietnamese with a small local NMT model
# running on CTranslate2 (int8, CPU). No server needed.
#
# NMT models translate whole sentences, but a sentence may be split over several cues,
# so cues are first joined into sentences, translated, then the translation is split
# back over the original cues by character length. Timings and cue count never change.
import re
from pathlib import Path

from srt import Subtitle

MODELS = {
    # name: (Hugging Face id, tokenizer options, target prefix)
    "vinai": ("vinai/vinai-translate-en2vi-v2", {"src_lang": "en_XX"}, ["vi_VN"]),
    "opus": ("Helsinki-NLP/opus-mt-en-vi", {}, None),
}
DEFAULT_MODEL = "vinai"
MODELS_DIR = Path(__file__).parent / "models"
SENTENCE_END = (".", "?", "!", "…", '."', '?"', '!"')
MAX_SENTENCE_CHARS = 400


class Translator:
    def __init__(self, name=DEFAULT_MODEL, beam_size=4):
        import ctranslate2
        import transformers

        repo, tokenizer_options, self.prefix = MODELS[name]
        model_dir = MODELS_DIR / name
        if not model_dir.exists():
            print(f"Converting {repo} to CTranslate2 int8 (one time)…")
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

    def translate_subtitles(self, subtitles):
        groups = group_sentences(subtitles)
        texts = [" ".join(clean(subtitles[i].content) for i in group) for group in groups]
        translated = []
        for group, translation in zip(groups, self.translate(texts)):
            parts = split_like(translation, [clean(subtitles[i].content) for i in group])
            for index, part in zip(group, parts):
                cue = subtitles[index]
                translated.append(Subtitle(index=cue.index, start=cue.start, end=cue.end, content=part))
        return translated


def make_translator(engine="local", model=DEFAULT_MODEL, beam_size=4, openai_model=None, workers=4,
                    claude_model=None):
    """Local CTranslate2 model, the OpenAI API (openai_translation.py) or Claude Code (claude_translation.py)."""
    if engine == "claude":
        from claude_translation import ClaudeCodeTranslator
        return ClaudeCodeTranslator(claude_model, workers=min(workers, 3))
    if engine == "openai":
        from openai_translation import OpenAITranslator
        return OpenAITranslator(openai_model, workers=workers)
    return Translator(model, beam_size=beam_size)


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def group_sentences(subtitles):
    """Indexes of consecutive cues that together form one sentence."""
    groups, current, length = [], [], 0
    for index, cue in enumerate(subtitles):
        current.append(index)
        length += len(cue.content)
        if clean(cue.content).endswith(SENTENCE_END) or length > MAX_SENTENCE_CHARS:
            groups.append(current)
            current, length = [], 0
    if current:
        groups.append(current)
    return groups


def split_like(text, sources):
    """Split text into len(sources) parts at word boundaries, sized like the sources."""
    words = text.split()
    if len(sources) == 1 or not words:
        return [text] + [""] * (len(sources) - 1)
    total = sum(len(s) for s in sources) or 1
    ends, position = [], 0
    for word in words:
        position += len(word) + 1
        ends.append(position)

    cuts, start, covered = [], 0, 0
    for k, source in enumerate(sources[:-1]):
        covered += len(source)
        target = ends[-1] * covered / total
        # keep at least one word for this part and for each part after it
        low = min(start + 1, len(words))
        high = max(low, len(words) - (len(sources) - k - 1))
        start = min(range(low, high + 1), key=lambda i: abs(ends[i - 1] - target))
        cuts.append(start)
    bounds = [0] + cuts + [len(words)]
    return [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:])]
