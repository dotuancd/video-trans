# Translate SRT subtitles from English to Vietnamese through the Claude Code CLI (`claude -p`),
# so usage counts against a Claude Pro/Max plan instead of being billed per token.
#
# ANTHROPIC_API_KEY is removed from the child env so the CLI always uses the claude.ai login.
# Tools, MCP servers, skills, settings and CLAUDE.md are all turned off: each call is one plain
# completion. Chunking, retries and id checks come from LLMTranslator (openai_translation.py).
import json
import os
import shutil
import subprocess
import tempfile

from openai_translation import SCHEMA, SYSTEM_PROMPT, LLMTranslator

DEFAULT_CLAUDE_MODEL = "sonnet"
TIMEOUT = 600


class ClaudeCodeTranslator(LLMTranslator):
    # every call pays a few seconds of CLI start-up, so send bigger chunks than the OpenAI engine
    chunk_size = 100

    def __init__(self, model=None, workers=3, effort="medium"):
        if not shutil.which("claude"):
            raise SystemExit("Claude Code CLI not found, install it and run `claude` once to log in")
        super().__init__(workers)
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.effort = effort
        self.env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # an empty folder as cwd so no project CLAUDE.md is picked up
        self.cwd = tempfile.mkdtemp(prefix="claude-translate-")

    def complete(self, payload):
        command = [
            "claude", "-p", "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA["schema"]),
            "--system-prompt", SYSTEM_PROMPT,
            "--tools", "", "--model", self.model, "--effort", self.effort,
            "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands",
        ]
        process = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                 text=True, env=self.env, cwd=self.cwd, timeout=TIMEOUT)
        try:
            reply = json.loads(process.stdout)
        except ValueError:
            raise RuntimeError(f"claude failed: {(process.stderr or process.stdout).strip()[-300:]}")
        if process.returncode != 0 or reply.get("is_error"):
            # usage limit, auth or API errors: retrying would not help
            raise RuntimeError(f"claude failed: {reply.get('result') or reply.get('subtype')}")
        output = reply.get("structured_output")
        if output is None:
            try:
                output = json.loads(reply.get("result") or "")
            except ValueError:
                return None
        translations = output.get("translations") if isinstance(output, dict) else None
        return translations if isinstance(translations, list) else None
