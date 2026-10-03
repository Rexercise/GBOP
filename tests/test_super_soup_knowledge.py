"""Shared knowledge and analyzer wiring contract."""
from pathlib import Path
import unittest
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES


class SuperSoupKnowledgeTests(unittest.TestCase):
    def test_shared_canon_keeps_structure_separate_from_outcome(self):
        self.assertIn('gtop_super_soup.txt', KNOWLEDGE_FILES)
        for term in ('Cleanliness describes STRUCTURE', 'delivery describes OUTCOME',
                     'do NOT require an immediate-next-candle Super Soup',
                     'clean-but-failed', 'not-clean-but-function-delivered',
                     'model1.lifecycle', 'NOT continuous monitoring'):
            self.assertIn(term, CANONICAL_KNOWLEDGE)
        for term in ('local_crt_outcome', 'local_function_outcome', 'local_function_objectives',
                     'relative_to_model1_invalidation', 'never restores CRT validity'):
            self.assertIn(term, CANONICAL_KNOWLEDGE)

    def test_shift_reuses_enriched_model1_evidence(self):
        root = Path(__file__).resolve().parents[1] / 'gbop_voice_web'
        self.assertIn('enrich_model1(bars, anchor', (root / 'candle_evidence.py').read_text())
        self.assertIn("'model1': evidence['model1']", (root / 'shift_review.py').read_text())


if __name__ == '__main__':
    unittest.main()
