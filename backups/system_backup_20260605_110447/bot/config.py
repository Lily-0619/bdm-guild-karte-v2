"""Configuration constants for the Discord bot."""

from __future__ import annotations

from pathlib import Path

import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CARDS_DIR = PROJECT_ROOT / "output" / "cards"
PERSONA_PATH = PROJECT_ROOT / "config" / "m_persona.txt"
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
