# -*- coding: utf-8 -*-
"""Tests for the pipeline stage planning and session guard."""

from __future__ import annotations

import unittest

from src.pipeline import Stage, build_stage_plan, session_guard_message


class BuildStagePlanTest(unittest.TestCase):
    def test_full_plan_order(self) -> None:
        labels = [stage.label for stage in build_stage_plan()]
        self.assertEqual(
            labels,
            ["DBonkデータ取得", "一覧作成", "コメント元データ作成", "AIコメント生成", "カルテ作成"],
        )

    def test_skip_flags(self) -> None:
        labels = [
            stage.label
            for stage in build_stage_plan(skip_scrape=True, skip_comments=True)
        ]
        self.assertEqual(labels, ["一覧作成", "カルテ作成"])

    def test_comment_stages_are_not_fatal(self) -> None:
        plan = {stage.label: stage for stage in build_stage_plan()}
        self.assertTrue(plan["DBonkデータ取得"].fatal)
        self.assertTrue(plan["一覧作成"].fatal)
        self.assertFalse(plan["コメント元データ作成"].fatal)
        self.assertFalse(plan["AIコメント生成"].fatal)
        self.assertTrue(plan["カルテ作成"].fatal)

    def test_model_and_source_args(self) -> None:
        plan = {stage.script: stage for stage in build_stage_plan(model="gemma3:4b", source="sqlite")}
        self.assertEqual(plan["analyze.py"].args, ("--source", "sqlite"))
        self.assertEqual(plan["comment_source.py"].args, ("--model", "gemma3:4b"))
        self.assertEqual(plan["comment_ai.py"].args, ("--model", "gemma3:4b"))
        self.assertEqual(plan["make_card.py"].args, ())

    def test_capture_api_arg(self) -> None:
        plan = {stage.script: stage for stage in build_stage_plan(capture_api="data/cap")}
        self.assertEqual(plan["scraper.py"].args, ("--capture-api", "data/cap"))

    def test_all_skipped(self) -> None:
        self.assertEqual(
            build_stage_plan(
                skip_scrape=True, skip_analyze=True, skip_comments=True, skip_cards=True
            ),
            [],
        )


class SessionGuardTest(unittest.TestCase):
    TODAY = "2026-07-02"

    def test_no_session_passes(self) -> None:
        self.assertIsNone(session_guard_message(None, self.TODAY))

    def test_today_session_passes(self) -> None:
        session = {"session_date": self.TODAY, "guilds": {"A": "x"}}
        self.assertIsNone(session_guard_message(session, self.TODAY))

    def test_stale_session_blocks(self) -> None:
        session = {"session_date": "2026-06-20", "guilds": {"A": "x", "B": "y"}}
        message = session_guard_message(session, self.TODAY)
        self.assertIsNotNone(message)
        self.assertIn("2026-06-20", message)
        self.assertIn("--new-session", message)
        self.assertIn("--continue-session", message)


if __name__ == "__main__":
    unittest.main()
