"""Bound terminology detail while retaining exact lifecycle and scoped retrieval."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_detail import DETAIL_CHARACTER_BUDGET, _double_purge
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_retained_market_replays import RetainedMarketReplayTests
from test_voice_payload_budget import expanded

NY = lambda clock: '2026-10-02T' + clock + ':00-04:00'


class TerminologyVoiceDetailTests(unittest.TestCase):
    def setUp(self):
        self.replay = RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)

    def request(self, **overrides):
        args = {'asset': 'NAS100', 'anchor_start_ny': NY('09:00'),
                'anchor_timeframe': 'H1', 'through_ny': NY('12:00'), **overrides}
        context = MarketConversation()
        context.begin_turn()
        raw = context.run('review_market_crt', args,
            lambda tool, resolved: market.market_tool(self.replay.db, tool, resolved))
        saved = deepcopy(raw)
        wire = voice_tool_payload('review_market_crt', raw)
        self.assertTrue(wire['ok'], wire)
        self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), DETAIL_CHARACTER_BUDGET)
        self.assertEqual(raw, saved)
        return raw, expanded(wire, wire)

    def test_original_page_keeps_reversal_result_and_exact_opposite_retrieval(self):
        raw, page = self.request(detail_candle_start_ny=NY('10:00'))
        expected = compact_voice_tool_result('review_market_crt', raw)
        self.assertEqual(page['review']['candle_lifecycle']['purge_candles'][0],
                         expected['review']['candle_lifecycle']['purge_candles'][0])
        double = page['review']['double_purge']
        self.assertTrue(double['observed'])
        self.assertEqual(double['sequence']['source_return_inside']['known_at_ny'], NY('11:14'))
        self.assertEqual(double['sequence']['assigned_return_inside']['known_at_ny'], NY('11:15'))
        thesis = double['reversal_thesis']
        self.assertEqual(thesis['status'], 'pending_at_review_cutoff')
        self.assertEqual((thesis['objective_side'], thesis['objective_level']), ('buy', 30995.59))
        midpoint = thesis['objectives']['midpoint']
        self.assertEqual(midpoint['distance_price_points'], 33.2)
        self.assertEqual(midpoint['closest_source_interval']['bar_open_ny'], NY('11:52'))
        self.assertEqual(midpoint['approach']['full_range_reference']['denominator_price_points'], 166.12)
        self.assertEqual(midpoint['approach']['boundary_to_target_reference']['denominator_price_points'], 83.06)
        self.assertAlmostEqual(midpoint['approach']['full_range_reference']['gap_percent'], 19.985552612569226)
        self.assertEqual(midpoint['gtop_context']['basis'], 'explicit_owner_characterization')
        self.assertFalse(midpoint['boundary_order_verified'])
        self.assertIsNone(double['proximity_policy']['numeric_inducement_threshold'])
        request = double['double_purge_detail_request']
        self.assertEqual(request['tool'], 'review_market_crt')
        self.assertEqual(request['args']['detail_candle_start_ny'], NY('11:10'))
        self.assertEqual(request['args']['anchor_start_ny'], NY('09:00'))
        self.assertEqual(request['args']['through_ny'], NY('12:00'))
        self.assertIn('omitted', double['detail_omissions'])

    def test_reverse_wick_page_restores_full_sequence_and_boundary_rows(self):
        raw, page = self.request(detail_candle_start_ny=NY('11:10'))
        double = page['review']['double_purge']
        self.assertNotIn('detail_omissions', double)
        self.assertEqual(double['opposite_identities'], raw['review']['double_purge']['opposite_identities'])
        self.assertEqual(double['sequence'], raw['review']['double_purge']['sequence'])
        actual = double['reversal_thesis']['objectives']['midpoint']
        original = raw['review']['double_purge']['reversal_thesis']['objectives']['midpoint']
        self.assertEqual(actual['boundary_observations'], original['boundary_observations'])
        for name in ('full_range_reference', 'boundary_to_target_reference'):
            self.assertEqual(actual['approach'][name], original['approach'][name])

    def test_non_touch_keeps_named_target_percentage_and_explicit_owner_context(self):
        _, page = self.request(anchor_start_ny=NY('08:00'))
        target = page['review']['objective_approach']['objectives']['midpoint']
        self.assertEqual(target['distance_price_points'], 7.76)
        self.assertEqual(target['target_approach']['target']['kind'], 'midpoint')
        self.assertEqual(target['target_approach']['target']['anchor_start_ny'], NY('08:00'))
        self.assertAlmostEqual(target['target_approach']['full_range_reference']['gap_percent'],
                               2.9991497255932)
        self.assertEqual(target['gtop_context']['basis'], 'explicit_owner_characterization')
        self.assertIsNone(target['target_approach']['numeric_inducement_threshold'])

    def test_independently_unverified_original_never_references_qualified_original(self):
        original = {'direction': 'bearish', 'status': 'opposing_liquidity_delivered',
                    'midpoint': {'status': 'observed_after_purge'},
                    'opposing_liquidity': {'status': 'observed_after_purge'}}
        unverified = deepcopy(original)
        unverified['status'] = 'unresolved'
        unverified['opposing_liquidity'] = {
            'status': 'unresolved_touch_validity_incomplete_coverage',
            'coverage_through_touch': {'complete': False, 'missing_bar_count': 1}}
        result = _double_purge({'original_outcome': unverified}, original)['original_outcome']
        self.assertNotIn('same_evidence_as', result)
        self.assertEqual(result['status'], 'unresolved')
        self.assertFalse(result['opposing_liquidity']['coverage_through_touch']['complete'])


if __name__ == '__main__':
    unittest.main()
