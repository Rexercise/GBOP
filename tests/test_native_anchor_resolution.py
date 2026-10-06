"""Synthetic native OHLC authority stays separate from missing source bars."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_evidence import crt_review, h1_anchor, parse_time, summarize
from gbop_voice_web.market_data import attach_lifecycle, session_review
from gbop_voice_web.shift_review import review_shift


START = parse_time('2026-06-11T07:00:00-04:00')


def bars():
    return [dict(time=START+i*60, open=100, high=110 if i < 60 else 108,
                 low=90 if i < 60 else 92, close=100) for i in range(300)]


def native(rows, start=START, **prices):
    candle = summarize(rows, start, start+3600, 60)
    return dict(time=start, **{k: prices.get(k, candle[k]) for k in ('open', 'high', 'low', 'close')},
                provenance=dict(source='MT5', timeframe='H1', method='copy_rates_from_pos',
                                asset='NAS100', symbol='TEST_NAS', captured_at=START+18000))


class NativeAnchorResolutionTests(unittest.TestCase):
    def test_recovered_bounds_preserve_missing_counts_and_no_extreme_times(self):
        rows = bars(); higher = native(rows, high=112, low=88)
        del rows[17:29]
        before = deepcopy(rows)
        result = h1_anchor(rows, START, 60, [higher], START+18000)
        self.assertEqual(rows, before)
        self.assertTrue(result['complete']); self.assertTrue(result['ohlc_complete'])
        self.assertFalse(result['source_coverage_complete'])
        self.assertEqual((result['bar_count'], result['missing_bar_count']), (48, 12))
        self.assertEqual((result['high'], result['low']), (112, 88))
        self.assertEqual(result['ohlc_basis'], 'native_broker_H1')
        self.assertNotIn('high_first_seen', result); self.assertNotIn('low_first_seen', result)
        self.assertEqual(result['missing_intervals'][0]['bar_count'], 12)

    def test_missing_native_does_not_repair_source_gap(self):
        rows = bars(); del rows[15]
        self.assertFalse(h1_anchor(rows, START, 60)['complete'])

    def test_native_only_anchor_does_not_fabricate_source_rows(self):
        rows = bars(); higher = native(rows); rows = rows[60:]
        result = h1_anchor(rows, START, 60, [higher], START+18000)
        self.assertTrue(result['complete'])
        self.assertEqual((result['bar_count'], result['missing_bar_count']), (0, 60))
        self.assertEqual(len(rows), 240)

    def test_invalid_provenance_cannot_recover_anchor(self):
        rows = bars(); higher = native(rows); del rows[12]
        for key, value in [('source', 'other_feed'), ('timeframe', 'M5'),
                           ('method', 'aggregate'), ('captured_at', START+3599),
                           ('captured_at', float('nan')), ('asset', ''), ('symbol', '')]:
            with self.subTest(key=key, value=value):
                item = deepcopy(higher); item['provenance'][key] = value
                self.assertFalse(h1_anchor(rows, START, 60, [item], START+18000)['complete'])

    def test_cutoff_excludes_forming_or_future_native_hour(self):
        rows = bars(); higher = native(rows); del rows[12]
        self.assertFalse(h1_anchor(rows, START, 60, [higher], START+3599)['complete'])
        self.assertFalse(h1_anchor(bars(), START, 60, [higher], START+3599)['complete'])

    def test_complete_source_conflict_fails_closed(self):
        rows = bars(); higher = native(rows, high=111)
        result = h1_anchor(rows, START, 60, [higher], START+18000)
        self.assertFalse(result['complete']); self.assertTrue(result['source_coverage_complete'])
        self.assertEqual(result['native_h1_status'], 'conflicting_ohlc')
        review = crt_review(rows, START, START+18000, 'H1', 60, native_h1=[higher])
        self.assertEqual(review['events'], [])

    def test_partial_source_outside_native_bounds_fails_closed(self):
        rows = bars(); higher = native(rows, high=109); del rows[12]
        self.assertEqual(h1_anchor(rows, START, 60, [higher], START+18000)['native_h1_status'],
                         'conflicting_ohlc')

    def test_known_boundary_open_or_close_conflict_fails_closed(self):
        for key in ('open', 'close'):
            rows = bars(); higher = native(rows, **{key: 101}); del rows[12]
            self.assertFalse(h1_anchor(rows, START, 60, [higher], START+18000)['complete'])

    def test_ambiguous_native_symbol_or_prices_fail_closed(self):
        rows = bars(); first = native(rows); second = deepcopy(first); del rows[12]
        second['provenance']['symbol'] = 'OTHER_SYMBOL'
        self.assertFalse(h1_anchor(rows, START, 60, [first, second], START+18000)['complete'])

    def test_valid_complete_sources_keep_extreme_source_evidence(self):
        rows = bars(); higher = native(rows)
        result = h1_anchor(rows, START, 60, [higher], START+18000)
        self.assertTrue(result['complete']); self.assertTrue(result['source_coverage_complete'])
        self.assertIn('high_first_seen', result)

    def test_native_recovery_allows_v1_without_inventing_anchor_minutes(self):
        rows = bars(); higher = native(rows)
        rows[65].update(open=95, high=97, low=88, close=94)
        rows[125].update(open=100, high=111, low=99, close=105)
        rows[179].update(high=112, close=111)
        del rows[17:29]
        review = session_review(rows, '2026-06-11', 'day', 60, native_h1=[higher])
        young = next(o['evidence'] for o in review['observations'] if o['play'] == 'Young Lefty')
        self.assertEqual(young['observed_direction'], 'bullish')
        self.assertEqual([v['code'] for v in young['variant_evidence']['labels']], ['V1'])
        self.assertEqual(young['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(young['invalidated_at_ny'], '2026-06-11T10:00:00-04:00')
        self.assertFalse(young['anchor']['source_coverage_complete'])

    def test_missing_post_anchor_before_purge_blocks_direction(self):
        rows = bars(); higher = native(rows)
        rows[65].update(low=88); del rows[61]
        review = crt_review(rows, START, START+18000, 'H1', 60, native_h1=[higher])
        self.assertEqual(review['direction_verification'], 'first_observed_purge_only_incomplete_prefix')
        self.assertEqual(review['sweep_order'], 'unverified_incomplete_source_prefix')
        result = attach_lifecycle(review, rows, START+18000, 60)
        self.assertEqual(result['variant_evidence']['labels'], [])
        self.assertFalse(result['double_purge']['observed'])

    def test_missing_post_anchor_before_target_blocks_completed_delivery(self):
        rows = bars(); higher = native(rows)
        rows[65].update(low=88); rows[125].update(high=111); del rows[90]
        review = attach_lifecycle(crt_review(rows, START, START+18000, 'H1', 60,
                                             native_h1=[higher]), rows, START+18000, 60)
        self.assertNotEqual(review['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(review['variant_evidence']['labels'], [])
        self.assertFalse(review['double_purge']['observed'])

    def test_native_invalidation_matches_progression_and_bounds_lifecycle(self):
        rows = bars()
        # 08:00 anchor is 92..108. Native 09:00 closes above it despite one gap.
        rows[179].update(high=112, close=111)
        higher = native(rows, START+7200)
        del rows[121]
        story = review_shift(rows, '2026-06-11', 'day', 60, native_h1=[higher])
        first = story['ranges'][0]
        self.assertEqual(first['invalidated_at_ny'], '2026-06-11T10:00:00-04:00')
        self.assertEqual(story['range_transitions'][0]['confirmed_at_ny'], first['invalidated_at_ny'])
        review = attach_lifecycle(crt_review(rows, START+3600, START+18000, 'H1', 60,
                                             native_h1=[higher]), rows, START+18000, 60)
        self.assertEqual(review['candle_lifecycle']['window_end_ny'], first['invalidated_at_ny'])
        self.assertFalse(review['range_still_valid_in_available_closes'])

    def test_following_native_source_conflict_fences_later_delivery(self):
        rows = bars()
        rows[60].update(high=112)
        rows[181].update(low=89)
        higher = native(rows, START+7200, high=112, close=111)
        review = attach_lifecycle(crt_review(rows, START, START+18000, 'H1', 60,
                                             native_h1=[higher]), rows, START+18000, 60)
        self.assertEqual(review['validity_evidence_through_ny'], '2026-06-11T09:00:00-04:00')
        self.assertEqual(review['status'], 'unverified_conflicting_hourly_evidence')
        self.assertIsNone(review['range_still_valid_in_available_closes'])
        self.assertNotEqual(review['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(review['variant_evidence']['labels'], [])
        self.assertEqual(review['candle_lifecycle']['window_end_ny'], '2026-06-11T09:00:00-04:00')
        self.assertFalse(any(e['kind'] == 'opposing_liquidity_observed' for e in review['events']))

    def test_shift_variant_and_sweep_details_stop_at_conflicting_hour(self):
        rows = bars()
        for i in range(120, 180):
            rows[i].update(open=106, high=112, low=103, close=106)
        for i in range(180, 240):
            rows[i].update(open=106, high=113, low=103, close=106)
        higher = native(rows, START+10800, close=111)
        story = review_shift(rows, '2026-06-11', 'day', 60, native_h1=[higher])
        first = story['ranges'][0]
        self.assertEqual(first['validity_evidence_through_ny'], '2026-06-11T10:00:00-04:00')
        self.assertNotIn('V6', [v['code'] for v in first['variant_evidence']['labels']])
        self.assertNotIn('resoup_hour_ny', first['variant_evidence'])
        self.assertEqual(first['sweep_detail'][0]['excursions_in_available_bars'], 60)

    def test_native_wall_hour_confirmation_is_not_attached_to_custom_h1_grid(self):
        rows = bars()
        rows[95].update(high=112)
        rows[125].update(low=88)
        higher = native(rows, START+7200)
        result = crt_review(rows, START+1800, START+18000, 'H1', 60, native_h1=[higher])
        self.assertTrue(result['anchor']['complete'])
        self.assertNotIn('opposing_purge_hourly_candle', result)


if __name__ == '__main__':
    unittest.main()
