"""Shared configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Look for a `runtime.env` next to the backend package (shipped in the
    # Docker image) and a `.env` beside it for local development.
    for candidate in (
        Path(__file__).resolve().parent.parent / "runtime.env",
        Path(__file__).resolve().parent.parent / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)
except ImportError:
    pass


def _parse_ids(value: str) -> set[int]:
    out: set[int] = set()
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk and chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()

ADMIN_IDS: set[int] = _parse_ids(
    os.environ.get("BOT_ADMIN_IDS", "6903588929,6734219400")
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Fallback when /data is not writable (local dev without volume).
    DATA_DIR = Path(os.environ.get("FALLBACK_DATA_DIR", "/tmp/ai_bot_data"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

WHITELIST_FILE = Path(
    os.environ.get("WHITELIST_FILE") or (DATA_DIR / "whitelist.json")
)

FRONTEND_URL: str = os.environ.get("FRONTEND_URL", "").strip()

# URL opened from the bot's "🚀 Открыть приложение" WebApp button.
BOT_WEBAPP_URL: str = (
    os.environ.get("BOT_WEBAPP_URL", "").strip() or FRONTEND_URL
)

CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
]
if FRONTEND_URL and FRONTEND_URL not in CORS_ORIGINS:
    CORS_ORIGINS.append(FRONTEND_URL)

MAX_FILE_SIZE: int = int(os.environ.get("MAX_FILE_SIZE", str(20 * 1024 * 1024)))
RUN_BOT: bool = os.environ.get("RUN_BOT", "1") != "0"

# initData TTL in seconds (Telegram docs recommend 24h)
INIT_DATA_TTL: int = int(os.environ.get("INIT_DATA_TTL", "86400"))
