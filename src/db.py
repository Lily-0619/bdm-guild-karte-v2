"""SQLite persistence helpers for BDM guild snapshots.

The database path defaults to ``data/bdm_guild.sqlite3`` relative to the
project root so the same code works when the project lives on Windows
(``D:\\bdm-guild-karte``) or on macOS/Linux.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence

try:
    from .paths import DB_PATH, PROJECT_ROOT
except ImportError:  # 直接実行された場合のため
    from paths import DB_PATH, PROJECT_ROOT  # type: ignore

DEFAULT_DB_PATH = DB_PATH


def utc_now_iso() -> str:
    """Return the current UTC time in an SQLite-friendly ISO format."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection and ensure the parent directory exists."""

    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all SQLite tables used by the guild card pipeline."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS guild_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            retrieved_at TEXT NOT NULL,
            guild_name TEXT NOT NULL,
            member_count INTEGER,
            avg_cpm REAL,
            total_cpm REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(retrieved_at, guild_name)
        );

        CREATE TABLE IF NOT EXISTS member_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            retrieved_at TEXT NOT NULL,
            guild_name TEXT NOT NULL,
            rank_no INTEGER,
            class_name TEXT,
            family_name TEXT NOT NULL,
            level INTEGER,
            cpm REAL,
            fcp REAL,
            class_name_raw TEXT,
            class_name_normalized TEXT,
            class_name_version TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(retrieved_at, guild_name, family_name)
        );

        CREATE TABLE IF NOT EXISTS import_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS node_history_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            retrieved_at TEXT NOT NULL,
            guild_name TEXT NOT NULL,
            row_no INTEGER NOT NULL,
            war_date TEXT,
            node_name TEXT,
            opponent_guild TEXT,
            result TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(retrieved_at, guild_name, row_no)
        );

        CREATE TABLE IF NOT EXISTS guild_summary_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            retrieved_at TEXT NOT NULL,
            guild_name TEXT NOT NULL,
            avg_cp REAL,
            total_cp INTEGER,
            total_family_cp INTEGER,
            active_member_count INTEGER,
            low_member_cp INTEGER,
            high_member_cp INTEGER,
            declared_on_other_guild INTEGER,
            declared_by_other_guild INTEGER,
            total_war INTEGER,
            all_time_win_rate REAL,
            most_war_with_guild TEXT,
            total_node_wars INTEGER,
            node_won INTEGER,
            total_siege_wars INTEGER,
            siege_won INTEGER,
            currently_holding TEXT,
            summary_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(retrieved_at, guild_name)
        );
        """
    )
    _ensure_columns(
        conn,
        "member_snapshots",
        {
            "class_name_raw": "TEXT",
            "class_name_normalized": "TEXT",
            "class_name_version": "TEXT",
        },
    )
    migrate_import_file_paths(conn)
    conn.commit()


def initialize(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database and create tables/migrations if needed."""

    conn = connect(db_path)
    create_tables(conn)
    return conn


def save_guild_snapshot(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    member_count: int | None = None,
    avg_cpm: float | None = None,
    total_cpm: float | None = None,
) -> None:
    """Upsert one guild-level snapshot without changing its original created_at."""

    conn.execute(
        """
        INSERT INTO guild_snapshots (
            retrieved_at, guild_name, member_count, avg_cpm, total_cpm, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(retrieved_at, guild_name) DO UPDATE SET
            member_count = excluded.member_count,
            avg_cpm = excluded.avg_cpm,
            total_cpm = excluded.total_cpm
        """,
        (retrieved_at, guild_name, member_count, avg_cpm, total_cpm, utc_now_iso()),
    )


def save_member_snapshot(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    rank_no: int | None = None,
    class_name: str | None = None,
    family_name: str,
    level: int | None = None,
    cpm: float | None = None,
    fcp: float | None = None,
    class_name_raw: str | None = None,
    class_name_normalized: str | None = None,
    class_name_version: str | None = None,
) -> None:
    """Upsert one member-level snapshot."""

    conn.execute(
        """
        INSERT INTO member_snapshots (
            retrieved_at, guild_name, rank_no, class_name, family_name,
            level, cpm, fcp, class_name_raw, class_name_normalized,
            class_name_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(retrieved_at, guild_name, family_name) DO UPDATE SET
            rank_no = excluded.rank_no,
            class_name = excluded.class_name,
            level = excluded.level,
            cpm = excluded.cpm,
            fcp = excluded.fcp,
            class_name_raw = excluded.class_name_raw,
            class_name_normalized = excluded.class_name_normalized,
            class_name_version = excluded.class_name_version
        """,
        (
            retrieved_at,
            guild_name,
            rank_no,
            class_name,
            family_name,
            level,
            cpm,
            fcp,
            class_name_raw,
            class_name_normalized,
            class_name_version,
            utc_now_iso(),
        ),
    )


