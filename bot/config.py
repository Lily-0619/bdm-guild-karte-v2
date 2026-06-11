"""Configuration constants for the Discord bot."""

from __future__ import annotations

import os

from src.paths import CARDS_DIR, CONFIG_DIR, PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
PERSONA_PATH = CONFIG_DIR / "m_persona.txt"
TOKEN_ENV_NAME = "DISCORD_BOT_TOKEN"
GUILD_ID_ENV_NAME = "DISCORD_GUILD_ID"
NOTIFY_USER_ID_ENV_NAME = "DISCORD_NOTIFY_USER_ID"


# =========================
# Ollama settings
# =========================

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
