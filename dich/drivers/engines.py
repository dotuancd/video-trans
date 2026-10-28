# The LLM engines the drivers that write with an LLM (brief, mindmap) ask: openai | claude, and "auto"
# going with the translation's driver.
import os
import shutil

from dich import settings

CLAUDE_MODELS = ("sonnet", "haiku", "opus")


def usable():
    """engine → whether it can run here (an OpenAI key, the claude CLI)."""
    return {"openai": bool(os.environ.get("OPENAI_API_KEY")), "claude": bool(shutil.which("claude"))}


def pick_engine(engine):
    """openai | claude, the first one usable for "auto"; None when neither is."""
    if engine != "auto":
        return engine
    return next((name for name, ok in usable().items() if ok), None)


def make_translator(engine):
    from dich.drivers.translate.llm import ClaudeCodeTranslator, OpenAITranslator
    if engine == "claude":
        model = settings.CLAUDE_MODEL if settings.CLAUDE_MODEL in CLAUDE_MODELS else "sonnet"
        return ClaudeCodeTranslator(model, workers=1, effort="low")
    return OpenAITranslator(settings.OPENAI_MODEL, workers=1)


def engine_of(translate_driver):
    """The engine that goes with a translate driver, None for one that is not an LLM."""
    if translate_driver == "claude":
        return "claude"
    if translate_driver and translate_driver.startswith("openai"):
        return "openai"
    return None