def save_member_snapshots(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    members: Sequence[Mapping[str, Any]],
) -> int:
    """Upsert many member snapshots and return the number processed."""

    count = 0
    for member in members:
        family_name = _clean_text(member.get("family_name"))
        if not family_name:
            continue
        save_member_snapshot(
            conn,
            retrieved_at=retrieved_at,
            guild_name=guild_name,
            rank_no=_to_int(member.get("rank_no")),
            class_name=_clean_text(member.get("class_name")),
            family_name=family_name,
            level=_to_int(member.get("level")),
            cpm=_to_float(member.get("cpm")),
            fcp=_to_float(member.get("fcp")),
            class_name_raw=_clean_text(member.get("class_name_raw")),
            class_name_normalized=_clean_text(member.get("class_name_normalized")),
            class_name_version=_clean_text(member.get("class_name_version")),
        )
        count += 1
    return count


def save_node_history_snapshots(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    node_history: Sequence[Mapping[str, Any]],
) -> int:
    """Upsert node-history rows and keep the original row as JSON."""

    count = 0
    for index, row in enumerate(node_history, start=1):
        row_no = _to_int(row.get("row_no")) or index
        conn.execute(
            """
            INSERT INTO node_history_snapshots (
                retrieved_at, guild_name, row_no, war_date, node_name,
                opponent_guild, result, raw_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(retrieved_at, guild_name, row_no) DO UPDATE SET
                war_date = excluded.war_date,
                node_name = excluded.node_name,
                opponent_guild = excluded.opponent_guild,
                result = excluded.result,
                raw_json = excluded.raw_json
            """,
            (
                retrieved_at,
                guild_name,
                row_no,
                _clean_text(row.get("war_date")),
                _clean_text(row.get("node_name")),
                _clean_text(row.get("opponent_guild")),
                _clean_text(row.get("result")),
                _json_dumps(row),
                utc_now_iso(),
            ),
        )
        count += 1
    return count


def save_guild_summary_snapshot(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    summary: Mapping[str, Any] | None,
) -> None:
    """Upsert one guild summary snapshot.

    Common analysis fields are stored as columns. The complete summary mapping is
    also preserved in summary_json so future analysis can use fields that were
    not known when this schema was created.
    """

    if not summary:
        return
    normalized = {str(k): v for k, v in summary.items() if k is not None}
    conn.execute(
        """
        INSERT INTO guild_summary_snapshots (
            retrieved_at, guild_name, avg_cp, total_cp, total_family_cp,
            active_member_count, low_member_cp, high_member_cp,
            declared_on_other_guild, declared_by_other_guild, total_war,
            all_time_win_rate, most_war_with_guild, total_node_wars, node_won,
            total_siege_wars, siege_won, currently_holding, summary_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(retrieved_at, guild_name) DO UPDATE SET
            avg_cp = excluded.avg_cp,
            total_cp = excluded.total_cp,
            total_family_cp = excluded.total_family_cp,
            active_member_count = excluded.active_member_count,
            low_member_cp = excluded.low_member_cp,
            high_member_cp = excluded.high_member_cp,
            declared_on_other_guild = excluded.declared_on_other_guild,
            declared_by_other_guild = excluded.declared_by_other_guild,
            total_war = excluded.total_war,
            all_time_win_rate = excluded.all_time_win_rate,
            most_war_with_guild = excluded.most_war_with_guild,
            total_node_wars = excluded.total_node_wars,
            node_won = excluded.node_won,
            total_siege_wars = excluded.total_siege_wars,
            siege_won = excluded.siege_won,
            currently_holding = excluded.currently_holding,
            summary_json = excluded.summary_json
        """,
        (
            retrieved_at,
            guild_name,
            _summary_float(normalized, "avg_cp"),
            _summary_int(normalized, "total_cp"),
            _summary_int(normalized, "total_family_cp"),
            _summary_int(normalized, "active_member_count"),
            _summary_int(normalized, "low_member_cp"),
            _summary_int(normalized, "high_member_cp"),
            _summary_int(normalized, "declared_on_other_guild"),
            _summary_int(normalized, "declared_by_other_guild"),
            _summary_int(normalized, "total_war"),
            _summary_float(normalized, "all_time_win_rate"),
            _summary_text(normalized, "most_war_with_guild"),
            _summary_int(normalized, "total_node_wars"),
            _summary_int(normalized, "node_won"),
            _summary_int(normalized, "total_siege_wars"),
            _summary_int(normalized, "siege_won"),
            _summary_text(normalized, "currently_holding"),
            _json_dumps(normalized),
            utc_now_iso(),
        ),
    )


