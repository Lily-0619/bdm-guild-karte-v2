"""Ollama chat service used by Discord AI talk commands."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bot import config as bot_config

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = bot_config.OLLAMA_BASE_URL.rstrip("/")
OLLAMA_MODEL = bot_config.OLLAMA_MODEL
OLLAMA_NUM_CTX = bot_config.OLLAMA_NUM_CTX
OLLAMA_TIMEOUT_SECONDS = bot_config.OLLAMA_TIMEOUT_SECONDS

OLLAMA_CHAT_ENDPOINT = f"{OLLAMA_BASE_URL}/api/chat"

DEFAULT_OLLAMA_OPTIONS: Dict[str, Union[float, int]] = {
    "temperature": 0.2,
    "top_p": 0.8,
    "repeat_penalty": 1.15,
    "num_ctx": max(4096, OLLAMA_NUM_CTX),
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PERSONA_PROMPT_PATH = Path(
    getattr(
        bot_config,
        "PERSONA_PATH",
        PROJECT_ROOT / "config" / "m_persona.txt",
    )
)

DEFAULT_OLLAMA_SYSTEM_PROMPT = """
あなたはDiscord上で会話するAIアシスタントです。
通常会話では自然な日本語で返答してください。
ただし、/talk の language 指定や translate mode では、指定された形式と言語ルールを優先してください。
返答は短めで、親しみやすく、わかりやすくしてください。
分からないことは断定せず、情報が足りないと伝えてください。
秘密情報や個人情報を表示しないでください。
""".strip()


class OllamaConnectionError(Exception):
    """Raised when Ollama cannot be reached or returns an invalid response."""


class OllamaModelNotFoundError(Exception):
    """Raised when the configured Ollama model is not available."""


class OllamaResponseError(Exception):
    """Raised when Ollama returns an unexpected non-model error."""


def load_system_prompt() -> str:
    """Load M's persona prompt from config, falling back to a safe built-in prompt."""
    try:
        prompt = PERSONA_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("M persona prompt file was not found: %s", PERSONA_PROMPT_PATH)
        return DEFAULT_OLLAMA_SYSTEM_PROMPT

    return prompt or DEFAULT_OLLAMA_SYSTEM_PROMPT


SYSTEM_PROMPT = load_system_prompt()


def chat_with_ollama(message: str, history=None) -> str:
    """Send a chat request to Ollama and return the assistant message."""
    messages = build_messages(message, history)

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": DEFAULT_OLLAMA_OPTIONS,
    }

    request = Request(
        OLLAMA_CHAT_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        handle_http_error(exc)
    except URLError as exc:
        raise OllamaConnectionError("Ollamaに接続できません。") from exc
    except TimeoutError as exc:
        raise OllamaConnectionError("Ollamaの応答がタイムアウトしました。") from exc

    try:
        data = json.loads(body)
        reply = data["message"]["content"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.exception("Ollama returned an unexpected response: %s", body)
        raise OllamaConnectionError("Ollamaの応答形式が不正です。") from exc

    if not isinstance(reply, str) or not reply.strip():
        raise OllamaConnectionError("Ollamaから空の返答が返りました。")

    return reply.strip()


def build_messages(message: str, history=None) -> List[Dict[str, str]]:
    """Build an Ollama chat message list with system prompt and optional history."""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    if history:
        messages.extend(sanitize_history(history))

    messages.append({"role": "user", "content": message})
    return messages


def sanitize_history(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Keep only safe role/content pairs for Ollama chat history."""
    sanitized: List[Dict[str, str]] = []

    for item in history:
        role = item.get("role")
        content = item.get("content")

        if role not in {"system", "user", "assistant"}:
            continue

        if not isinstance(content, str):
            continue

        content = content.strip()
        if not content:
            continue

        sanitized.append({"role": role, "content": content})

    return sanitized


def handle_http_error(exc: HTTPError) -> None:
    """Translate Ollama HTTP errors into bot-specific exceptions."""
    body = read_error_body(exc)
    lower_body = body.lower()

    if exc.code == HTTPStatus.NOT_FOUND or (
        "model" in lower_body and "not found" in lower_body
    ):
        raise OllamaModelNotFoundError(
            f"指定されたOllamaモデルが見つかりません: {OLLAMA_MODEL}"
        ) from exc

    raise OllamaResponseError(
        f"Ollamaがエラーを返しました: HTTP {exc.code} {body}"
    ) from exc


def read_error_body(exc: HTTPError) -> str:
    """Read an HTTP error body without letting decoding errors crash the bot."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to read Ollama HTTP error body")
        return ""
