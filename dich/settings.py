# Settings that differ per machine/user, read from the environment. A .env at the repository root is
# loaded on import (see .env.example); variables already set in the shell win over .env.
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


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
ENGINE = env("DICH_ENGINE", "local")                  # translate driver: local | openai | claude
LOCAL_MODEL = env("DICH_LOCAL_MODEL", "vinai")        # vinai | opus
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-5-mini")
OPENAI_PRICE = env("OPENAI_PRICE", "")                # "input,cached,output" USD per 1M tokens, for the cost log
CLAUDE_MODEL = env("DICH_CLAUDE_MODEL", "sonnet")     # sonnet | haiku | opus

# dubbing
VOICE = env("DICH_VOICE", "Phạm Tuyên")

# web UI
UI_ROOT = env_path("DICH_UI_ROOT", "~")
UI_PORT = int(env("DICH_UI_PORT", "7860"))

# workers per lane (dich serve / dich worker)
WORKERS = {
    "cpu": int(env("DICH_WORKERS_CPU", "1")),
    "net": int(env("DICH_WORKERS_NET", "3")),
    "gpu": int(env("DICH_WORKERS_GPU", "0")),
    "interactive": int(env("DICH_WORKERS_INTERACTIVE", "1")),
}

# where things are kept
MODELS_DIR = env_path("DICH_MODELS_DIR", ROOT / "models")                 # local translation models
DATA_DIR = env_path("DICH_DATA_DIR", "~/.local/share/dich-video")         # database, job workspaces, uploads
CACHE_DIR = env_path("DICH_CACHE_DIR", "~/.cache/dich-video")             # TTS sentence cache, voice clips
DB_PATH = DATA_DIR / "dich.sqlite"
KEEP_DAYS = int(env("DICH_KEEP_DAYS", "7"))   # days to keep keep="last" outputs of older runs (0: delete at once)
