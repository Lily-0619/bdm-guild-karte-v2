from pathlib import Path
import sqlite3
import unittest

from src import analyze, db


class SQLiteSourceOfTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path.cwd() / ".test_sqlite_source_of_truth.sqlite3"
        self.database_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.database_path.unlink(missing_ok=True)

    def test_person_ids_are_stable_and_manual_merge_is_persistent(self) -> None:
            conn = db.initialize(self.database_path)
            db.save_snapshot(
                conn,
                retrieved_at="2026-09-01 09:00",
                guild_name="Alpha",
                members=[{"family_name": "Lily", "cpm": 100, "fcp": 200, "level": 90}],
            )
            db.save_snapshot(
                conn,
                retrieved_at="2026-09-08 09:00",
                guild_name="Alpha",
                members=[{"family_name": "Lily", "cpm": 110, "fcp": 220, "level": 91}],
            )
            ids = [row["person_id"] for row in conn.execute(
                "SELECT person_id FROM member_snapshots WHERE guild_name='Alpha' ORDER BY retrieved_at"
            )]
            self.assertEqual(ids[0], ids[1])

            db.save_snapshot(
                conn,
                retrieved_at="2026-09-15 09:00",
                guild_name="Beta",
                members=[{"family_name": "Lily", "cpm": 120, "fcp": 240, "level": 92}],
            )
            beta_id = conn.execute(
                "SELECT person_id FROM member_snapshots WHERE guild_name='Beta'"
            ).fetchone()["person_id"]
            self.assertNotEqual(ids[0], beta_id)
            db.merge_persons(conn, keep_person_id=ids[0], merge_person_id=beta_id)
            history = db.person_history(conn, ids[0])
            self.assertEqual(len(history), 3)
            self.assertEqual({row["guild_name"] for row in history}, {"Alpha", "Beta"})
            conn.close()

    def test_existing_member_rows_are_backfilled_without_deletion(self) -> None:
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            CREATE TABLE member_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                retrieved_at TEXT NOT NULL, guild_name TEXT NOT NULL,
                rank_no INTEGER, class_name TEXT, family_name TEXT NOT NULL,
                level INTEGER, cpm REAL, fcp REAL, created_at TEXT,
                UNIQUE(retrieved_at, guild_name, family_name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO member_snapshots
                (retrieved_at, guild_name, family_name, cpm, fcp)
            VALUES ('2026-08-01 09:00', 'Legacy', 'OldName', 123, 456)
            """
        )
        conn.commit()
        conn.close()

        migrated = db.initialize(self.database_path)
        row = migrated.execute(
            "SELECT family_name, cpm, fcp, person_id FROM member_snapshots"
        ).fetchone()
        self.assertEqual((row["family_name"], row["cpm"], row["fcp"]), ("OldName", 123, 456))
        self.assertIsNotNone(row["person_id"])
        migrated.close()

    def test_guild_analysis_reads_sqlite_and_calculates_growth(self) -> None:
            database_path = self.database_path
            conn = db.initialize(database_path)
            for date, values in (("2026-09-01 09:00", [100, 200]), ("2026-09-08 09:00", [200, 400])):
                db.save_snapshot(
                    conn,
                    retrieved_at=date,
                    guild_name="Alpha",
                    members=[{"family_name": f"P{i}", "cpm": value} for i, value in enumerate(values)],
                    summary={"total_node_wars": 10, "node_won": 5},
                )
            conn.close()

            original_path = analyze.DB_PATH
            original_session_loader = analyze.session_state.load_session
            try:
                analyze.DB_PATH = database_path
                analyze.session_state.load_session = lambda: None
                metrics, failures = analyze.collect_metrics(analyze.AnalysisSettings([10]))
            finally:
                analyze.DB_PATH = original_path
                analyze.session_state.load_session = original_session_loader
            self.assertEqual(failures, [])
            self.assertEqual(metrics[0]["avg_cpm"], 300)
            self.assertEqual(metrics[0]["prev_avg_cpm"], 150)
            self.assertEqual(metrics[0]["avg_cpm_growth"], 150)
            self.assertEqual(metrics[0]["node_win_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