def save_snapshot(
    conn: sqlite3.Connection,
    *,
    retrieved_at: str,
    guild_name: str,
    members: Sequence[Mapping[str, Any]],
    member_count: int | None = None,
    avg_cpm: float | None = None,
    total_cpm: float | None = None,
    node_history: Sequence[Mapping[str, Any]] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> int:
    """Save one guild snapshot, members, node history, and summary."""

    member_rows = [m for m in members if _clean_text(m.get("family_name"))]
    if member_count is None:
        member_count = len(member_rows)
    if total_cpm is None:
        cpm_values = [_to_float(m.get("cpm")) for m in member_rows]
        total_cpm = sum(v for v in cpm_values if v is not None)
    if avg_cpm is None and member_count:
        avg_cpm = total_cpm / member_count if total_cpm is not None else None

    with conn:
        save_guild_snapshot(
            conn,
            retrieved_at=retrieved_at,
            guild_name=guild_name,
            member_count=member_count,
            avg_cpm=avg_cpm,
            total_cpm=total_cpm,
        )
        member_rows_saved = save_member_snapshots(
            conn,
            retrieved_at=retrieved_at,
            guild_name=guild_name,
            members=member_rows,
        )
        if node_history:
            save_node_history_snapshots(
                conn,
                retrieved_at=retrieved_at,
                guild_name=guild_name,
                node_history=node_history,
            )
        if summary:
            save_guild_summary_snapshot(
                conn,
                retrieved_at=retrieved_at,
                guild_name=guild_name,
                summary=summary,
            )
        return member_rows_saved


def is_file_imported(conn: sqlite3.Connection, file_path: str | Path) -> bool:
    """Return True when an Excel file has already been imported.

    New records use a repository-relative path. For safety during migration, an
    old absolute-path record is also treated as imported and converted when
    possible.
    """

    relative_path = normalize_import_path(file_path)
    legacy_path = str(Path(file_path).resolve())
    rows = conn.execute("SELECT file_path FROM import_files").fetchall()
    row = next(
        (
            candidate
            for candidate in rows
            if candidate["file_path"] in {relative_path, legacy_path}
            or _path_text_to_project_relative(candidate["file_path"]) == relative_path
        ),
        None,
    )
    if row is None:
        return False
    if row["file_path"] != relative_path:
        _replace_import_file_path(conn, row["file_path"], relative_path, Path(file_path).name)
        conn.commit()
    return True


def mark_file_imported(conn: sqlite3.Connection, file_path: str | Path) -> None:
    """Record that an Excel file was imported using a project-relative path."""

    path = Path(file_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO import_files (file_path, file_name, imported_at)
        VALUES (?, ?, ?)
        """,
        (normalize_import_path(path), path.name, utc_now_iso()),
    )


def migrate_import_file_paths(conn: sqlite3.Connection) -> None:
    """Convert import_files.file_path values from absolute to relative paths.

    The migration is conservative: paths outside PROJECT_ROOT are left unchanged,
    so existing records are not destroyed.
    """

    if not _table_exists(conn, "import_files"):
        return
    rows = conn.execute("SELECT id, file_path, file_name FROM import_files").fetchall()
    for row in rows:
        relative_path = _absolute_to_project_relative(row["file_path"])
        if relative_path and relative_path != row["file_path"]:
            existing = conn.execute(
                "SELECT id FROM import_files WHERE file_path = ?", (relative_path,)
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM import_files WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE import_files SET file_path = ? WHERE id = ?",
                    (relative_path, row["id"]),
                )


def normalize_import_path(file_path: str | Path) -> str:
    """Return the import_files path stored in SQLite.

    If the file is inside the project, the result is like
    ``data/xAEGISx/guild_xAEGISx_2026-06-01.xlsx``. Otherwise, the resolved path
    is kept because it cannot be made project-relative safely.
    """

    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    relative_path = _path_to_project_relative(path)
    return relative_path if relative_path else str(path.resolve())


def _replace_import_file_path(
    conn: sqlite3.Connection, old_path: str, new_path: str, file_name: str
) -> None:
    existing = conn.execute(
        "SELECT id FROM import_files WHERE file_path = ?", (new_path,)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM import_files WHERE file_path = ?", (old_path,))
    else:
        conn.execute(
            "UPDATE import_files SET file_path = ?, file_name = ? WHERE file_path = ?",
            (new_path, file_name, old_path),
        )


def _ensure_columns(
    conn: sqlite3.Connection, table_name: str, columns: Mapping[str, str]
) -> None:
    existing_columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")
    }
    for column_name, column_type in columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone()
    return row is not None


def _path_to_project_relative(path: str | Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return None


def _absolute_to_project_relative(path_text: str) -> str | None:
    try:
        return _path_to_project_relative(Path(path_text))
    except (OSError, RuntimeError):
        return _path_text_to_project_relative(path_text)


def _path_text_to_project_relative(path_text: str) -> str | None:
    text = str(path_text).replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("data/"):
        return text
    marker = "/data/"
    if marker in text:
        return "data/" + text.split(marker, 1)[1]
    try:
        windows_path = PureWindowsPath(path_text)
        parts = list(windows_path.parts)
        if "data" in parts:
            index = parts.index("data")
            return PureWindowsPath(*parts[index:]).as_posix()
    except (TypeError, ValueError):
        return None
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").strip().replace("%", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _summary_value(summary: Mapping[str, Any], canonical_key: str) -> Any:
    aliases = SUMMARY_KEY_ALIASES.get(canonical_key, {canonical_key})
    normalized_summary = {_normalize_key(key): value for key, value in summary.items()}
    for alias in aliases:
        value = normalized_summary.get(_normalize_key(alias))
        if value not in (None, ""):
            return value
    return None


def _summary_int(summary: Mapping[str, Any], canonical_key: str) -> int | None:
    return _to_int(_summary_value(summary, canonical_key))


def _summary_float(summary: Mapping[str, Any], canonical_key: str) -> float | None:
    return _to_float(_summary_value(summary, canonical_key))


def _summary_text(summary: Mapping[str, Any], canonical_key: str) -> str | None:
    return _clean_text(_summary_value(summary, canonical_key))


def _normalize_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


SUMMARY_KEY_ALIASES: dict[str, set[str]] = {
    "avg_cp": {"avg_cp", "avg_cpm", "平均cp", "平均戦闘力", "平均CPM"},
    "total_cp": {"total_cp", "total_cpm", "合計cp", "総戦闘力", "合計CPM"},
    "total_family_cp": {"total_family_cp", "合計家門戦闘力", "総家門戦闘力"},
    "active_member_count": {"active_member_count", "active_members", "稼働人数"},
    "low_member_cp": {"low_member_cp", "最低cp", "最低戦闘力"},
    "high_member_cp": {"high_member_cp", "最高cp", "最高戦闘力"},
    "declared_on_other_guild": {"declared_on_other_guild", "布告した数"},
    "declared_by_other_guild": {"declared_by_other_guild", "布告された数"},
    "total_war": {"total_war", "総戦争数", "戦争数"},
    "all_time_win_rate": {"all_time_win_rate", "勝率", "通算勝率"},
    "most_war_with_guild": {"most_war_with_guild", "最多対戦ギルド"},
    "total_node_wars": {"total_node_wars", "拠点戦数"},
    "node_won": {"node_won", "拠点戦勝利数"},
    "total_siege_wars": {"total_siege_wars", "攻城戦数"},
    "siege_won": {"siege_won", "攻城戦勝利数"},
    "currently_holding": {"currently_holding", "現在保有", "保有拠点"},
}
