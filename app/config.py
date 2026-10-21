"""Settings for the Agent Shield demo. Reads a local .env file if present."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
STATIC = Path(__file__).resolve().parent / "static"


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_env()

PORT = int(os.getenv("PORT", "8000"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-4o-mini")

# How long the agent waits for an answer before it counts as a timeout.
AGENT_TIMEOUT = 1.5
# How many times the agent retries a payment that timed out.
AGENT_MAX_ATTEMPTS = 8


def ai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))
