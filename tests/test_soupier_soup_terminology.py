"""Narrow user terminology must not silently introduce trading rules."""
from pathlib import Path
import sqlite3
import unittest

from gbop_voice_web.gtop_protocol import (
    CANONICAL_KNOWLEDGE,
    KNOWLEDGE_FILES,
    classification_warning,
    infer_tier,
    recognize_soupier_soup,
    tier_used_r,
)


class SoupierSoupTerminologyTests(unittest.TestCase):
    def test_exact_term_returns_canonical_spelling(self):
        for text in (
            "Soupier Soup",
            "SOUPIER SOUP",
            "soupier   soup",
            "Describe Soupier\nSoup, please.",
            "Soupier Soup (soup of a Super Soup)",
            "S O U P I E R Soup",
        ):
            with self.subTest(text=text):
                self.assertEqual(recognize_soupier_soup(text), "Soupier Soup")

    def test_no_fuzzy_alias_or_concept_only_recognition(self):
        for text in (
            None, "", "Super Soup", "Soupiest Soup", "SoupierSoup",
            "Soupier soups", "Soupier soupish", "Xsoupier soup",
            "Soupier-Soup", "Soupier_Soup", "S O U P E R Soup",
            "soup of a Super Soup",
        ):
            with self.subTest(text=text):
                self.assertIsNone(recognize_soupier_soup(text))

    def test_term_and_nested_model_names_do_not_infer_tier(self):
        for text in (
            "Soupier Soup",
            "Soupier Soup (soup of a Super Soup)",
            "S O U P I E R Soup (soup of a Super Soup)",
            "Soupier Soup of a Super Soup of Model 1",
            "Soupier Soup; entry timing, KOD and Breaker rules unspecified",
        ):
            with self.subTest(text=text):
                self.assertIsNone(infer_tier(text))
                for invalid in (0, 4, "1"):
                    self.assertIsNone(infer_tier(text, invalid))
                for supplied in (1, 2, 3):
                    self.assertEqual(infer_tier(text, supplied), supplied)
                    self.assertIsNone(classification_warning(text, supplied))

    def test_other_model_classifications_are_unchanged(self):
        cases = {
            "Super Soup of Model 1": 1,
            "Super Soup of a Turtle Body Soup": 1,
            "KOD Super Soup": 2,
            "Turtle Body Soup": 2,
            "Model 1 / CISD": 2,
            "Turtle Wick Soup": 2,
            "Blessed Thief": 3,
            "Breaker / OTE": 3,
            "88.7": 3,
            "Super Soup of a wick": None,
            "Turtle Soup": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(infer_tier(text), expected)

    def test_budget_uses_supplied_tier_without_rewriting_history(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE thesis_executions "
            "(thesis_id, entry_model, tier, risk_r)"
        )
        entries = [
            (1, "Soupier Soup (soup of a Super Soup)", 2, .2),
            (1, "Soupier Soup", None, .1),
            (1, "Super Soup of Model 1", 1, .3),
        ]
        conn.executemany("INSERT INTO thesis_executions VALUES (?,?,?,?)", entries)
        self.assertAlmostEqual(tier_used_r(lambda: conn, 1, 1), .3)
        self.assertAlmostEqual(tier_used_r(lambda: conn, 1, 2), .2)
        self.assertEqual(conn.execute("SELECT * FROM thesis_executions").fetchall(), entries)

    def test_shared_canon_and_traceable_source_record(self):
        self.assertEqual(KNOWLEDGE_FILES.count("gtop_soupier_soup.txt"), 1)
        root = Path(__file__).resolve().parents[1] / "gbop_voice_web"
        canonical = (root / "gtop_soupier_soup.txt").read_text().strip()
        self.assertEqual(CANONICAL_KNOWLEDGE.count(canonical), 1)
        for text in (
            "Soupier Soup (S O U P I E R Soup)",
            '"soup of a Super Soup"',
            "remain unspecified",
            "Do not infer a tier, entry model classification or trade classification",
            "All existing rules for other named models remain unchanged",
        ):
            self.assertIn(text, canonical)
        record = (root / "SOUPIER_SOUP.md").read_text()
        for text in (
            "GTOP-TERM-SOUPIER-SOUP", "Revision: 1", "Status: active",
            "relayed on 2026-10-07", "original user message ID not provided",
            "not an inferred effective date",
        ):
            self.assertIn(text, record)


if __name__ == "__main__":
    unittest.main()
