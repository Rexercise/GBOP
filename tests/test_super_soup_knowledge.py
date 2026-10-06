"""Shared knowledge and analyzer wiring contract."""
from pathlib import Path
import unittest
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES


class SuperSoupKnowledgeTests(unittest.TestCase):
    def test_shared_canon_keeps_structure_separate_from_outcome(self):
        self.assertIn('gtop_super_soup.txt', KNOWLEDGE_FILES)
        for term in ('canonical V1–V6', 'V4/V5 allow inside bars',
                     'V6 re-soups the manipulation extreme', 'valid completed Super Soup',
                     'no inside close is required after full delivery',
                     'SS and CISD may confirm on the same assigned close',
                     'Same-source-bar order stays unverified', 'model1.lifecycle'):
            self.assertIn(term, CANONICAL_KNOWLEDGE)
        for term in ('local_function delivery, parent objectives, CISD and execution separate',
                     'Later delivery never restores a previously failed CRT',
                     'relative_to_model1_invalidation', 'No fills, profits, superiority, probability'):
            self.assertIn(term, CANONICAL_KNOWLEDGE)

    def test_shift_reuses_enriched_model1_evidence(self):
        root = Path(__file__).resolve().parents[1] / 'gbop_voice_web'
        self.assertIn('enrich_model1(bars, anchor', (root / 'candle_evidence.py').read_text())
        self.assertIn("'model1': evidence['model1']", (root / 'shift_review.py').read_text())


if __name__ == '__main__':
    unittest.main()
