# Settings that differ per machine/user, read from the environment. A .env next to this file is
# loaded on import (see .env.example); variables already set in the shell win over .env.
import os
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env")


def load_env(path=ENV_FILE):
    """KEY=value lines from .env into os.environ, without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip().strip("\"'"))


def env(name, default):
    """$name, or default when unset or empty."""
    return os.environ.get(name) or default


def env_path(name, default):
    return Path(env(name, default)).expanduser()


load_env()

# translation (OPENAI_API_KEY / OPENAI_BASE_URL are read by the openai SDK itself)
ENGINE = env("DICH_ENGINE", "local")                  # local | openai | claude
LOCAL_MODEL = env("DICH_LOCAL_MODEL", "vinai")        # vinai | opus
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-5-mini")
CLAUDE_MODEL = env("DICH_CLAUDE_MODEL", "sonnet")     # sonnet | haiku | opus

# dubbing
VOICE = env("DICH_VOICE", "Phạm Tuyên")

# web UI
UI_ROOT = env_path("DICH_UI_ROOT", "~")
UI_PORT = int(env("DICH_UI_PORT", "7860"))

# where things are kept
MODELS_DIR = env_path("DICH_MODELS_DIR", Path(__file__).parent / "models")    # local translation models
DATA_DIR = env_path("DICH_DATA_DIR", "~/.local/share/dich-video")            # web UI jobs + uploaded voice clips
CACHE_DIR = env_path("DICH_CACHE_DIR", "~/.cache/dich-video")                # TTS sentence cache
