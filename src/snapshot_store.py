"""Read-side access layer for the guild snapshot SQLite database.

``db.py`` is the write side used by the scraper; this module is the read side
used by ``analyze.py`` (and as a fallback by ``comment_source.py``).  It exposes
guild snapshots grouped by *collection date* (the ``YYYY-MM-DD`` prefix of
``retrieved_at``) so that the semantics match the historical Excel files:

* One Excel workbook per guild per date, overwritten on same-day re-scrapes.
  → Here: within one date, only the newest ``retrieved_at`` group is used.
* "Previous data" for growth = the newest workbook before the latest one.
  → Here: the previous distinct date's newest ``retrieved_at`` group.

Member rows are returned with the same keys the Excel ``members`` sheet uses
(``rank`` / ``player_name`` / ``level`` / ``cpm`` / ``fcp``) so downstream code
can consume either source interchangeably.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from . import db
    from .paths import DATA_DIR
    from .scraper import sanitize_filename
except ImportError:  # 直接実行された場合のため
    import db  # type: ignore
    from paths import DATA_DIR  # type: ignore
    from scraper import sanitize_filename  # type: ignore


# guild_summary_snapshots の列のうち、Excel の summary シートにも存在する項目。
SUMMARY_COLUMNS = (
    "avg_cp",
    "total_cp",
    "total_family_cp",
    "active_member_count",
    "low_member_cp",
    "high_member_cp",
    "declared_on_other_guild",
    "declared_by_other_guild",
    "total_war",
    "all_time_win_rate",
    "most_war_with_guild",
    "total_node_wars",
    "node_won",
    "total_siege_wars",
    "siege_won",
    "currently_holding",
)


def database_exists(db_path: str | Path | None = None) -> bool:
    """Return True when the snapshot database file exists on disk."""

    path = Path(db_path) if db_path is not None else db.DEFAULT_DB_PATH
    return path.exists()


def open_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a read/write connection (rows behave like dicts)."""

    return db.connect(db_path)


def guild_names(conn: sqlite3.Connection) -> list[str]:
    """Return every guild that has at least one member snapshot."""

    rows = conn.execute(
        "SELECT DISTINCT guild_name FROM member_snapshots ORDER BY guild_name"
    ).fetchall()
    return [row["guild_name"] for row in rows]


def sanitized_name_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Map scraper-sanitized folder names back to the real guild names.

    ``data/<dir>`` folders are created with ``scraper.sanitize_filename``; using
    the same function here keeps the mapping exact.
    """

    return {sanitize_filename(name): name for name in guild_names(conn)}


def snapshot_dates(
    conn: sqlite3.Connection, guild_name: str, as_of_date: str | None = None
) -> list[str]:
    """Distinct collection dates (``YYYY-MM-DD``) for one guild, oldest first."""

    sql = (
        "SELECT DISTINCT substr(retrieved_at, 1, 10) AS snapshot_date "
        "FROM member_snapshots WHERE guild_name = ?"
    )
    params: list[Any] = [guild_name]
    if as_of_date:
        sql += " AND substr(retrieved_at, 1, 10) <= ?"
        params.append(as_of_date)
    sql += " ORDER BY snapshot_date"
    return [row["snapshot_date"] for row in conn.execute(sql, params)]


def latest_retrieved_at(
    conn: sqlite3.Connection, guild_name: str, on_date: str
) -> str | None:
    """Newest full ``retrieved_at`` within one collection date.

    Excel の同日再取得は同名ファイル上書きだったため、SQLite でも同日内は
    最新の取得だけを「その日のスナップショット」として扱う。
    """

    row = conn.execute(
        "SELECT MAX(retrieved_at) AS newest FROM member_snapshots "
        "WHERE guild_name = ? AND substr(retrieved_at, 1, 10) = ?",
        (guild_name, on_date),
    ).fetchone()
    return row["newest"] if row and row["newest"] else None


def member_rows(
    conn: sqlite3.Connection, guild_name: str, retrieved_at: str
) -> list[dict[str, Any]]:
    """Member rows shaped like the Excel ``members`` sheet."""

    rows = conn.execute(
        "SELECT rank_no, family_name, level, cpm, fcp FROM member_snapshots "
        "WHERE guild_name = ? AND retrieved_at = ? "
        "ORDER BY (rank_no IS NULL), rank_no, family_name",
        (guild_name, retrieved_at),
    ).fetchall()
    return [
        {
            "rank": row["rank_no"],
            "player_name": row["family_name"],
            "level": row["level"],
            "cpm": row["cpm"],
            "fcp": row["fcp"],
            "retrieved_at": retrieved_at,
        }
        for row in rows
    ]


def member_cpms(
    conn: sqlite3.Connection, guild_name: str, retrieved_at: str
) -> list[float]:
    """Numeric CPM values for one snapshot (matches ``read_cpm_values``)."""

    rows = conn.execute(
        "SELECT cpm FROM member_snapshots "
        "WHERE guild_name = ? AND retrieved_at = ? AND cpm IS NOT NULL",
        (guild_name, retrieved_at),
    ).fetchall()
    return [float(row["cpm"]) for row in rows]


def has_member_data(conn: sqlite3.Connection, guild_name: str) -> bool:
    """Return True when the guild has at least one member snapshot."""

    row = conn.execute(
        "SELECT 1 FROM member_snapshots WHERE guild_name = ? LIMIT 1",
        (guild_name,),
    ).fetchone()
    return row is not None


def summary_mapping(
    conn: sqlite3.Connection, guild_name: str, retrieved_at: str
) -> dict[str, Any]:
    """One guild summary shaped like the Excel ``summary`` sheet first row.

    ``summary_json``（scraper が保存した元の文字列値）を優先し、無い項目だけ
    型付きカラムで補う。完全一致の ``retrieved_at`` が無ければ同日の最新行を使う。
    """

    row = conn.execute(
        "SELECT * FROM guild_summary_snapshots "
        "WHERE guild_name = ? AND retrieved_at = ?",
        (guild_name, retrieved_at),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM guild_summary_snapshots "
            "WHERE guild_name = ? AND substr(retrieved_at, 1, 10) = ? "
            "ORDER BY retrieved_at DESC LIMIT 1",
            (guild_name, retrieved_at[:10]),
        ).fetchone()
    if row is None:
        return {"guild_name": guild_name, "retrieved_at": retrieved_at}

    summary: dict[str, Any] = {
        "guild_name": guild_name,
        "retrieved_at": row["retrieved_at"],
    }
    for column in SUMMARY_COLUMNS:
        summary[column] = row[column]
    raw_json = row["summary_json"]
    if raw_json:
        try:
            original = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            original = None
        if isinstance(original, dict):
            for key, value in original.items():
                if value not in (None, ""):
                    summary[str(key)] = value
    return summary


def excel_workbook_path(guild_name: str, snapshot_date: str) -> Path | None:
    """Locate the Excel workbook the scraper wrote for this snapshot, if any.

    SQLite を入力にしても、下流（comment_source / pv_tracker）が参照できるよう
    summary の ``source_file`` には実在する Excel パスを入れたい。scraper と同じ
    命名規則で探し、無ければ None。
    """

    safe_name = sanitize_filename(guild_name)
    candidate = DATA_DIR / safe_name / f"guild_{safe_name}_{snapshot_date}.xlsx"
    return candidate if candidate.exists() else None
