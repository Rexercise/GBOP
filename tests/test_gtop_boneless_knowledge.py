"""Knowledge/wiring regressions; these do not simulate live model behavior."""
import ast
from pathlib import Path
import unittest

from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES, infer_tier

ROOT = Path(__file__).resolve().parent.parent


class BonelessKnowledgeTests(unittest.TestCase):
    def test_all_canonical_sources_are_loaded_in_order(self):
        expected = "\n\n".join(
            (ROOT / "gbop_voice_web" / name).read_text(encoding="utf-8").strip()
            for name in KNOWLEDGE_FILES
        )
        self.assertEqual(CANONICAL_KNOWLEDGE, expected)
        self.assertIn("gtop_boneless.txt", KNOWLEDGE_FILES)

    def test_boneless_is_the_nonpurging_side(self):
        self.assertIn("Boneless names the correlated asset that does NOT visibly purge", CANONICAL_KNOWLEDGE)
        self.assertIn("partner's visible purge supplies the functional purge context", CANONICAL_KNOWLEDGE)
        self.assertIn("gold is the boneless side", CANONICAL_KNOWLEDGE)
        self.assertIn("bullish counterpart reverses the sides", CANONICAL_KNOWLEDGE)
        self.assertIn("Boneless is an ADJECTIVE", CANONICAL_KNOWLEDGE)
        self.assertNotIn("Gold completed the boneless SMT objective", CANONICAL_KNOWLEDGE)

    def test_delivery_uses_own_objective_and_preserves_chronology(self):
        self.assertIn("ITS OWN selected range", CANONICAL_KNOWLEDGE)
        self.assertIn("Gold was boneless and completed its sell-side objective", CANONICAL_KNOWLEDGE)
        self.assertIn("A midpoint touch alone is midpoint delivery", CANONICAL_KNOWLEDGE)
        self.assertIn("A partner reaching its own objective does not prove", CANONICAL_KNOWLEDGE)
        self.assertIn("Later invalidation does not erase earlier SMT", CANONICAL_KNOWLEDGE)
        self.assertIn("A touch only after thesis invalidation is not valid-thesis objective completion", CANONICAL_KNOWLEDGE)

    def test_smt_does_not_invent_candles_or_execution(self):
        self.assertIn("Do not fabricate a local sweep, local Model 1 candle", CANONICAL_KNOWLEDGE)
        self.assertIn("uncertain chronology, or an unverified target touch mean unverified", CANONICAL_KNOWLEDGE)
        self.assertIn("Boneless is a structural descriptor, not a new execution model or risk tier", CANONICAL_KNOWLEDGE)
        self.assertIn("Do not claim continuous monitoring, pre-analysis", CANONICAL_KNOWLEDGE)

    def test_proactive_coaching_is_time_aware_not_hindsight(self):
        self.assertIn("Volunteer relevant verified SMT context without waiting", CANONICAL_KNOWLEDGE)
        self.assertIn("A post-entry SMT cannot be presented as a missed pre-entry observation", CANONICAL_KNOWLEDGE)
        self.assertIn("SMT alone does not prove why a trade lost", CANONICAL_KNOWLEDGE)
        self.assertIn("Objective completion does not guarantee continuation", CANONICAL_KNOWLEDGE)
        self.assertIn("NAS100/SPX", CANONICAL_KNOWLEDGE)
        self.assertIn("never pretend an SPX feed is connected", CANONICAL_KNOWLEDGE)

    def test_model1_identity_correction_is_preserved(self):
        self.assertIn("Model 1 candle identification is separate from subsequent CSD/CISD confirmation", CANONICAL_KNOWLEDGE)
        self.assertIn("An unconfirmed Model 1 and a failed Model 1 are still Model 1 candles", CANONICAL_KNOWLEDGE)
        self.assertNotIn("MODEL 1 CANDIDATE / BODY-CLOSE LOGIC", CANONICAL_KNOWLEDGE)
        self.assertNotIn("Model 1 confirms only when", CANONICAL_KNOWLEDGE)

    def test_existing_entry_tiers_are_unchanged(self):
        for name, tier in (("Super Soup", 1), ("Model 1", 2), ("Turtle Body Soup", 2),
                           ("Turtle Wick Soup", 2), ("KOD Turtle Soup", 2),
                           ("Breaker OTE", 3), ("Blessed Thief", 3)):
            with self.subTest(name=name):
                self.assertEqual(infer_tier(name), tier)
        self.assertIsNone(infer_tier("boneless"))

    def test_both_runtimes_import_shared_canon(self):
        for filename in ("bot.py", "gbop_voice_web/server.py"):
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
                self.assertTrue(any(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "gbop_voice_web.gtop_protocol"
                    and any(alias.name == "CANONICAL_KNOWLEDGE" for alias in node.names)
                    for node in ast.walk(tree)
                ))


if __name__ == "__main__":
    unittest.main()
