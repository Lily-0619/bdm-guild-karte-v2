"""Configuration constants for the Discord bot."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CARDS_DIR = PROJECT_ROOT / "output" / "cards"
TOKEN_ENV_NAME = "DISCORD_BOT_TOKEN"
GUILD_ID_ENV_NAME = "DISCORD_GUILD_ID"
