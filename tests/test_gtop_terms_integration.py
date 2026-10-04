"""Retained market-only evidence through terminology and narrative transports."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import attach_lifecycle, market_tool
from gbop_voice_web.shift_synopsis import build_other_ranges
from gbop_voice_web.voice_payload import voice_tool_payload
import test_retained_market_replays as retained


class TermsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)

    def detail(self, hour='09:00', cutoff='12:00'):
        return market_tool(self.replay.db, 'review_market_crt', {
            'asset': 'NAS100', 'anchor_start_ny': retained.ny(hour),
            'anchor_timeframe': 'H1', 'through_ny': retained.ny(cutoff)})

    def test_default_starts_failed_bearish_then_preserves_nine_original_and_reversal(self):
        result = self.replay.tool('NAS100')
        before = deepcopy(result)
        payload = voice_tool_payload('review_market_session', result)
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(result, before)
        synopsis = payload['review']['shift_synopsis']
        text = synopsis['spoken_summary']
        self.assertIn('failed bearish', text.split('.')[0])
        self.assertIn('non-touch 50% approach was inducement', text)
        self.assertIn('double-purge bullish reversal remained pending', text)
        self.assertLess(len(text.split()), 160)
        self.assertLess(len(json.dumps(payload, separators=(',', ':'))), 9000)
        nine = next(r for r in synopsis['ranges'] if r['anchor_start_ny'] == retained.ny('09:00'))
        self.assertEqual(nine['outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(nine['direction'], 'bearish')
        self.assertEqual([v['code'] for v in nine['variant']['labels']], ['V1'])

    def test_exact_nine_detail_owner_qualitative_example_is_separate_from_numerical_rule(self):
        result = self.detail()
        self.assertTrue(result['ok'], result)
        reverse = result['review']['double_purge']['reversal_thesis']
        midpoint = reverse['objectives']['midpoint']
        self.assertEqual(midpoint['gtop_context']['basis'], 'explicit_owner_characterization')
        self.assertIsNone(midpoint['gtop_context']['general_numeric_threshold'])
        self.assertIsNone(midpoint['approach']['numeric_inducement_threshold'])
        self.assertEqual(midpoint['distance_price_points'], 33.2)
        self.assertEqual(reverse['objective_side'], 'buy')
        self.assertEqual(reverse['objective_level'], 30995.59)
        self.assertEqual(reverse['status'], 'pending_at_review_cutoff')

    def test_exact_eight_example_does_not_turn_failed_range_into_a_double_purge(self):
        result = self.detail('08:00')['review']
        midpoint = result['objective_approach']['objectives']['midpoint']
        self.assertEqual(midpoint['gtop_context']['basis'], 'explicit_owner_characterization')
        self.assertEqual(midpoint['distance_price_points'], 7.76)
        self.assertEqual(result['directional_outcome']['status'], 'failed_before_objectives')
        self.assertFalse(result['double_purge']['observed'])
        self.assertEqual(result['invalidated_at_ny'], retained.ny('10:00'))

    def test_active_range_continuation_keeps_return_and_original_side_objective(self):
        review = self.replay.tool('NAS100')['review']
        result = build_other_ranges(review, 'NAS100', continue_active=True)
        value = result['active_range_context']['double_purge']
        self.assertEqual(value['original_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(value['reversal_outcome'], 'pending_at_review_cutoff')
        self.assertEqual(value['full_objective_side'], 'buy')
        self.assertEqual(value['source_return_inside']['bar_open_ny'], retained.ny('11:13'))
        self.assertEqual(value['source_return_inside']['known_at_ny'], retained.ny('11:14'))
        self.assertEqual(value['assigned_return_inside']['bar_open_ny'], retained.ny('11:10'))
        self.assertEqual(value['assigned_return_inside']['known_at_ny'], retained.ny('11:15'))
        mid = value['objectives']['midpoint']
        self.assertEqual(mid['gtop_context']['basis'], 'explicit_owner_characterization')
        self.assertAlmostEqual(mid['full_range_reference']['gap_percent'], 19.9855526125692)
        self.assertAlmostEqual(mid['boundary_to_target_reference']['progress_percent'], 60.0288947748615)

    def test_zero_width_anchor_remains_unverified_without_breaking_review(self):
        start = parse_time(retained.ny('09:00'))
        bars = [dict(time=start + i * 60, open=100, high=100, low=100, close=100) for i in range(60)]
        bars.extend(dict(time=start + i * 60, open=100, high=101, low=100, close=100) for i in range(60, 65))
        end = start + 3900
        review = attach_lifecycle(crt_review(bars, start, end, 'H1', 60), bars, end, 60)
        self.assertEqual(review['objective_approach']['status'], 'unverified_nonpositive_range')
        self.assertEqual(review['double_purge']['status'], 'unverified_nonpositive_range')
        self.assertEqual(review['objective_approach']['objectives'], {})

    def test_fixed_cutoff_cannot_borrow_rebound_return_or_variant(self):
        bars = self.replay.bars['NAS100']
        for cutoff, observed in [('11:13', False), ('11:14', True)]:
            end = parse_time(retained.ny(cutoff))
            review = attach_lifecycle(crt_review(bars, parse_time(retained.ny('09:00')), end, 'H1', 60),
                                      bars, end, 60)
            value = review['double_purge']
            self.assertEqual(value['observed'], observed)
            self.assertEqual(value['original_outcome']['status'], 'opposing_liquidity_delivered')
            self.assertNotIn('11:52', json.dumps(value))
            self.assertNotIn('V1', json.dumps(value))
            self.assertNotIn('11:15:00', json.dumps(value))
            if observed:
                self.assertIsNone(value['sequence']['assigned_return_inside'])
                self.assertEqual(value['reversal_thesis']['objectives']['midpoint']['status'],
                                 'no_closed_post_return_bars')


if __name__ == '__main__':
    unittest.main()
