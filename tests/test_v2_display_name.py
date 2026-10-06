"""Canonical V2 presentation preserves codes, evidence, and classification."""
from copy import deepcopy
import unittest

from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_data import session_review
from gbop_voice_web.variant_explanation import variant_clause
from gbop_voice_web.voice_payload import voice_tool_payload
from test_shift_review import fixture
from test_variant_explanation import base, review


FULL_NAME = 'V2 — Pattern Trader’s Kryptonite'
VARIANT_NAME = 'Pattern Trader’s Kryptonite'


class V2DisplayNameTests(unittest.TestCase):
    def test_completed_label_uses_full_name_without_changing_mechanics(self):
        data = base()
        data[12]['high'] = 112
        data[14]['low'] = 89
        row = review(data, '10:00')
        evidence = row['variant_evidence']
        self.assertEqual(evidence['status'], 'distribution_observed')
        self.assertEqual([(v['code'], v['name']) for v in evidence['labels']],
                         [('V2', VARIANT_NAME)])
        self.assertEqual(evidence['candles_through_distribution'], 2)
        self.assertFalse(evidence['entry_confirmed'])
        self.assertIn(FULL_NAME + ' because', variant_clause(evidence))

    def test_pending_v2_uses_full_name_and_stays_conditional(self):
        for target_observed in (False, True):
            with self.subTest(target_observed=target_observed):
                data = base()
                data[12]['high'] = 112
                if target_observed:
                    data[14]['low'] = 89
                evidence = review(data, '09:30')['variant_evidence']
                self.assertEqual(evidence['labels'], [])
                explanation = evidence['explanation']
                self.assertEqual(explanation['status'], 'pending')
                candidate = next(v for v in explanation['candidates'] if v['code'] == 'V2')
                self.assertEqual(candidate['name'], VARIANT_NAME)
                text = variant_clause(evidence)
                self.assertIn(FULL_NAME, text)
                self.assertIn('pending because', text)
                self.assertNotIn('established at', text)

    def test_shift_text_and_voice_keep_exact_canonical_name(self):
        data = fixture()
        data[26]['high'] = 101
        source = session_review(data, '2026-10-02', 'day', 300)
        before = deepcopy(source)
        self.assertIn(FULL_NAME, source['shift_synopsis']['spoken_summary'])
        self.assertIn(FULL_NAME, source['shift_story']['recap']['selected_range_chapters'][1]['text'])
        voice = voice_tool_payload('review_market_session', {'ok': True, 'review': source})
        self.assertTrue(voice['ok'], voice)
        self.assertIn(FULL_NAME, voice['review']['shift_synopsis']['spoken_summary'])
        self.assertEqual(source, before)

    def test_rendering_legacy_short_name_does_not_mutate_saved_evidence(self):
        for key in ('labels', 'candidates'):
            with self.subTest(key=key):
                entry = {'code': 'V2', 'name': 'Kryptonite', 'requires': 'Complete H1 evidence.'}
                evidence = ({'labels': [entry]} if key == 'labels' else
                            {'explanation': {'candidates': [entry], 'reason': 'Candle 2 is forming'}})
                before = deepcopy(evidence)
                self.assertIn(FULL_NAME, variant_clause(evidence))
                self.assertEqual(evidence, before)

    def test_shared_canon_uses_the_complete_v2_name(self):
        self.assertIn(FULL_NAME + ': two candles; Candle 2 manipulates and distributes.',
                      CANONICAL_KNOWLEDGE)
        self.assertNotIn('V2 Kryptonite:', CANONICAL_KNOWLEDGE)


if __name__ == '__main__':
    unittest.main()
