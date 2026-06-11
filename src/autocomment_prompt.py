# -*- coding: utf-8 -*-
"""AutoComment用のプロンプト生成処理。"""

from __future__ import annotations

import json
from typing import Any, Dict, List


SYSTEM_PROMPT = """あなたはBDMギルドカルテのコメント作成担当です。
入力された旧日付と新日付の比較材料をもとに、ギルドごとの状況コメントを日本語で作成してください。
事実として材料にない内容は断定せず、「可能性」「傾向」として表現してください。
過度に攻撃的・断定的な表現は避け、ゲーム内の振り返りに使いやすい自然な文体にしてください。
必ずJSONのみを返してください。Markdownや説明文は不要です。
"""


COMMENT_SCHEMA: Dict[str, Any] = {
    "guild_name": "ギルド名",
    "short_comment": "短文コメント",
    "normal_comment": "通常コメント",
    "detail_comment": "詳細コメント",
    "attention_points": ["注目点1", "注目点2"],
    "tone": "状態タグ",
}


def build_guild_prompt(material: Dict[str, Any], guild_name: str) -> List[Dict[str, str]]:
    """Ollama chat APIに渡すmessagesを作成する。"""
    if material.get("material_type") == "guild_autocomment_material":
        guild_material = material
        warnings = material.get("guild_warnings", [])
        warning_summary = {}
    else:
        guild_material = material.get("guilds", {}).get(guild_name, {})
        warnings = material.get("warnings", [])
        warning_summary = material.get("warning_summary", {})

    old_date = material.get("old_date", "")
    new_date = material.get("new_date", "")

    user_payload = {
        "task": "BDMギルド別コメント作成",
        "old_date": old_date,
        "new_date": new_date,
        "guild_name": guild_name,
        "required_output_schema": COMMENT_SCHEMA,
        "comment_guidelines": {
            "short_comment": "1文で要点のみ。",
            "normal_comment": "2〜4文で、変化と総評を書く。",
            "detail_comment": "材料に基づいて、良い点・注意点・次に見る点を具体的に書く。",
            "attention_points": "重要な注目点を2〜5個の配列にする。",
            "tone": "好調、安定、注意、停滞、情報不足など短い状態タグにする。",
        },
        "guild_material": guild_material,
        "data_warnings": warnings,
        "warning_summary": warning_summary,
    }

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
        },
    ]


