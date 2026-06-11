"""Minimal Ollama chat client using only the Python standard library."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from bot.config import PERSONA_PATH

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_BASE_URL_ENV_NAME = "OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV_NAME = "OLLAMA_MODEL"
DEFAULT_OLLAMA_SYSTEM_PROMPT = (
    "あなたはDiscord上で会話するAIアシスタントです。"
    "通常会話では自然な日本語で返答してください。"
    "ただし、/talk の language 指定や translate mode では、指定された形式と言語ルールを優先してください。"
    "返答は短めで、親しみやすく、わかりやすくしてください。""
)
REQUEST_TIMEOUT_SECONDS = 120


def load_system_prompt() -> str:
    """Load the Mぼっと persona prompt, falling back to a built-in prompt."""
    try:
        persona = PERSONA_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_OLLAMA_SYSTEM_PROMPT

    if not persona:
        return DEFAULT_OLLAMA_SYSTEM_PROMPT
    return f"{DEFAULT_OLLAMA_SYSTEM_PROMPT}\n\n{persona}"


class OllamaError(Exception):
    """Base exception for Ollama service errors."""


class OllamaConnectionError(OllamaError):
    """Raised when Ollama cannot be reached."""


class OllamaModelNotFoundError(OllamaError):
    """Raised when the configured model is not available in Ollama."""


def chat_with_ollama(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Send one user message to Ollama and return the assistant response text."""
    base_url = os.getenv(OLLAMA_BASE_URL_ENV_NAME, DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    model = os.getenv(OLLAMA_MODEL_ENV_NAME, DEFAULT_OLLAMA_MODEL)
    endpoint = f"{base_url}/api/chat"
    messages = [
        {
            "role": "system",
            "content": load_system_prompt(),
        },
    ]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": message,
        }
    )
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404 or "model" in response_body.lower():
            raise OllamaModelNotFoundError(response_body) from exc
        raise OllamaError(response_body) from exc
    except urllib.error.URLError as exc:
        raise OllamaConnectionError(str(exc)) from exc
    except TimeoutError as exc:
        raise OllamaConnectionError(str(exc)) from exc

    try:
        data = json.loads(response_body)
        return data["message"]["content"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OllamaError("Ollama response did not contain message.content") from exc
