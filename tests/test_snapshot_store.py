# -*- coding: utf-8 -*-
"""Tests for snapshot_store and the SQLite analyze path.

実行方法（リポジトリ直下で）:
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from src import db
from src import snapshot_store
from src.analyze import AnalysisSettings, analyze_guild, analyze_guild_sqlite

GUILD = "TestGuild"

OLD_MEMBERS = [
    {"family_name": "Alpha", "rank_no": 1, "level": 80, "cpm": 120000, "fcp": 200000},
    {"family_name": "Bravo", "rank_no": 2, "level": 78, "cpm": 100000, "fcp": 180000},
    {"family_name": "Charlie", "rank_no": 3, "level": 75, "cpm": 90000, "fcp": 150000},
]
NEW_MEMBERS = [
    {"family_name": "Alpha", "rank_no": 1, "level": 81, "cpm": 131000, "fcp": 210000},
    {"family_name": "Bravo", "rank_no": 2, "level": 79, "cpm": 111000, "fcp": 190000},
    {"family_name": "Charlie", "rank_no": 3, "level": 76, "cpm": 95000, "fcp": 155000},
    {"family_name": "Delta", "rank_no": 4, "level": 70, "cpm": 88000, "fcp": 120000},
]
SUMMARY = {
    "avg_cp": "108500",
    "total_cp": "434000",
    "total_family_cp": "675000",
    "active_member_count": "4",
    "low_member_cp": "88000",
    "high_member_cp": "131000",
    "declared_on_other_guild": "3",
    "declared_by_other_guild": "1",
    "total_war": "21",
    "all_time_win_rate": "52.38%",
    "most_war_with_guild": "RivalGuild",
    "total_node_wars": "10",
    "node_won": "6",
    "total_siege_wars": "4",
    "siege_won": "1",
    "currently_holding": "Mediah 1",
}
OLD_DATE = "2026-06-01"
NEW_DATE = "2026-06-08"


def build_db(db_path: Path) -> None:
    conn = db.initialize(db_path)
    try:
        db.save_snapshot(
            conn,
            retrieved_at=f"{OLD_DATE} 21:00",
            guild_name=GUILD,
            members=OLD_MEMBERS,
            summary=SUMMARY,
        )
        # 同日リトライ（古い時刻）: 最新時刻のスナップショットだけが使われること。
        db.save_snapshot(
            conn,
            retrieved_at=f"{NEW_DATE} 20:00",
            guild_name=GUILD,
            members=OLD_MEMBERS,
            summary=SUMMARY,
        )
        db.save_snapshot(
            conn,
            retrieved_at=f"{NEW_DATE} 22:30",
            guild_name=GUILD,
            members=NEW_MEMBERS,
            summary=SUMMARY,
        )
    finally:
        conn.close()


def build_guild_workbook(path: Path, members: list[dict], retrieved_at: str) -> None:
    workbook = Workbook()
    ws = workbook.active
    ws.title = "members"
    ws.append(["rank", "player_name", "level", "cpm", "fcp", "retrieved_at"])
    for member in members:
        ws.append(
            [
                member["rank_no"],
                member["family_name"],
                member["level"],
                member["cpm"],
                member["fcp"],
                retrieved_at,
            ]
        )
    summary_sheet = workbook.create_sheet("summary")
    keys = ["guild_name", "retrieved_at"] + list(SUMMARY.keys())
    summary_sheet.append(keys)
    summary_sheet.append([GUILD, retrieved_at] + list(SUMMARY.values()))
    workbook.save(path)


class SnapshotStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.sqlite3"
        build_db(self.db_path)
        self.conn = snapshot_store.open_connection(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_snapshot_dates(self) -> None:
        self.assertEqual(
            snapshot_store.snapshot_dates(self.conn, GUILD), [OLD_DATE, NEW_DATE]
        )
        self.assertEqual(
            snapshot_store.snapshot_dates(self.conn, GUILD, as_of_date=OLD_DATE),
            [OLD_DATE],
        )
        self.assertEqual(snapshot_store.snapshot_dates(self.conn, "Nope"), [])

    def test_latest_retrieved_at_prefers_same_day_retry(self) -> None:
        self.assertEqual(
            snapshot_store.latest_retrieved_at(self.conn, GUILD, NEW_DATE),
            f"{NEW_DATE} 22:30",
        )

    def test_member_rows_shape(self) -> None:
        rows = snapshot_store.member_rows(self.conn, GUILD, f"{NEW_DATE} 22:30")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["player_name"], "Alpha")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["cpm"], 131000)
        self.assertIn("fcp", rows[0])

    def test_summary_mapping_prefers_original_strings(self) -> None:
        summary = snapshot_store.summary_mapping(self.conn, GUILD, f"{NEW_DATE} 22:30")
        self.assertEqual(summary["guild_name"], GUILD)
        self.assertEqual(summary["all_time_win_rate"], "52.38%")
        self.assertEqual(summary["currently_holding"], "Mediah 1")

    def test_guild_names_and_sanitized_map(self) -> None:
        self.assertEqual(snapshot_store.guild_names(self.conn), [GUILD])
        self.assertEqual(snapshot_store.sanitized_name_map(self.conn), {GUILD: GUILD})


class SqliteExcelParityTest(unittest.TestCase):
    """同一データでExcel版とSQLite版の分析結果が一致することを保証する。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.sqlite3"
        build_db(self.db_path)
        self.guild_dir = self.tmp / GUILD
        self.guild_dir.mkdir()
        build_guild_workbook(
            self.guild_dir / f"guild_{GUILD}_{OLD_DATE}.xlsx",
            OLD_MEMBERS,
            f"{OLD_DATE} 21:00",
        )
        build_guild_workbook(
            self.guild_dir / f"guild_{GUILD}_{NEW_DATE}.xlsx",
            NEW_MEMBERS,
            f"{NEW_DATE} 22:30",
        )
        self.settings = AnalysisSettings(top_avg_counts=[10, 15, 20, 25])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parity(self) -> None:
        excel_metrics = analyze_guild(self.guild_dir, self.settings)
        conn = snapshot_store.open_connection(self.db_path)
        try:
            sqlite_metrics = analyze_guild_sqlite(conn, GUILD, self.settings)
        finally:
            conn.close()

        compare_keys = [
            "guild_name",
            "member_count",
            "avg_cpm",
            "median_cpm",
            "max_cpm",
            "min_cpm",
            "stdev_cpm",
            "top10_avg_cpm",
            "top25_avg_cpm",
            "cpm_130000_plus_count",
            "cpm_under_90000_count",
            "total_cp",
            "active_member_count",
            "all_time_win_rate",
            "node_win_rate",
            "siege_win_rate",
            "node_siege_total",
            "node_siege_win_total",
            "currently_holding",
            "prev_avg_cpm",
            "avg_cpm_growth",
            "avg_cpm_growth_rate",
        ]
        for key in compare_keys:
            self.assertEqual(
                excel_metrics.get(key),
                sqlite_metrics.get(key),
                msg=f"key={key}: excel={excel_metrics.get(key)!r} sqlite={sqlite_metrics.get(key)!r}",
            )
        # source_file はExcel実体が無いのでsqlite仮想パスになる。
        self.assertTrue(str(sqlite_metrics["source_file"]).startswith("sqlite:"))

    def test_as_of_date(self) -> None:
        conn = snapshot_store.open_connection(self.db_path)
        try:
            metrics = analyze_guild_sqlite(
                conn, GUILD, self.settings, as_of_date=OLD_DATE
            )
        finally:
            conn.close()
        self.assertEqual(metrics["member_count"], 3)
        self.assertEqual(metrics["prev_avg_cpm"], "")


if __name__ == "__main__":
    unittest.main()
