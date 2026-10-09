"""Synthetic coverage-qualified negative wording; no generated answer is assumed."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.market_data import session_review
from gbop_voice_web.multi_market_context import ContextBank, _summary, CONTRACT
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from test_young_lefty_coverage import source, DAY
from test_later_shift_ranges import fixture as delivered_fixture, DAY as DELIVERED_DAY
from test_shift_double_purge_voice import result as double_result


class NegativeClaimScopeTests(unittest.TestCase):
    def incomplete_seven(self):
        bars = source()
        del bars[17:23]
        return session_review(bars, DAY, 'day', 60)

    def guard(self, value):
        self.assertEqual(value['shift_wide_absence'], 'not_assessed')
        self.assertIn('verified named range and cutoff', value['instruction'])
        self.assertIn('never from an objective summary', value['instruction'])

    def summary(self, review):
        scope = {'asset': 'NAS100', 'date_ny': DAY, 'shift': 'day',
                 'anchor_start_ny': DAY + 'T08:00:00-04:00', 'anchor_timeframe': 'H1',
                 'through_ny': DAY + 'T12:00:00-04:00'}
        record = ContextBank('synthetic').record('review_market_session', scope,
            {'ok': True, 'asset': 'NAS100', 'symbol': 'SYNTHETIC', 'review': review})
        return _summary(record)

    def test_full_session_and_unknown_seven_do_not_certify_whole_shift_absence(self):
        synopsis = self.incomplete_seven()['shift_synopsis']
        self.assertTrue(synopsis['coverage_complete'])
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.guard(synopsis['negative_claim_guard'])
        self.assertEqual(synopsis['negative_claim_guard']['unverified_contexts'], ['Young Lefty'])

    def test_voice_and_compacted_comparison_preserve_guard(self):
        review = self.incomplete_seven()
        before = deepcopy(review)
        wire = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100', 'review': review})
        self.guard(wire['review']['shift_synopsis']['negative_claim_guard'])
        self.assertLess(len(json.dumps(wire, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)
        # Force the normal comparison-summary fallback without private data.
        review['shift_synopsis']['detail_padding'] = 'x' * 9000
        compact = self.summary(review)
        self.assertIn('evidence_note', compact)
        self.guard(compact['negative_claim_guard'])
        self.assertEqual(compact['negative_claim_guard']['unverified_contexts'], ['Young Lefty'])
        review['shift_synopsis'].pop('detail_padding')
        self.assertEqual(review, before)

    def test_legacy_prepared_synopsis_missing_guard_is_also_bounded(self):
        review = self.incomplete_seven()
        review['shift_synopsis'].pop('negative_claim_guard')
        self.guard(self.summary(review)['negative_claim_guard'])

    def test_no_range_evidence_also_blocks_whole_shift_absence(self):
        synopsis = build_shift_synopsis({'shift_story': {'ranges': []}})
        self.guard(synopsis['negative_claim_guard'])
        self.assertEqual(synopsis['ranges'], [])

    def test_positive_delivery_survives_an_unknown_independent_context(self):
        bars = delivered_fixture()
        del bars[17:23]
        synopsis = session_review(bars, DELIVERED_DAY, 'day', 60)['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertEqual(synopsis['ranges'][0]['outcome'], 'opposing_liquidity_delivered')
        self.guard(synopsis['negative_claim_guard'])

    def test_complete_contexts_need_no_extra_gap_guard_payload(self):
        synopsis = session_review(source(), DAY, 'day', 60)['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'absent')
        self.assertNotIn('negative_claim_guard', synopsis)

    def test_double_purge_and_original_delivery_remain_independent_facts(self):
        result = double_result()
        wire = voice_tool_payload('review_market_session', result)
        from test_chronological_transport import expand
        synopsis = expand(wire)['review']['shift_synopsis']
        self.assertTrue(synopsis['ranges'][0]['double_purge']['observed'])
        self.assertEqual(synopsis['ranges'][0]['outcome'], 'opposing_liquidity_delivered')
        self.assertIn('assess double purges separately', CONTRACT)


if __name__ == '__main__':
    unittest.main()
