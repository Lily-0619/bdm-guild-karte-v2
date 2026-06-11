# -*- coding: utf-8 -*-
"""Ollama chat APIを使ったAutoComment生成処理。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .autocomment_prompt import build_guild_prompt
    from .paths import CONFIG_DIR
except ImportError:  # 直接実行された場合のため
    from autocomment_prompt import build_guild_prompt
    from paths import CONFIG_DIR  # type: ignore


DEFAULT_CONFIG_PATH = CONFIG_DIR / "autocomment_ai.json"


class OllamaError(RuntimeError):
    """Ollama呼び出しに失敗した場合の例外。"""


def load_ai_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """AI設定JSONを読み込む。"""
    with config_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _extract_json_object(text: str) -> Dict[str, Any]:
    """モデル応答からJSONオブジェクトを取り出す。"""
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise OllamaError("Ollama応答からJSONを抽出できませんでした。")
        parsed = json.loads(stripped[start : end + 1])

    if not isinstance(parsed, dict):
        raise OllamaError("Ollama応答JSONがオブジェクトではありません。")
    return parsed


def _normalize_comment(raw_comment: Dict[str, Any], guild_name: str) -> Dict[str, Any]:
    """期待するコメント形式に整える。"""
    attention_points = raw_comment.get("attention_points", [])
    if isinstance(attention_points, str):
        attention_points = [attention_points]
    if not isinstance(attention_points, list):
        attention_points = []

    return {
        "guild_name": str(raw_comment.get("guild_name") or guild_name),
        "short_comment": str(raw_comment.get("short_comment") or ""),
        "normal_comment": str(raw_comment.get("normal_comment") or ""),
        "detail_comment": str(raw_comment.get("detail_comment") or ""),
        "attention_points": [str(point) for point in attention_points],
        "tone": str(raw_comment.get("tone") or "情報不足"),
    }


def call_ollama_chat(
    messages: List[Dict[str, str]],
    config: Dict[str, Any],
    *,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Ollama /api/chat を urllib.request で呼び出す。"""
    base_url = str(config.get("base_url") or "http://localhost:11434").rstrip("/")
    endpoint = f"{base_url}/api/chat"
    timeout = int(timeout_sec or config.get("timeout_sec") or 120)

    payload = {
        "model": config.get("model") or "qwen3:8b",
        "messages": messages,
        "stream": False,
        "format": config.get("output_format") or "json",
        "options": {
            "temperature": float(config.get("temperature", 0.4)),
        },
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise OllamaError(f"Ollama接続に失敗しました: {exc}") from exc

    try:
        response_json = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise OllamaError("OllamaレスポンスがJSONではありません。") from exc

    content = response_json.get("message", {}).get("content", "")
    if not content:
        raise OllamaError("Ollamaレスポンスにmessage.contentがありません。")
    return _extract_json_object(content)


def generate_guild_comment(
    material: Dict[str, Any], guild_name: str, config: Dict[str, Any]
) -> Dict[str, Any]:
    """1ギルド分のAIコメントを生成する。"""
    messages = build_guild_prompt(material, guild_name)
    raw_comment = call_ollama_chat(messages, config)
    return _normalize_comment(raw_comment, guild_name)


def generate_all_comments(
    material: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """材料JSON内の全ギルドに対してAIコメントを生成する。"""
    comments: List[Dict[str, Any]] = []
    errors: List[str] = []

    guild_materials = material.get("guild_materials", {})
    if guild_materials:
        iterable = guild_materials.items()
    else:
        iterable = ((guild_name, material) for guild_name in material.get("guilds", {}).keys())

    for guild_name, guild_material in iterable:
        try:
            comments.append(generate_guild_comment(guild_material, guild_name, config))
        except OllamaError as exc:
            errors.append(f"{guild_name}: {exc}")

    return {
        "old_date": material.get("old_date"),
        "new_date": material.get("new_date"),
        "provider": config.get("provider", "ollama"),
        "model": config.get("model"),
        "comments": comments,
        "errors": errors,
    }


def main() -> int:
    """既存の材料JSONからAIコメントだけを再生成するCLI。"""
    import argparse

    parser = argparse.ArgumentParser(description="AutoComment材料JSONからOllamaコメントを生成します。")
    parser.add_argument("material_json", type=Path, help="autocomment_material_*.json のパス")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="AI設定JSONのパス")
    parser.add_argument("--output", type=Path, default=None, help="出力先JSON。省略時は材料JSONと同じフォルダに出力")
    args = parser.parse_args()

    with args.material_json.open("r", encoding="utf-8") as file_obj:
        material = json.load(file_obj)
    config = load_ai_config(args.config)
    result = generate_all_comments(material, config)

    output_path = args.output
    if output_path is None:
        old_date = material.get("old_date", "old")
        new_date = material.get("new_date", "new")
        output_path = args.material_json.parent / f"ai_comments_{old_date}_to_{new_date}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