def build_material_text(material: Dict[str, Any]) -> str:
    """確認用テキストファイルに書き出す人間向け材料を作成する。"""
    lines: List[str] = []
    lines.append("# AutoComment AI作文材料")
    lines.append(f"旧日付: {material.get('old_display_date', material.get('old_date', ''))}")
    lines.append(f"新日付: {material.get('new_display_date', material.get('new_date', ''))}")
    lines.append("")

    warning_summary = material.get("warning_summary", {})
    warnings = material.get("warnings", [])
    lines.append("## warnings")
    if warning_summary:
        lines.append(
            "- 表示件数: "
            f"{warning_summary.get('shown_count', len(warnings))} / "
            f"総数 {warning_summary.get('total_count', len(warnings))}"
        )
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- なし")
    lines.append("")

    debug = material.get("debug", {})
    date_inputs = debug.get("date_inputs", {})
    if date_inputs:
        lines.append("## debug_dates")
        lines.append(json.dumps(date_inputs, ensure_ascii=False, indent=2))
        lines.append("")

    sheetnames = debug.get("sheetnames", {})
    if sheetnames:
        lines.append("## debug_sheetnames")
        for source_key, entries in sheetnames.items():
            lines.append(f"### {source_key}")
            for entry in entries[:20]:
                lines.append(
                    "- "
                    f"{entry.get('path')}: "
                    f"sheetnames={entry.get('sheetnames', [])}, "
                    f"summary={entry.get('detected_summary_sheets', [])}, "
                    f"pv={entry.get('detected_pv_sheets', [])}"
                )
            if len(entries) > 20:
                lines.append(f"- ... 残り{len(entries) - 20}件のsheetnames debugはJSONを確認してください。")
        lines.append("")

    lines.append("## guild_output_index")
    guild_output_index = material.get("guild_output_index", [])
    if not guild_output_index:
        lines.append("ギルド別材料ファイルは作成されていません。")
    for entry in guild_output_index:
        lines.append(f"- {entry.get('guild_name')}: {entry.get('material_txt')}")
    lines.append("")

    lines.append("## record_counts")
    lines.append(json.dumps(material.get("debug", {}).get("record_counts", {}), ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("※ AI作文の本命材料は、この全体indexではなく guilds/<guild_name>/material_*.txt または material_*.json です。")

    return "\n".join(lines).rstrip() + "\n"


def build_guild_material_text(guild_material: Dict[str, Any]) -> str:
    """1ギルドだけをAIに渡すための材料txtを作成する。"""
    lines: List[str] = []
    guild_name = guild_material.get("guild_name", "")
    lines.append("# ギルド別AutoComment AI作文材料")
    lines.append(f"ギルド名: {guild_name}")
    lines.append(
        "対象期間: "
        f"{guild_material.get('old_display_date', guild_material.get('old_date', ''))} "
        "→ "
        f"{guild_material.get('new_display_date', guild_material.get('new_date', ''))}"
    )
    lines.append("")

    warnings = guild_material.get("guild_warnings", [])
    if warnings:
        lines.append("## 材料不足・注意")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## 主要指標")
    key_metrics = guild_material.get("key_metrics", {})
    for key, label in (
        ("member_count", "人数"),
        ("avg_cpm", "平均CPM"),
        ("total_cpm", "総CPM"),
        ("rank", "順位"),
    ):
        lines.append(f"- {label}: {key_metrics.get(key, '不明')}")
    lines.append("")

    lines.append("## 前回との差分")
    deltas = guild_material.get("deltas", {})
    if deltas:
        for key, value in deltas.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- 差分を計算できる前回データが不足しています。")
    lines.append("")

    lines.append("## メンバー変化")
    member_changes = guild_material.get("member_changes", {})
    for key, label in (
        ("exact_match_count", "完全一致人数"),
        ("new_joiners", "新規加入者"),
        ("unknown_members", "追跡不明者"),
        ("renamed_members", "名前変更者"),
        ("transferred_members", "移籍者"),
    ):
        lines.append(f"- {label}: {member_changes.get(key, [])}")
    lines.append("")

    lines.append("## CPM伸び上位")
    growth_top = guild_material.get("growth_top", [])
    if growth_top:
        for record in growth_top:
            lines.append(f"- {record}")
    else:
        lines.append("- 該当データなし")
    lines.append("")

    lines.append("## 停滞・減少情報")
    stagnant = guild_material.get("stagnant_or_decline", [])
    if stagnant:
        for record in stagnant:
            lines.append(f"- {record}")
    else:
        lines.append("- 目立つ停滞・減少データなし")
    lines.append("")

    lines.append("## summary由来の情報")
    lines.append(json.dumps(guild_material.get("summary_records", []), ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("## SQLite由来の情報")
    lines.append(json.dumps(guild_material.get("sqlite_rows", []), ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("## PVカルテ由来の推移情報")
    lines.append(json.dumps(guild_material.get("pv_trends", {}), ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("## ギルドファイル由来の情報")
    lines.append(json.dumps(guild_material.get("guild_files", []), ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("## AIへの作文指示")
    lines.append("- 上記の材料だけを使って、このギルド専用の自然な日本語コメントを作成してください。")
    lines.append("- 数字が不足している項目は断定せず、情報不足として扱ってください。")
    lines.append("- 良い点、注意点、次に見るべき点が分かる文章にしてください。")
    lines.append("- 出力は指定されたJSON形式だけにしてください。")

    return "\n".join(lines).rstrip() + "\n"
