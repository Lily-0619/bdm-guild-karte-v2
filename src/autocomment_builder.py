# -*- coding: utf-8 -*-
"""AutoComment/AI作文システム用の材料作成CLI。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree

try:
    from .autocomment_ollama import generate_guild_comment, load_ai_config
    from .autocomment_prompt import build_guild_material_text, build_material_text
    from .paths import ANALYSIS_DIR, AUTOCOMMENT_DIR, CONFIG_DIR, DATA_DIR, DB_PATH, DETA_PV_DIR, PROJECT_ROOT, ensure_dirs
except ImportError:  # 直接実行された場合のため
    from autocomment_ollama import generate_guild_comment, load_ai_config
    from autocomment_prompt import build_guild_material_text, build_material_text
    from paths import ANALYSIS_DIR, AUTOCOMMENT_DIR, CONFIG_DIR, DATA_DIR, DB_PATH, DETA_PV_DIR, PROJECT_ROOT, ensure_dirs  # type: ignore


ROOT_DIR = PROJECT_ROOT
PV_DIR = DETA_PV_DIR
CONFIG_PATH = CONFIG_DIR / "autocomment_ai.json"
SQLITE_PATH = DB_PATH
OUTPUT_ROOT = AUTOCOMMENT_DIR

MAX_ROWS_PER_SHEET = 120
MAX_SQLITE_ROWS_PER_TABLE = 200
WARNING_LIMIT = 50
DEBUG_SHEETNAME_LIMIT = 200

SUMMARY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "guild_name": ("guild_name", "guild", "ギルド名", "ギルド", "所属", "所属ギルド"),
    "member_count": ("member_count", "members", "member", "人数", "メンバー数", "所属人数"),
    "avg_cpm": ("avg_cpm", "average_cpm", "平均cpm", "平均CPM", "avg", "平均"),
    "total_cpm": ("total_cpm", "sum_cpm", "総cpm", "合計cpm", "総CPM", "合計CPM", "total", "合計"),
    "rank": ("rank", "ranking", "順位", "ランク"),
}


@dataclass(frozen=True)
class DateSpec:
    """AutoCommentで使う日付の表示用/探索用表現。"""

    display_date: str
    compact_date: str
    slash_date: str
    input_value: str

    @property
    def search_tokens(self) -> Tuple[str, ...]:
        """ファイル探索で使う日付候補。"""
        return (self.display_date, self.compact_date, self.slash_date)


def normalize_date_input(value: Any) -> DateSpec:
    """入力日付をdisplay_date(YYYY-MM-DD)とcompact_date(YYYYMMDD)に正規化する。"""
    if isinstance(value, datetime):
        parsed_date = value.date()
        input_value = value.isoformat()
    elif isinstance(value, date):
        parsed_date = value
        input_value = value.isoformat()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excelの1900日付シリアル値。openpyxl等で数値として渡された場合に備える。
        parsed_date = (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
        input_value = str(value)
    else:
        input_value = str(value).strip()
        parsed_date = None
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed_date = datetime.strptime(input_value, fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None and re.fullmatch(r"\d+(?:\.0+)?", input_value):
            serial_value = float(input_value)
            if 30000 <= serial_value <= 60000:
                parsed_date = (datetime(1899, 12, 30) + timedelta(days=serial_value)).date()

    if parsed_date is None:
        raise ValueError("日付は YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD / datetime/date / Excel日付型で指定してください。")

    return DateSpec(
        display_date=parsed_date.strftime("%Y-%m-%d"),
        compact_date=parsed_date.strftime("%Y%m%d"),
        slash_date=parsed_date.strftime("%Y/%m/%d"),
        input_value=input_value,
    )


def normalize_date_label(date_text: Any) -> str:
    """互換用: 入力日付をYYYYMMDDに正規化する。"""
    return normalize_date_input(date_text).compact_date


def hyphen_date(date_label: str) -> str:
    """YYYYMMDDまたはYYYY-MM-DDをYYYY-MM-DDに変換する。"""
    return normalize_date_input(date_label).display_date


def add_warning(warnings: List[str], message: str) -> None:
    """warning一覧に重複を避けて追加する。"""
    if message not in warnings:
        warnings.append(message)


def finalize_warnings(warnings: List[str], limit: int = WARNING_LIMIT) -> Dict[str, Any]:
    """warningsをAIに渡しすぎないように最大件数で丸める。"""
    original_count = len(warnings)
    if original_count > limit:
        shown_before_summary = max(limit - 1, 0)
        omitted_count = original_count - shown_before_summary
        del warnings[shown_before_summary:]
        warnings.append(f"warningsは{limit}件まで表示しています。残り{omitted_count}件は省略しました。")
    return {
        "total_count": original_count,
        "shown_count": len(warnings),
        "omitted_count": max(original_count - limit, 0),
        "limit": limit,
    }


def safe_glob(directory: Path, pattern: str, warnings: List[str], label: str) -> List[Path]:
    """存在しないディレクトリをwarningにしつつglobする。"""
    if not directory.exists():
        add_warning(warnings, f"{label}ディレクトリが見つかりません: {directory}")
        return []
    return sorted(directory.glob(pattern))


def _xml_text(element: ElementTree.Element) -> str:
    return "".join(element.itertext()).strip()


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - ord("A") + 1)
    return max(index - 1, 0)


def _read_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        raw_xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw_xml)
    return [_xml_text(item) for item in root.iter() if item.tag.endswith("}si") or item.tag == "si"]


def _read_workbook_sheets(archive: zipfile.ZipFile) -> List[Tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships: Dict[str, str] = {}
    try:
        rels_root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels_root:
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                relationships[rel_id] = "xl/" + target.lstrip("/")
    except KeyError:
        pass

    sheets: List[Tuple[str, str]] = []
    for sheet in workbook.iter():
        if not (sheet.tag.endswith("}sheet") or sheet.tag == "sheet"):
            continue
        name = sheet.attrib.get("name", "sheet")
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        path = relationships.get(rel_id or "")
        if path:
            sheets.append((name, path))
    return sheets


def read_xlsx_preview(path: Path, warnings: List[str], *, max_rows: int = MAX_ROWS_PER_SHEET) -> Dict[str, Any]:
    """外部ライブラリなしでxlsxの実シート名と先頭行を安全に読む。"""
    if not path.exists():
        add_warning(warnings, f"xlsxファイルが見つかりません: {path}")
        return {"path": str(path), "sheetnames": [], "sheets": [], "missing": True}

    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = _read_shared_strings(archive)
            sheets = _read_workbook_sheets(archive)
            sheetnames = [sheet_name for sheet_name, _ in sheets]
            if not sheets:
                add_warning(warnings, f"xlsxシートが見つかりません: {path}")

            sheet_results: List[Dict[str, Any]] = []
            for sheet_name, sheet_path in sheets:
                try:
                    root = ElementTree.fromstring(archive.read(sheet_path))
                except KeyError:
                    add_warning(warnings, f"xlsxシート実体が見つかりません: {path} / {sheet_name}")
                    continue

                rows: List[List[str]] = []
                for row in root.iter():
                    if not (row.tag.endswith("}row") or row.tag == "row"):
                        continue
                    values: List[str] = []
                    for cell in row:
                        if not (cell.tag.endswith("}c") or cell.tag == "c"):
                            continue
                        cell_ref = cell.attrib.get("r", "")
                        col_index = _column_index(cell_ref) if cell_ref else len(values)
                        while len(values) < col_index:
                            values.append("")
                        cell_type = cell.attrib.get("t")
                        value = ""
                        for child in cell:
                            if child.tag.endswith("}v") or child.tag == "v":
                                value = child.text or ""
                                break
                            if child.tag.endswith("}is") or child.tag == "is":
                                value = _xml_text(child)
                                break
                        if cell_type == "s" and value.isdigit():
                            shared_index = int(value)
                            if 0 <= shared_index < len(shared_strings):
                                value = shared_strings[shared_index]
                        values.append(value)
                    if any(value != "" for value in values):
                        rows.append(values)
                    if len(rows) >= max_rows:
                        break
                sheet_results.append({"name": sheet_name, "rows": rows})
            return {"path": str(path), "sheetnames": sheetnames, "sheets": sheet_results}
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        add_warning(warnings, f"xlsx読み込みに失敗しました: {path} / {exc}")
        return {"path": str(path), "sheetnames": [], "sheets": [], "error": str(exc)}


def read_sqlite_preview(path: Path, warnings: List[str]) -> Dict[str, Any]:
    """sqlite3のテーブル一覧と先頭行を安全に読む。"""
    if not path.exists():
        add_warning(warnings, f"sqliteファイルが見つかりません: {path}")
        return {"path": str(path), "tables": [], "missing": True}

    result: Dict[str, Any] = {"path": str(path), "tables": []}
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            for table_row in table_rows:
                table_name = table_row["name"]
                columns_info = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                columns = [column["name"] for column in columns_info]
                rows = connection.execute(
                    f'SELECT * FROM "{table_name}" LIMIT {MAX_SQLITE_ROWS_PER_TABLE}'
                ).fetchall()
                result["tables"].append(
                    {
                        "name": table_name,
                        "columns": columns,
                        "rows": [dict(row) for row in rows],
                    }
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        add_warning(warnings, f"sqlite読み込みに失敗しました: {path} / {exc}")
        result["error"] = str(exc)
    return result


def normalize_key(value: Any) -> str:
    """ヘッダー比較用に文字を正規化する。"""
    return re.sub(r"[\s_\-　（）()]+", "", str(value).strip().lower())


def normalize_cell(value: Any) -> str:
    return str(value).strip()


def is_number_like(value: Any) -> bool:
    text = normalize_cell(value).replace(",", "")
    if text == "":
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def is_date_like(value: Any) -> bool:
    text = normalize_cell(value)
    if not text:
        return False
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return True
    if re.fullmatch(r"\d{8}", text):
        return True
    # Excelシリアル値の可能性をゆるく許容する。
    return is_number_like(text) and 30000 <= float(text.replace(",", "")) <= 60000


def header_matches(header: Sequence[Any]) -> Dict[str, int]:
    """summary系シートのヘッダーから必要列を推定する。"""
    normalized_aliases = {
        field: {normalize_key(alias) for alias in aliases} for field, aliases in SUMMARY_ALIASES.items()
    }
    mapping: Dict[str, int] = {}
    for index, cell in enumerate(header):
        normalized = normalize_key(cell)
        if not normalized:
            continue
        for field, aliases in normalized_aliases.items():
            if normalized in aliases or any(alias and alias in normalized for alias in aliases):
                mapping.setdefault(field, index)
    return mapping


def find_summary_header(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[int], Dict[str, int]]:
    """存在するシート全体からsummaryらしいヘッダー行を探す。"""
    best_index: Optional[int] = None
    best_mapping: Dict[str, int] = {}
    best_score = 0
    for index, row in enumerate(rows[:20]):
        mapping = header_matches(row)
        score = len(mapping)
        if "guild_name" in mapping:
            score += 3
        if score > best_score:
            best_index = index
            best_mapping = mapping
            best_score = score
    if best_index is None or "guild_name" not in best_mapping or best_score < 4:
        return None, {}
    return best_index, best_mapping


def extract_summary_records(item: Dict[str, Any], warnings: List[str]) -> List[Dict[str, Any]]:
    """summary xlsxの全シートからギルド集計らしい行を抽出する。"""
    records: List[Dict[str, Any]] = []
    detected_sheets: List[str] = []
    for sheet in item.get("sheets", []):
        rows = sheet.get("rows", [])
        header_index, mapping = find_summary_header(rows)
        if header_index is None:
            continue
        detected_sheets.append(str(sheet.get("name", "")))
        for row in rows[header_index + 1 :]:
            guild_index = mapping["guild_name"]
            if guild_index >= len(row):
                continue
            guild_name = normalize_cell(row[guild_index])
            if not guild_name:
                continue
            record: Dict[str, Any] = {
                "guild_name": guild_name,
                "source_path": item.get("path"),
                "source_date": item.get("source_date"),
                "sheet": sheet.get("name"),
            }
            for field, column_index in mapping.items():
                if column_index < len(row):
                    record[field] = row[column_index]
            records.append(record)
    item["detected_summary_sheets"] = detected_sheets
    item["summary_structure_detected"] = bool(detected_sheets)
    return records


def looks_like_pv_sheet(rows: Sequence[Sequence[Any]]) -> bool:
    """A-F列が 日付/CPM/伸び/家門名/所属/状態 らしいシートを判定する。"""
    evidence = 0
    for row in rows[:40]:
        if len(row) < 5:
            continue
        date_ok = is_date_like(row[0])
        cpm_ok = is_number_like(row[1]) if len(row) > 1 else False
        growth_ok = is_number_like(row[2]) if len(row) > 2 else True
        family_ok = bool(normalize_cell(row[3])) if len(row) > 3 else False
        guild_ok = bool(normalize_cell(row[4])) if len(row) > 4 else False
        if date_ok and cpm_ok and growth_ok and family_ok and guild_ok:
            evidence += 1
    return evidence >= 2


def extract_pv_records(item: Dict[str, Any], warnings: List[str]) -> List[Dict[str, Any]]:
    """PVカルテxlsxからシート名に依存せずPV行を抽出する。"""
    records: List[Dict[str, Any]] = []
    detected_sheets: List[str] = []
    for sheet in item.get("sheets", []):
        rows = sheet.get("rows", [])
        if not looks_like_pv_sheet(rows):
            continue
        detected_sheets.append(str(sheet.get("name", "")))
        for row in rows:
            if len(row) < 5 or not is_date_like(row[0]) or not is_number_like(row[1]):
                continue
            records.append(
                {
                    "date": row[0],
                    "cpm": row[1],
                    "growth": row[2] if len(row) > 2 else "",
                    "family_name": row[3] if len(row) > 3 else "",
                    "guild_name": row[4] if len(row) > 4 else "",
                    "status": row[5] if len(row) > 5 else "",
                    "source_path": item.get("path"),
                    "source_date": item.get("source_date"),
                    "sheet": sheet.get("name"),
                }
            )
    item["detected_pv_sheets"] = detected_sheets
    item["pv_structure_detected"] = bool(detected_sheets)
    return records


def _looks_like_guild_key(key: str) -> bool:
    lowered = key.lower()
    return "guild" in lowered or "ギルド" in key or key in ("所属", "所属ギルド")


def extract_guild_names_from_rows(rows: Sequence[Sequence[Any]]) -> List[str]:
    """xlsx行からギルド名らしい値を拾う。"""
    guilds: List[str] = []
    if not rows:
        return guilds

    header_index, mapping = find_summary_header(rows)
    if header_index is not None and "guild_name" in mapping:
        guild_columns = [mapping["guild_name"]]
        data_rows = rows[header_index + 1 :]
    else:
        header = [str(value) for value in rows[0]]
        guild_columns = [index for index, key in enumerate(header) if _looks_like_guild_key(key)]
        data_rows = rows[1:]

    for row in data_rows:
        for index in guild_columns:
            if index < len(row):
                value = normalize_cell(row[index])
                if value and value not in guilds:
                    guilds.append(value)
    return guilds


def extract_guild_names(material: Dict[str, Any]) -> List[str]:
    """集めた材料からギルド名候補を抽出する。"""
    guilds: List[str] = []

    for guild_name in material.get("guild_files", {}).keys():
        if guild_name not in guilds:
            guilds.append(guild_name)

    for record in material.get("summary_records", []) + material.get("personal_pv_records", []) + material.get("unknown_pv_records", []):
        guild_name = normalize_cell(record.get("guild_name", ""))
        if guild_name and guild_name not in guilds:
            guilds.append(guild_name)

    for source_key in ("summaries", "personal_pv", "unknown_pv"):
        for item in material.get(source_key, []):
            for sheet in item.get("sheets", []):
                for guild_name in extract_guild_names_from_rows(sheet.get("rows", [])):
                    if guild_name not in guilds:
                        guilds.append(guild_name)

    sqlite_data = material.get("sqlite", {})
    for table in sqlite_data.get("tables", []):
        columns = table.get("columns", [])
        guild_columns = [column for column in columns if _looks_like_guild_key(column)]
        for row in table.get("rows", []):
            for column in guild_columns:
                value = normalize_cell(row.get(column, ""))
                if value and value not in guilds:
                    guilds.append(value)

    return guilds


def filter_rows_for_guild(rows: Sequence[Sequence[Any]], guild_name: str) -> List[List[Any]]:
    """ギルド名を含む行だけを抽出する。"""
    filtered: List[List[Any]] = []
    for row in rows:
        if any(normalize_cell(value) == guild_name for value in row):
            filtered.append(list(row))
    return filtered[:MAX_ROWS_PER_SHEET]


def filter_records_for_guild(records: Sequence[Dict[str, Any]], guild_name: str) -> List[Dict[str, Any]]:
    """抽出済みレコードからギルド一致分を返す。"""
    return [record for record in records if normalize_cell(record.get("guild_name", "")) == guild_name]


def filter_sqlite_for_guild(sqlite_data: Dict[str, Any], guild_name: str) -> List[Dict[str, Any]]:
    """sqliteプレビューからギルド名に関係する行を抽出する。"""
    tables: List[Dict[str, Any]] = []
    for table in sqlite_data.get("tables", []):
        matched_rows = []
        for row in table.get("rows", []):
            if any(normalize_cell(value) == guild_name for value in row.values()):
                matched_rows.append(row)
        if matched_rows:
            tables.append(
                {
                    "name": table.get("name"),
                    "columns": table.get("columns", []),
                    "rows": matched_rows[:MAX_SQLITE_ROWS_PER_TABLE],
                }
            )
    return tables


def append_sheet_debug(debug: Dict[str, Any], source_key: str, items: Sequence[Dict[str, Any]]) -> None:
    """debug用に実際のsheetnamesを集約する。"""
    sheetnames = debug.setdefault("sheetnames", {})
    entries = sheetnames.setdefault(source_key, [])
    for item in items:
        if len(entries) >= DEBUG_SHEETNAME_LIMIT:
            break
        entries.append(
            {
                "path": item.get("path"),
                "sheetnames": item.get("sheetnames", []),
                "detected_summary_sheets": item.get("detected_summary_sheets", []),
                "detected_pv_sheets": item.get("detected_pv_sheets", []),
            }
        )


def add_structure_warning(
    warnings: List[str],
    *,
    label: str,
    items: Sequence[Dict[str, Any]],
    detected_key: str,
) -> None:
    """構造推定失敗warningをファイルごとに大量出力せず集約する。"""
    failed_items = [item for item in items if item.get("sheets") and not item.get(detected_key)]
    if not failed_items:
        return
    examples = []
    for item in failed_items[:5]:
        examples.append(f"{item.get('path')} sheetnames={item.get('sheetnames', [])}")
    suffix = ""
    if len(failed_items) > len(examples):
        suffix = f" / ほか{len(failed_items) - len(examples)}件"
    add_warning(warnings, f"{label}構造を推定できないxlsxが{len(failed_items)}件あります: " + "; ".join(examples) + suffix)


def date_token_matches(path: Path, *date_specs: DateSpec) -> bool:
    """ファイル名にdisplay/compactどちらかの日付が含まれるか判定する。"""
    name = path.name
    return any(token in name for spec in date_specs for token in spec.search_tokens)


def collect_summary_files(old_date: DateSpec, new_date: DateSpec, warnings: List[str]) -> List[Dict[str, Any]]:
    """analysis/summary_*.xlsx を読む。"""
    files = safe_glob(ANALYSIS_DIR, "summary_*.xlsx", warnings, "analysis")
    target_files = [path for path in files if date_token_matches(path, old_date, new_date)]
    searched = sorted({token for spec in (old_date, new_date) for token in spec.search_tokens})
    if not target_files and files:
        add_warning(warnings, f"指定日付を含むsummary_*.xlsxが見つかりません。探索日付形式={searched}。全summaryを材料候補にします。")
        target_files = files
    if not target_files:
        add_warning(warnings, f"analysis/summary_*.xlsx が見つかりません。探索日付形式={searched}")
    return [read_xlsx_preview(path, warnings) for path in target_files]


def collect_personal_pv_files(old_date: DateSpec, new_date: DateSpec, warnings: List[str]) -> List[Dict[str, Any]]:
    """deta_PV/*.xlsx を読む。"""
    files = safe_glob(PV_DIR, "*.xlsx", warnings, "個人PVカルテ保存先(deta_PV)")
    target_files = [path for path in files if date_token_matches(path, old_date, new_date)]
    searched = sorted({token for spec in (old_date, new_date) for token in spec.search_tokens})
    if not target_files and files:
        add_warning(warnings, f"指定日付を含むdeta_PV直下のxlsxが見つかりません。探索日付形式={searched}。直下xlsxを材料候補にします。")
        target_files = files
    if not target_files:
        add_warning(warnings, f"deta_PV/*.xlsx が見つかりません。探索日付形式={searched}")
    return [read_xlsx_preview(path, warnings) for path in target_files]


def collect_unknown_pv_files(new_date: DateSpec, warnings: List[str]) -> List[Dict[str, Any]]:
    """deta_PV/追跡不明/YYYYMMDD または YYYY-MM-DD/*.xlsx を読む。"""
    files: List[Path] = []
    searched_dirs = []
    for token in (new_date.compact_date, new_date.display_date):
        unknown_dir = PV_DIR / "追跡不明" / token
        searched_dirs.append(str(unknown_dir))
        files.extend(safe_glob(unknown_dir, "*.xlsx", warnings, "追跡不明PV"))
    # 同じファイルが複数候補から取れた場合に備えて重複排除。
    files = sorted(set(files))
    if not files:
        add_warning(warnings, f"deta_PV/追跡不明 の指定日付xlsxが見つかりません。探索先={searched_dirs}")
    return [read_xlsx_preview(path, warnings) for path in files]


def collect_guild_files(old_date: DateSpec, new_date: DateSpec, warnings: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """data/<ギルド名>/guild_ギルド名_YYYY-MM-DD または YYYYMMDD.xlsx を読む。"""
    guild_files: Dict[str, List[Dict[str, Any]]] = {}
    if not DATA_DIR.exists():
        add_warning(warnings, f"dataディレクトリが見つかりません: {DATA_DIR}")
        return guild_files

    searched_tokens = sorted({old_date.display_date, old_date.compact_date, new_date.display_date, new_date.compact_date})
    found_any = False
    for guild_dir in sorted(path for path in DATA_DIR.iterdir() if path.is_dir()):
        guild_name = guild_dir.name
        patterns = [f"guild_{guild_name}_{token}.xlsx" for token in searched_tokens]
        seen_paths = set()
        for pattern in patterns:
            for path in sorted(guild_dir.glob(pattern)):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                found_any = True
                guild_files.setdefault(guild_name, []).append(read_xlsx_preview(path, warnings))
    if not found_any:
        add_warning(warnings, f"data/<ギルド名>/guild_ギルド名_YYYY-MM-DD.xlsx または YYYYMMDD.xlsx が見つかりません。探索日付形式={searched_tokens}")
    return guild_files

def safe_filename(value: str) -> str:
    """Windows/Macで使えない文字を避けたフォルダ・ファイル名にする。"""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    sanitized = sanitized.strip(" .")
    return sanitized or "unknown_guild"


def assign_source_dates(items: Sequence[Dict[str, Any]], old_date: DateSpec, new_date: DateSpec) -> None:
    """ファイル名から旧日付・新日付を推定して付与する。"""
    for item in items:
        name = Path(str(item.get("path", ""))).name
        if any(token in name for token in old_date.search_tokens):
            item["source_date"] = old_date.display_date
            item["source_compact_date"] = old_date.compact_date
        elif any(token in name for token in new_date.search_tokens):
            item["source_date"] = new_date.display_date
            item["source_compact_date"] = new_date.compact_date
        else:
            item["source_date"] = "unknown"
            item["source_compact_date"] = "unknown"


def numeric_value(value: Any) -> Optional[float]:
    """差分計算用に数値化する。"""
    text = normalize_cell(value).replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def record_date_matches(value: Any, date_spec: DateSpec) -> bool:
    """PV行などの日付が指定日付と同じか判定する。"""
    text = normalize_cell(value)
    if text in date_spec.search_tokens:
        return True
    try:
        return normalize_date_input(value).compact_date == date_spec.compact_date
    except ValueError:
        return False


def latest_summary_record(records: Sequence[Dict[str, Any]], preferred_date: str) -> Dict[str, Any]:
    """指定日のsummaryを優先し、なければ最後のrecordを返す。"""
    for record in records:
        if record.get("source_date") == preferred_date:
            return record
    return records[-1] if records else {}


def summary_deltas(records: Sequence[Dict[str, Any]], old_date: str, new_date: str) -> Dict[str, Any]:
    """旧日付と新日付のsummary差分を計算する。"""
    old_record = latest_summary_record([r for r in records if r.get("source_date") == old_date], old_date)
    new_record = latest_summary_record([r for r in records if r.get("source_date") == new_date], new_date)
    if not old_record or not new_record:
        return {}

    deltas: Dict[str, Any] = {}
    for key in ("member_count", "avg_cpm", "total_cpm", "rank"):
        old_value = numeric_value(old_record.get(key))
        new_value = numeric_value(new_record.get(key))
        if old_value is not None and new_value is not None:
            deltas[key] = new_value - old_value
    return deltas


def sort_by_growth(records: Sequence[Dict[str, Any]], *, reverse: bool) -> List[Dict[str, Any]]:
    """growthを数値化して並べる。"""
    sortable = []
    for record in records:
        growth = numeric_value(record.get("growth"))
        if growth is not None:
            sortable.append((growth, record))
    sortable.sort(key=lambda item: item[0], reverse=reverse)
    return [record for _, record in sortable]


def summarize_member_changes(
    personal_pv_records: Sequence[Dict[str, Any]],
    unknown_pv_records: Sequence[Dict[str, Any]],
    old_date: DateSpec,
    new_date: DateSpec,
) -> Dict[str, Any]:
    """PVレコードからメンバー変化の材料を作る。"""
    old_names = {normalize_cell(r.get("family_name")) for r in personal_pv_records if record_date_matches(r.get("date"), old_date)}
    new_names = {normalize_cell(r.get("family_name")) for r in personal_pv_records if record_date_matches(r.get("date"), new_date)}
    old_names.discard("")
    new_names.discard("")

    exact_matches = sorted(old_names & new_names)
    new_joiners = sorted(new_names - old_names)
    unknown_members = sorted({normalize_cell(r.get("family_name")) for r in unknown_pv_records if normalize_cell(r.get("family_name"))})
    moved = sorted(
        {
            normalize_cell(r.get("family_name"))
            for r in personal_pv_records
            if "移籍" in normalize_cell(r.get("status"))
        }
    )
    renamed = sorted(
        {
            normalize_cell(r.get("family_name"))
            for r in personal_pv_records
            if "改名" in normalize_cell(r.get("status")) or "名前" in normalize_cell(r.get("status"))
        }
    )
    return {
        "exact_match_count": len(exact_matches) if old_names or new_names else None,
        "exact_matches_sample": exact_matches[:30],
        "new_joiners": new_joiners[:50],
        "unknown_members": unknown_members[:50],
        "renamed_members": renamed[:50],
        "transferred_members": moved[:50],
    }


def build_pv_trends(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """PVカルテ由来の推移情報をコンパクトに整理する。"""
    return {
        "record_count": len(records),
        "latest_rows": list(records)[-30:],
        "growth_top": sort_by_growth(records, reverse=True)[:10],
        "stagnant_or_decline": [r for r in sort_by_growth(records, reverse=False) if (numeric_value(r.get("growth")) or 0) <= 0][:10],
    }


def build_guild_ai_material(
    whole_material: Dict[str, Any], guild_name: str, guild_data: Dict[str, Any]
) -> Dict[str, Any]:
    """1ギルド専用のAI作文材料を作る。"""
    old_date = whole_material.get("old_date", "")
    new_date = whole_material.get("new_date", "")
    old_date_spec = normalize_date_input(whole_material.get("old_compact_date") or old_date)
    new_date_spec = normalize_date_input(whole_material.get("new_compact_date") or new_date)
    summary_records = guild_data.get("summary_records", [])
    personal_pv_records = guild_data.get("personal_pv_records", [])
    unknown_pv_records = guild_data.get("unknown_pv_records", [])
    latest_summary = latest_summary_record(summary_records, new_date)
    pv_trends = build_pv_trends(personal_pv_records)

    guild_warnings: List[str] = []
    if not summary_records:
        guild_warnings.append("summary由来のギルド集計情報が不足しています。")
    if not personal_pv_records:
        guild_warnings.append("PVカルテ由来の個人推移情報が不足しています。")
    if not guild_data.get("sqlite_rows"):
        guild_warnings.append("SQLite由来の該当行が少ない、または見つかりません。")

    return {
        "material_type": "guild_autocomment_material",
        "guild_name": guild_name,
        "old_date": old_date,
        "new_date": new_date,
        "old_display_date": old_date_spec.display_date,
        "new_display_date": new_date_spec.display_date,
        "old_compact_date": old_date_spec.compact_date,
        "new_compact_date": new_date_spec.compact_date,
        "period": {"old_date": old_date, "new_date": new_date},
        "key_metrics": {
            "member_count": latest_summary.get("member_count"),
            "avg_cpm": latest_summary.get("avg_cpm"),
            "total_cpm": latest_summary.get("total_cpm"),
            "rank": latest_summary.get("rank"),
        },
        "deltas": summary_deltas(summary_records, old_date, new_date),
        "member_changes": summarize_member_changes(personal_pv_records, unknown_pv_records, old_date_spec, new_date_spec),
        "growth_top": pv_trends["growth_top"],
        "stagnant_or_decline": pv_trends["stagnant_or_decline"],
        "summary_records": summary_records,
        "sqlite_rows": guild_data.get("sqlite_rows", []),
        "pv_trends": pv_trends,
        "personal_pv_records": personal_pv_records,
        "unknown_pv_records": unknown_pv_records,
        "summary_rows": guild_data.get("summary_rows", []),
        "personal_pv_rows": guild_data.get("personal_pv_rows", []),
        "unknown_pv_rows": guild_data.get("unknown_pv_rows", []),
        "guild_files": guild_data.get("guild_files", []),
        "guild_warnings": guild_warnings,
        "ai_instruction": [
            "このギルド専用材料だけを使って自然な日本語コメントを作成する。",
            "不足データは断定せず情報不足として扱う。",
            "良い点、注意点、次に見るべき点を含める。",
            "指定JSON形式のみで返す。",
        ],
    }


def build_guild_materials(raw_material: Dict[str, Any]) -> Dict[str, Any]:
    """共通材料をギルド別に整理する。"""
    guilds: Dict[str, Any] = {}
    for guild_name in extract_guild_names(raw_material):
        guilds[guild_name] = {
            "guild_name": guild_name,
            "summary_records": filter_records_for_guild(raw_material.get("summary_records", []), guild_name),
            "personal_pv_records": filter_records_for_guild(raw_material.get("personal_pv_records", []), guild_name),
            "unknown_pv_records": filter_records_for_guild(raw_material.get("unknown_pv_records", []), guild_name),
            "summary_rows": [],
            "personal_pv_rows": [],
            "unknown_pv_rows": [],
            "sqlite_rows": filter_sqlite_for_guild(raw_material.get("sqlite", {}), guild_name),
            "guild_files": raw_material.get("guild_files", {}).get(guild_name, []),
        }
        for source_key, output_key in (
            ("summaries", "summary_rows"),
            ("personal_pv", "personal_pv_rows"),
            ("unknown_pv", "unknown_pv_rows"),
        ):
            for item in raw_material.get(source_key, []):
                for sheet in item.get("sheets", []):
                    rows = filter_rows_for_guild(sheet.get("rows", []), guild_name)
                    if rows:
                        guilds[guild_name][output_key].append(
                            {
                                "path": item.get("path"),
                                "sheet": sheet.get("name"),
                                "rows": rows,
                            }
                        )
    return guilds


def build_material(old_date_text: Any, new_date_text: Any) -> Dict[str, Any]:
    """旧日付・新日付からAI作文材料を作る。"""
    old_date_spec = normalize_date_input(old_date_text)
    new_date_spec = normalize_date_input(new_date_text)
    old_date = old_date_spec.display_date
    new_date = new_date_spec.display_date
    warnings: List[str] = []
    debug: Dict[str, Any] = {
        "date_inputs": {
            "old_input": old_date_spec.input_value,
            "new_input": new_date_spec.input_value,
            "old_display_date": old_date_spec.display_date,
            "new_display_date": new_date_spec.display_date,
            "old_compact_date": old_date_spec.compact_date,
            "new_compact_date": new_date_spec.compact_date,
            "old_search_tokens": list(old_date_spec.search_tokens),
            "new_search_tokens": list(new_date_spec.search_tokens),
        }
    }

    summaries = collect_summary_files(old_date_spec, new_date_spec, warnings)
    assign_source_dates(summaries, old_date_spec, new_date_spec)
    summary_records: List[Dict[str, Any]] = []
    for item in summaries:
        summary_records.extend(extract_summary_records(item, warnings))

    personal_pv = collect_personal_pv_files(old_date_spec, new_date_spec, warnings)
    assign_source_dates(personal_pv, old_date_spec, new_date_spec)
    personal_pv_records: List[Dict[str, Any]] = []
    for item in personal_pv:
        personal_pv_records.extend(extract_pv_records(item, warnings))

    unknown_pv = collect_unknown_pv_files(new_date_spec, warnings)
    assign_source_dates(unknown_pv, old_date_spec, new_date_spec)
    unknown_pv_records: List[Dict[str, Any]] = []
    for item in unknown_pv:
        unknown_pv_records.extend(extract_pv_records(item, warnings))

    add_structure_warning(
        warnings,
        label="summary",
        items=summaries,
        detected_key="summary_structure_detected",
    )
    add_structure_warning(
        warnings,
        label="PVカルテ",
        items=personal_pv,
        detected_key="pv_structure_detected",
    )
    add_structure_warning(
        warnings,
        label="追跡不明PV",
        items=unknown_pv,
        detected_key="pv_structure_detected",
    )

    guild_files = collect_guild_files(old_date_spec, new_date_spec, warnings)
    for guild_file_items in guild_files.values():
        assign_source_dates(guild_file_items, old_date_spec, new_date_spec)
    append_sheet_debug(debug, "summaries", summaries)
    append_sheet_debug(debug, "personal_pv", personal_pv)
    append_sheet_debug(debug, "unknown_pv", unknown_pv)
    append_sheet_debug(
        debug,
        "guild_files",
        [item for items in guild_files.values() for item in items],
    )
    debug["record_counts"] = {
        "summary_records": len(summary_records),
        "personal_pv_records": len(personal_pv_records),
        "unknown_pv_records": len(unknown_pv_records),
    }

    raw_material: Dict[str, Any] = {
        "old_date": old_date,
        "new_date": new_date,
        "old_display_date": old_date_spec.display_date,
        "new_display_date": new_date_spec.display_date,
        "old_compact_date": old_date_spec.compact_date,
        "new_compact_date": new_date_spec.compact_date,
        "source_paths": {
            "analysis_summary": str(ANALYSIS_DIR / "summary_*.xlsx"),
            "sqlite": str(SQLITE_PATH),
            "personal_pv": str(PV_DIR / "*.xlsx"),
            "unknown_pv": str(PV_DIR / "追跡不明" / new_date_spec.compact_date / "*.xlsx"),
            "guild_files": str(DATA_DIR / "<ギルド名>" / "guild_ギルド名_YYYY-MM-DDまたはYYYYMMDD.xlsx"),
        },
        "warnings": warnings,
        "warning_summary": {},
        "debug": debug,
        "summaries": summaries,
        "summary_records": summary_records,
        "sqlite": read_sqlite_preview(SQLITE_PATH, warnings),
        "personal_pv": personal_pv,
        "personal_pv_records": personal_pv_records,
        "unknown_pv": unknown_pv,
        "unknown_pv_records": unknown_pv_records,
        "guild_files": guild_files,
    }
    raw_material["guilds"] = build_guild_materials(raw_material)
    raw_material["guild_materials"] = {
        guild_name: build_guild_ai_material(raw_material, guild_name, guild_data)
        for guild_name, guild_data in raw_material["guilds"].items()
    }
    if not raw_material["guilds"]:
        add_warning(warnings, "ギルド名候補を抽出できませんでした。AIコメント対象は0件です。")
    raw_material["warning_summary"] = finalize_warnings(warnings)
    return raw_material


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file_obj:
        file_obj.write(text)


def output_paths(old_date: str, new_date: str) -> Dict[str, Path]:
    output_dir = OUTPUT_ROOT / new_date
    stem = f"{old_date}_to_{new_date}"
    return {
        "dir": output_dir,
        "guilds_dir": output_dir / "guilds",
        "text": output_dir / f"autocomment_material_{stem}.txt",
        "json": output_dir / f"autocomment_material_{stem}.json",
        "comments": output_dir / f"ai_comments_{stem}.json",
    }


def guild_output_paths(base_dir: Path, guild_name: str, old_date: str, new_date: str) -> Dict[str, Path]:
    """1ギルド分の出力パスを作る。"""
    safe_guild = safe_filename(guild_name)
    guild_dir = base_dir / "guilds" / safe_guild
    stem = f"{safe_guild}_{old_date}_to_{new_date}"
    return {
        "dir": guild_dir,
        "text": guild_dir / f"material_{stem}.txt",
        "json": guild_dir / f"material_{stem}.json",
        "comment": guild_dir / f"ai_comment_{stem}.json",
    }


def write_guild_material_outputs(material: Dict[str, Any], base_dir: Path) -> List[Dict[str, Any]]:
    """ギルドごとの材料txt/jsonを書き出し、index用の一覧を返す。"""
    old_date = material.get("old_compact_date") or normalize_date_label(material.get("old_date", ""))
    new_date = material.get("new_compact_date") or normalize_date_label(material.get("new_date", ""))
    (base_dir / "guilds").mkdir(parents=True, exist_ok=True)
    entries: List[Dict[str, Any]] = []
    for guild_name, guild_material in material.get("guild_materials", {}).items():
        paths = guild_output_paths(base_dir, guild_name, old_date, new_date)
        write_text(paths["text"], build_guild_material_text(guild_material))
        write_json(paths["json"], guild_material)
        entries.append(
            {
                "guild_name": guild_name,
                "safe_guild_name": safe_filename(guild_name),
                "material_txt": str(paths["text"]),
                "material_json": str(paths["json"]),
                "ai_comment_json": str(paths["comment"]),
            }
        )
    return entries


def generate_and_write_guild_comments(
    material: Dict[str, Any], base_dir: Path, *, skip_ai: bool
) -> Dict[str, Any]:
    """ギルド別材料を1件ずつOllamaへ渡し、個別コメントと全体まとめを作る。"""
    old_date = material.get("old_date", "")
    new_date = material.get("new_date", "")
    old_compact_date = material.get("old_compact_date") or normalize_date_label(old_date)
    new_compact_date = material.get("new_compact_date") or normalize_date_label(new_date)
    comments: List[Dict[str, Any]] = []
    errors: List[str] = []
    config: Dict[str, Any] = {"provider": "ollama", "model": None}
    if skip_ai:
        errors.append("--skip-ai が指定されたため、Ollama生成は実行していません。")

    if not skip_ai:
        try:
            config = load_ai_config(CONFIG_PATH)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            skip_ai = True
            errors.append(f"AI設定読み込みに失敗しました: {exc}")

    for guild_name, guild_material in material.get("guild_materials", {}).items():
        paths = guild_output_paths(base_dir, guild_name, old_compact_date, new_compact_date)
        if skip_ai:
            result = {
                "old_date": old_date,
                "new_date": new_date,
                "guild_name": guild_name,
                "provider": "ollama",
                "model": config.get("model"),
                "comment": None,
                "errors": ["--skip-ai またはAI設定エラーのため、Ollama生成は実行していません。"],
                "material_txt": str(paths["text"]),
                "material_json": str(paths["json"]),
            }
        else:
            try:
                comment = generate_guild_comment(guild_material, guild_name, config)
                result = {
                    "old_date": old_date,
                    "new_date": new_date,
                    "guild_name": guild_name,
                    "provider": config.get("provider", "ollama"),
                    "model": config.get("model"),
                    "comment": comment,
                    "errors": [],
                    "material_txt": str(paths["text"]),
                    "material_json": str(paths["json"]),
                }
                comments.append(comment)
            except Exception as exc:  # 個別ギルド失敗で全体を止めない
                message = f"{guild_name}: {exc}"
                errors.append(message)
                result = {
                    "old_date": old_date,
                    "new_date": new_date,
                    "guild_name": guild_name,
                    "provider": config.get("provider", "ollama"),
                    "model": config.get("model"),
                    "comment": None,
                    "errors": [message],
                    "material_txt": str(paths["text"]),
                    "material_json": str(paths["json"]),
                }
        write_json(paths["comment"], result)

    return {
        "old_date": old_date,
        "new_date": new_date,
        "provider": config.get("provider", "ollama"),
        "model": config.get("model"),
        "comments": comments,
        "errors": errors,
        "guild_comment_files": [
            entry.get("ai_comment_json") for entry in material.get("guild_output_index", [])
        ],
    }


def run(old_date_text: Any, new_date_text: Any, *, skip_ai: bool = False) -> Dict[str, Path]:
    """材料作成とAIコメントJSON出力を実行する。"""
    ensure_dirs()
    old_date_spec = normalize_date_input(old_date_text)
    new_date_spec = normalize_date_input(new_date_text)
    paths = output_paths(old_date_spec.compact_date, new_date_spec.compact_date)
    material = build_material(old_date_text, new_date_text)

    material["guild_output_index"] = write_guild_material_outputs(material, paths["dir"])
    write_text(paths["text"], build_material_text(material))
    write_json(paths["json"], material)

    comments = generate_and_write_guild_comments(material, paths["dir"], skip_ai=skip_ai)
    write_json(paths["comments"], comments)
    return paths


def run_from_pv_detail_selection(old_date: Any, new_date: Any, *, skip_ai: bool = True) -> Dict[str, Path]:
    """pv_detail_app.pyの旧データ/新データ選択値をそのまま受け取って実行するための関数。"""
    return run(old_date, new_date, skip_ai=skip_ai)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="旧日付・新日付からAutoComment AI作文材料を作成します。")
    parser.add_argument("old_date", help="旧日付。YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD")
    parser.add_argument("new_date", help="新日付。YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD")
    parser.add_argument("--skip-ai", action="store_true", help="Ollama呼び出しを行わず、材料だけ作成します。")
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = run(args.old_date, args.new_date, skip_ai=args.skip_ai)
    print("AutoComment出力が完了しました。")
    print(f"出力先: {paths['dir']}")
    print(f"ギルド別材料: {paths['guilds_dir']}")
    print(f"材料txt: {paths['text']}")
    print(f"材料json: {paths['json']}")
    print(f"AIコメントjson: {paths['comments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
