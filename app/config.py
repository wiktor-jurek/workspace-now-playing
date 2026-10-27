"""Settings, read from environment variables."""

import os


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if not value:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


def _csv(name: str) -> set[str]:
    return {v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()}


BASE_URL = _env("BASE_URL").rstrip("/")
SECRET_KEY = _env("SECRET_KEY")

GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
SPOTIFY_CLIENT_ID = _env("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _env("SPOTIFY_CLIENT_SECRET")

DATABASE_PATH = _env("DATABASE_PATH", "/data/now-playing.db")
POLL_INTERVAL = float(_env("POLL_INTERVAL", "15"))
STATUS_EMOJI = _env("STATUS_EMOJI", "🎵")
STATUS_TTL = int(_env("STATUS_TTL", "600"))  # status self-expires if the service stops
CONCURRENCY = int(_env("CONCURRENCY", "10"))
SOURCE_URL = os.environ.get("SOURCE_URL", "")  # shown in the footer if set

# Optional sign-up allowlist. Both empty = anyone with a Google account may sign in.
ALLOWED_EMAILS = _csv("ALLOWED_EMAILS")
ALLOWED_DOMAINS = _csv("ALLOWED_DOMAINS")


def is_allowed(email: str) -> bool:
    if not ALLOWED_EMAILS and not ALLOWED_DOMAINS:
        return True
    email = email.lower()
    return email in ALLOWED_EMAILS or email.rsplit("@", 1)[-1] in ALLOWED_DOMAINS
