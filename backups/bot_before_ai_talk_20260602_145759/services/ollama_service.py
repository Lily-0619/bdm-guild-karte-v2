"""Ollama chat API client for the Discord /talk command."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

OLLAMA_BASE_URL_ENV_NAME = "OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV_NAME = "OLLAMA_MODEL"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_CHAT_PATH = "/api/chat"
OLLAMA_TIMEOUT_SECONDS = 120
SYSTEM_PROMPT = (
    "あなたはDiscord上で会話する日本語アシスタントです。"
    "必ず日本語で返答してください。"
    "中国語・英語では返答しないでください。"
    "返答は短めで、親しみやすく、わかりやすくしてください。"
)


class OllamaError(RuntimeError):
    """Base error for Ollama chat failures."""


class OllamaConnectionError(OllamaError):
    """Raised when the Ollama server cannot be reached."""


class OllamaModelNotFoundError(OllamaError):
    """Raised when the configured Ollama model is unavailable."""


def chat_with_ollama(message: str) -> str:
    """Send one user message to Ollama and return the assistant response."""
    base_url = os.getenv(OLLAMA_BASE_URL_ENV_NAME, DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    model = os.getenv(OLLAMA_MODEL_ENV_NAME, DEFAULT_OLLAMA_MODEL)
    endpoint = f"{base_url}{OLLAMA_CHAT_PATH}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": message,
            },
        ],
        "stream": False,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = _read_error_body(exc)
        if exc.code == 404 or "not found" in error_body.lower():
            raise OllamaModelNotFoundError("Configured Ollama model was not found.") from exc
        raise OllamaConnectionError(f"Ollama returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise OllamaConnectionError("Could not connect to Ollama.") from exc
    except TimeoutError as exc:
        raise OllamaConnectionError("Ollama request timed out.") from exc

    data = json.loads(response_body)
    return data["message"]["content"]


def _read_error_body(error: urllib.error.HTTPError) -> str:
    """Read an HTTPError body without exposing request secrets."""
    try:
        return error.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
