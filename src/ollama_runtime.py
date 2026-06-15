# -*- coding: utf-8 -*-
"""Ensure the local Ollama server is running before AI generation.

The AI comment step needs an Ollama server reachable at ``base_url``.  Rather
than asking the user to start it by hand, this module checks reachability and,
if the server is down, locates the ``ollama`` executable and starts ``ollama
serve`` as a detached background process, then waits until the API answers.

It is intentionally dependency-free (urllib only) and degrades gracefully: if
Ollama is not installed or cannot be started, it returns a clear message and the
caller skips generation instead of crashing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


DEFAULT_BASE_URL = "http://localhost:11434"


def _api_get(base_url: str, path: str, timeout: float) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def is_running(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> bool:
    """Return True when the Ollama API answers /api/version."""
    return _api_get(base_url, "/api/version", timeout) is not None


def find_executable() -> Optional[str]:
    """Locate the ollama executable on PATH or in common install locations."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
        Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
        Path("/usr/bin/ollama"),
        Path.home() / ".local" / "bin" / "ollama",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def _spawn_server(executable: str) -> bool:
    """Start ``ollama serve`` detached so it outlives this process."""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= getattr(subprocess, name, 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([executable, "serve"], **kwargs)
        return True
    except OSError:
        return False


def ensure_running(
    base_url: str = DEFAULT_BASE_URL,
    *,
    wait_sec: int = 30,
    log=print,
) -> tuple[bool, str]:
    """Make sure the Ollama server is up, starting it if needed.

    Returns ``(ok, message)``.  ``ok`` is False when Ollama is not installed or
    failed to start within ``wait_sec`` seconds.
    """
    if is_running(base_url):
        return True, "Ollamaは既に起動しています。"

    executable = find_executable()
    if executable is None:
        return False, (
            "Ollamaが見つかりません。インストール済みか、PATHが通っているか確認してください "
            "(https://ollama.com)。"
        )

    log(f"Ollamaを起動します: {executable} serve")
    if not _spawn_server(executable):
        return False, f"Ollamaの起動に失敗しました: {executable}"

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if is_running(base_url):
            return True, "Ollamaを起動しました。"
        time.sleep(1.0)
    return False, f"Ollamaの起動を待ちましたが応答しません（{wait_sec}秒）。"


def list_models(base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0) -> list[str]:
    """Return installed model names (best effort)."""
    payload = _api_get(base_url, "/api/tags", timeout)
    if not payload:
        return []
    return [str(model.get("name", "")) for model in payload.get("models", []) if model.get("name")]


def _canonical_model(name: str) -> str:
    """Treat an untagged name as ':latest' so 'qwen3' == 'qwen3:latest'."""
    return name if ":" in name else f"{name}:latest"


def warm_model(
    model: str,
    base_url: str = DEFAULT_BASE_URL,
    *,
    keep_alive: str = "30m",
    wait_sec: int = 30,
    timeout: float = 300.0,
    log=print,
) -> tuple[bool, str]:
    """Start the server (if needed) and load ``model`` into memory.

    Sends a tiny 1-token request with ``keep_alive`` so the model stays resident
    in the Ollama server.  Because the server is a separate long-lived process,
    the model remains warm after this caller exits, making the first real
    generation call fast.  Best effort: returns ``(ok, message)``.
    """
    ok, message = ensure_running(base_url, wait_sec=wait_sec, log=log)
    if not ok:
        return False, message
    if model and not has_model(model, base_url):
        return False, f"モデル '{model}' が未取得のためウォームアップをスキップしました。"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ok"}],
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_predict": 1},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True, f"モデル '{model}' をウォームアップしました。"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"ウォームアップに失敗しました: {exc}"


def has_model(model: str, base_url: str = DEFAULT_BASE_URL) -> bool:
    """Return True if exactly ``model`` is installed.

    Tags are part of the identity: ``qwen3:8b`` and ``qwen3:32b`` are different
    models, so a different size of the same family does not count as a match.
    An untagged name is compared as its ``:latest`` form.
    """
    if not model:
        return True
    installed = list_models(base_url)
    if not installed:
        return False
    target = _canonical_model(model)
    return any(_canonical_model(name) == target for name in installed)
