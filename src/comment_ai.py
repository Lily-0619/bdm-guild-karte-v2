# -*- coding: utf-8 -*-
"""Generate per-guild AI comments from the comment-source material via Ollama.

This is the step after ``comment_source.py``: it takes the per-guild combined
material (guild aggregate + whole-server comparison + member detail) and asks
the local Ollama model to write a guild comment in three lengths
(short / normal / detail).  All three are saved so the card can use any of them.

Results go next to the comment-source output, keyed by summary date:
``analysis/comment_source/<date>/ai_comments.json`` (+ a readable per-guild md).
``make_card.py`` reads that file and drops the chosen length into the card.

The Ollama plumbing and output shaping are reused from ``autocomment_ollama``;
the prompt schema/system text from ``autocomment_prompt``; the material from
``comment_source``.  Each guild is generated independently so one failure (or a
stopped Ollama server) never aborts the whole run.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from .paths import ANALYSIS_DIR, CONFIG_DIR, ensure_dirs
    from . import comment_source as cs
    from . import ollama_runtime
    from .autocomment_ollama import OllamaError, call_ollama_chat, load_ai_config, _normalize_comment
    from .autocomment_prompt import COMMENT_SCHEMA, SYSTEM_PROMPT
except ImportError:  # 直接実行された場合のため
    from paths import ANALYSIS_DIR, CONFIG_DIR, ensure_dirs  # type: ignore
    import comment_source as cs  # type: ignore
    import ollama_runtime  # type: ignore
    from autocomment_ollama import OllamaError, call_ollama_chat, load_ai_config, _normalize_comment  # type: ignore
    from autocomment_prompt import COMMENT_SCHEMA, SYSTEM_PROMPT  # type: ignore


CONFIG_PATH = CONFIG_DIR / "autocomment_ai.json"

COMMENT_GUIDELINES = {
    "short_comment": "1文で要点のみ。",
    "normal_comment": "2〜4文で、全体と比べた強さ・前回比の伸び・構成の雰囲気（まったり/本気度）を書く。",
    "detail_comment": "良い点・注意点・次に見るべき点を、材料に基づいて具体的に複数文で書く。",
    "attention_points": "重要な注目点を2〜5個の配列にする。",
    "tone": "好調/安定/注意/停滞/情報不足 など短い状態タグ。",
}


def select_summaries(new_date: Optional[str], old_date: Optional[str]):
    """Resolve (new_summary_path, old_summary_path, new_date, old_date)."""
    summaries = cs.list_summary_paths()
    if not summaries:
        raise FileNotFoundError(
            f"analysis/summary_*.xlsx が見つかりません。先に「一覧作成」を実行してください: {ANALYSIS_DIR}"
        )
    if new_date:
        new_summary = cs.summary_path_for_date(new_date)
        if new_summary is None:
            raise FileNotFoundError(f"指定日のサマリーが見つかりません: summary_{new_date}.xlsx")
    else:
        new_summary = summaries[-1]
    new_date = cs.summary_date_from_path(new_summary)

    if old_date:
        old_summary = cs.summary_path_for_date(old_date)
    else:
        earlier = [p for p in summaries if cs.summary_date_from_path(p) < new_date]
        old_summary = earlier[-1] if earlier else None
    old_date_str = cs.summary_date_from_path(old_summary) if old_summary else None
    return new_summary, old_summary, new_date, old_date_str


def build_ai_material_text(record: dict[str, Any], new_date: str, old_date: Optional[str]) -> str:
    """A compact, AI-facing version of the material (no full member table).

    The viewable Markdown keeps the whole roster, but for generation we only need
    the aggregate, the whole-server comparison phrases, and a few member
    highlights -- a much smaller prompt that the model answers far faster.
    """
    row = record["summary_row"]
    lines: list[str] = [f"# {record['guild_name']} コメント材料"]
    period = f"集計日: {new_date}"
    if old_date:
        period += f" / 前回比較日: {old_date}"
    lines.append(period)
    lines.append("")
    lines.append("## 全体の中での位置づけ")
    lines.append(f"- {record['phrases']['position']}")
    lines.append(f"- 構成傾向: {record['phrases']['power']}")
    lines.append(f"- 伸び: {record['phrases']['growth']}")
    lines.append("")
    lines.append("## 主要指標（全体比較込み）")
    for key, label in cs.GUILD_SUMMARY_FIELDS:
        value = record[key] if key in record else row.get(key)
        lines.append(f"- {label}: {value if value not in (None, '') else '不明'}")
    lines.append("")
    if record["growth_top"]:
        lines.append("## 伸び上位メンバー")
        for member in record["growth_top"]:
            lines.append(f"- {member['player_name']}: 前回比 +{member.get('growth')}（CPM {member.get('cpm')}）")
        lines.append("")
    if record["growth_bottom"]:
        lines.append("## 停滞・減少メンバー")
        for member in record["growth_bottom"]:
            lines.append(f"- {member['player_name']}: 前回比 {member.get('growth')}（CPM {member.get('cpm')}）")
        lines.append("")
    top_members = record["members"][:8]
    if top_members:
        lines.append("## 上位CPMメンバー")
        for member in top_members:
            lines.append(f"- {member['player_name']}: CPM {member.get('cpm')}")
        lines.append("")
    return "\n".join(lines)


def build_messages(record: dict[str, Any], new_date: str, old_date: Optional[str]) -> list[dict[str, str]]:
    """Build the Ollama chat messages for one guild from its material."""
    material_text = build_ai_material_text(record, new_date, old_date)
    instruction = {
        "task": "BDMギルド別コメント作成",
        "guild_name": record["guild_name"],
        "required_output_schema": COMMENT_SCHEMA,
        "comment_guidelines": COMMENT_GUIDELINES,
        "writing_style": (
            "全体と比べた強さ・伸び・構成の雰囲気（まったり/本気度）・注目メンバーが伝わる自然な日本語。"
            "事務的すぎず煽りすぎず。材料にない事は断定しない。"
        ),
    }
    user_content = (
        material_text
        + "\n\n---\n以下の指示に従い、JSONのみで返してください。\n"
        + json.dumps(instruction, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def empty_comment(guild_name: str) -> dict[str, Any]:
    return {
        "guild_name": guild_name,
        "short_comment": "",
        "normal_comment": "",
        "detail_comment": "",
        "attention_points": [],
        "tone": "情報不足",
    }


def generate_comments(
    records: list[dict[str, Any]],
    new_date: str,
    old_date: Optional[str],
    *,
    skip_ai: bool,
    model: Optional[str] = None,
) -> dict[str, Any]:
    comments: dict[str, Any] = {}
    errors: list[str] = []
    config: dict[str, Any] = {"provider": "ollama", "model": None}

    if skip_ai:
        errors.append("--skip-ai が指定されたため、Ollama生成は実行していません（雛形のみ）。")
    else:
        try:
            config = load_ai_config(CONFIG_PATH)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            skip_ai = True
            errors.append(f"AI設定読み込みに失敗しました: {exc}")

    if model:  # GUI/CLIで選ばれたモデルを優先する。
        config["model"] = model

    if not skip_ai:
        base_url = str(config.get("base_url") or ollama_runtime.DEFAULT_BASE_URL)
        ok, message = ollama_runtime.ensure_running(base_url)
        print(message)
        if not ok:
            skip_ai = True
            errors.append(message)
        else:
            model = str(config.get("model") or "")
            if model and not ollama_runtime.has_model(model, base_url):
                installed = ollama_runtime.list_models(base_url)
                warning = (
                    f"モデル '{model}' が未取得です。`ollama pull {model}` を実行するか、"
                    f"config/autocomment_ai.json の model を導入済みモデルに変更してください。"
                    f" 導入済み: {', '.join(installed) if installed else 'なし'}"
                )
                print(warning)
                errors.append(warning)
                skip_ai = True  # 全ギルドが同じ失敗を繰り返すのを避ける

    total = len(records)
    for index, record in enumerate(records, start=1):
        guild_name = record["guild_name"]
        # GUIが進捗バー/残り時間に使う機械可読の進捗行（順序: 番号/総数 ギルド名）。
        print(f"[進捗] {index}/{total} {guild_name}", flush=True)
        if skip_ai:
            comments[guild_name] = empty_comment(guild_name)
            continue
        try:
            messages = build_messages(record, new_date, old_date)
            raw = call_ollama_chat(messages, config)
            comments[guild_name] = _normalize_comment(raw, guild_name)
        except (OllamaError, json.JSONDecodeError, KeyError, ValueError) as exc:
            errors.append(f"{guild_name}: {exc}")
            comments[guild_name] = empty_comment(guild_name)
        except Exception as exc:  # 1ギルドの想定外失敗で全体を止めない
            errors.append(f"{guild_name}: 想定外エラー: {exc}")
            comments[guild_name] = empty_comment(guild_name)

    return {
        "new_date": new_date,
        "old_date": old_date,
        "provider": config.get("provider", "ollama"),
        "model": config.get("model"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "comments": comments,
        "errors": errors,
    }


def write_comment_markdown(out_dir: Path, guild_name: str, comment: dict[str, Any]) -> None:
    lines = [
        f"# {guild_name} AIコメント",
        "",
        f"状態タグ: {comment.get('tone', '')}",
        "",
        "## 短文",
        comment.get("short_comment", "") or "(なし)",
        "",
        "## 通常",
        comment.get("normal_comment", "") or "(なし)",
        "",
        "## 詳細",
        comment.get("detail_comment", "") or "(なし)",
        "",
        "## 注目点",
    ]
    points = comment.get("attention_points", []) or []
    if points:
        lines.extend(f"- {point}" for point in points)
    else:
        lines.append("- (なし)")
    lines.append("")
    file_name = f"ai_comment_{cs.safe_filename(guild_name)}.md"
    (out_dir / file_name).write_text("\n".join(lines), encoding="utf-8")


def run(
    new_date: Optional[str] = None,
    old_date: Optional[str] = None,
    *,
    skip_ai: bool = False,
    model: Optional[str] = None,
) -> Path:
    ensure_dirs()
    new_summary, old_summary, new_date, old_date_str = select_summaries(new_date, old_date)
    records = cs.build_guild_records(new_summary, old_summary)

    result = generate_comments(records, new_date, old_date_str, skip_ai=skip_ai, model=model)

    out_dir = ANALYSIS_DIR / "comment_source" / new_date
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ai_comments.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for guild_name, comment in result["comments"].items():
        write_comment_markdown(out_dir, guild_name, comment)

    generated = sum(1 for c in result["comments"].values() if c.get("normal_comment"))
    print("AIコメント生成が完了しました。")
    print(f"対象サマリー: {new_summary.name}" + (f" / 前回: {old_summary.name}" if old_summary else " / 前回: なし"))
    print(f"モデル: {result.get('model')}")
    print(f"ギルド数: {len(records)} / 本文生成済み: {generated} / エラー: {len(result['errors'])}")
    if result["errors"]:
        for message in result["errors"][:10]:
            print(f"  - {message}")
    print(f"出力: {json_path}")
    return json_path


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="コメント元データからOllamaでギルド別AIコメント（短/通/詳）を生成します。"
    )
    parser.add_argument("--new-date", default=None, help="対象サマリー日付 (YYYY-MM-DD)。省略時は最新。")
    parser.add_argument("--old-date", default=None, help="前回比較サマリー日付 (YYYY-MM-DD)。省略時は直近過去。")
    parser.add_argument("--skip-ai", action="store_true", help="Ollamaを呼ばず雛形JSONだけ作成します。")
    parser.add_argument("--model", default=None, help="使用するOllamaモデル。省略時はconfigの設定を使用。")
    args = parser.parse_args(list(argv) if argv is not None else None)
    run(new_date=args.new_date, old_date=args.old_date, skip_ai=args.skip_ai, model=args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
