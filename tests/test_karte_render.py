# -*- coding: utf-8 -*-
"""Tests for the Pillow card renderer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import karte_render
from src.paths import PROJECT_ROOT

TEMPLATE = PROJECT_ROOT / "template" / "karte.xlsx"


class FormatCellValueTest(unittest.TestCase):
    def test_thousands(self) -> None:
        self.assertEqual(karte_render.format_cell_value(108543, "#,##0"), "108,543")

    def test_plain_int(self) -> None:
        self.assertEqual(karte_render.format_cell_value(46, "0"), "46")

    def test_general_integer_float(self) -> None:
        self.assertEqual(karte_render.format_cell_value(5.0, "General"), "5")

    def test_percent(self) -> None:
        self.assertEqual(karte_render.format_cell_value(0.6, "0.00%"), "60.00%")

    def test_none_and_string(self) -> None:
        self.assertEqual(karte_render.format_cell_value(None, "General"), "")
        self.assertEqual(karte_render.format_cell_value("Mediah 1", "General"), "Mediah 1")


class ColorTest(unittest.TestCase):
    def test_apply_tint_lightens(self) -> None:
        # accent1 #4472C4 + tint 0.8 → Excel の D9E2F3 近似（±2/チャンネル許容）
        result = karte_render.apply_tint((0x44, 0x72, 0xC4), 0.7999816888943144)
        expected = (0xD9, 0xE2, 0xF3)
        for got, want in zip(result, expected):
            self.assertLessEqual(abs(got - want), 2)

    def test_apply_tint_zero(self) -> None:
        self.assertEqual(karte_render.apply_tint((10, 20, 30), 0.0), (10, 20, 30))

    def test_load_theme_colors_from_template(self) -> None:
        if not TEMPLATE.exists():
            self.skipTest("template/karte.xlsx がありません")
        colors = karte_render.load_theme_colors(TEMPLATE)
        self.assertEqual(colors.get("accent1"), "4472C4")
        self.assertEqual(colors.get("lt1"), "FFFFFF")
        self.assertEqual(colors.get("dk1"), "000000")


class WrapTest(unittest.TestCase):
    def test_tokenize_keeps_words(self) -> None:
        tokens = karte_render._tokenize_for_wrap("CPM 108,543 と日本語")
        self.assertIn("CPM ", tokens)
        self.assertIn("日", tokens)
        self.assertEqual("".join(tokens), "CPM 108,543 と日本語")


class RenderSmokeTest(unittest.TestCase):
    def test_render_template_sheets(self) -> None:
        if not TEMPLATE.exists():
            self.skipTest("template/karte.xlsx がありません")
        with tempfile.TemporaryDirectory() as tmp:
            karte_png = Path(tmp) / "karte.png"
            members_png = Path(tmp) / "members.png"
            karte_render.render_range_to_png(TEMPLATE, "カルテ", "A1:M30", karte_png)
            karte_render.render_range_to_png(TEMPLATE, "個人戦闘力一覧", "A1:G28", members_png)
            self.assertTrue(karte_png.exists())
            self.assertTrue(members_png.exists())
            self.assertGreater(karte_png.stat().st_size, 10_000)
            self.assertGreater(members_png.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
