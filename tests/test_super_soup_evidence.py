"""Synthetic owner-spec regressions; no market or member data is fabricated as fact."""
import unittest
from copy import deepcopy

from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp
from gbop_voice_web.super_soup_evidence import relation

START = parse_time('2026-10-02T09:00:00-04:00')
T = START + 3600


def bar(t, o=95, h=100, l=90, c=95):
    return dict(time=t, open=o, high=h, low=l, close=c)


def fixture(items, step=300):
    return [bar(t) for t in range(START, T, step)] + [bar(T + i * step, *v) for i, v in enumerate(items)]


def review(items, end=None, step=300):
    data = fixture(items, step)
    return crt_review(data, START, end or T + len(items) * step, 'H1', step)


def life(items):
    return review(items)['model1']['lifecycle'][0]


MODEL = (99, 103, 98, 102)
INSIDE = (102, 102.5, 101, 102)
CLEAN = (102, 104, 101, 102)
TARGET = (100, 101, 89, 94)


class SuperSoupEvidenceTests(unittest.TestCase):
    def test_identity_without_followup_stays_model1(self):
        out = review([MODEL])
        self.assertEqual(out['model1']['candles'][0]['identity'], 'Model 1 candle')
        self.assertEqual(out['model1']['lifecycle'][0]['super_soup']['structure_status'], 'not_observed_by_cutoff')
        self.assertEqual(out['model1']['csd_status'], 'not_assessed')
        self.assertFalse(out['entry_confirmed'])

    def test_identity_is_not_mutated_by_later_events(self):
        before = review([MODEL])['model1']['candles']
        after = review([MODEL, CLEAN, TARGET])['model1']['candles']
        self.assertEqual(before, after)

    def test_next_candle_is_described_not_assumed_to_be_soup(self):
        item = life([MODEL, INSIDE])
        self.assertEqual(item['next_candle']['relationship'], 'inside_bar')
        self.assertEqual(item['super_soup']['structure_status'], 'not_observed_by_cutoff')

    def test_clean_can_deliver(self):
        item = life([MODEL, CLEAN, TARGET])
        soup = item['super_soup']
        self.assertEqual(soup['structural_quality'], 'clean')
        self.assertEqual(soup['parent_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(soup['event']['purge_form'], 'wick_only')
        self.assertEqual(item['csd']['evidence']['bar_close_ny'], stamp(T + 900))
        self.assertFalse(review([MODEL, CLEAN, TARGET])['entry_confirmed'])

    def test_clean_can_fail_without_relabelling_its_formation(self):
        item = life([MODEL, CLEAN, (102, 105, 102, 104)])
        self.assertEqual(item['super_soup']['structural_quality'], 'clean')
        self.assertEqual(item['super_soup']['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(item['model1_crt_invalidating_close']['bar_close_ny'], stamp(T + 900))

    def test_unclean_can_still_perform_parent_function(self):
        item = life([MODEL, (102, 105, 101, 104), TARGET])
        soup = item['super_soup']
        self.assertEqual(soup['structural_quality'], 'not_clean')
        self.assertEqual(soup['event']['purge_form'], 'body_purge')
        self.assertEqual(soup['parent_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(soup['variants'], [])

    def test_delayed_purge_with_one_inside_is_v4(self):
        soup = life([MODEL, INSIDE, CLEAN, TARGET])['super_soup']
        self.assertEqual(soup['structural_quality'], 'clean')
        self.assertEqual(soup['inside_bars_before_purge'], 1)
        self.assertIn('V4', [v['code'] for v in soup['variants']])

    def test_delayed_purge_with_multiple_inside_is_v5(self):
        soup = life([MODEL, INSIDE, INSIDE, CLEAN, TARGET])['super_soup']
        self.assertEqual(soup['inside_bars_before_purge'], 2)
        self.assertIn('V5', [v['code'] for v in soup['variants']])

    def test_local_target_is_not_parent_target(self):
        soup = life([MODEL, CLEAN, (102, 102, 98, 99)])['super_soup']
        self.assertEqual(soup['local_crt_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(soup['parent_function_outcome'], 'pending_at_cutoff')
        self.assertEqual(soup['local_crt_objectives']['opposing_liquidity']['level'], 98)
        self.assertEqual(soup['parent_range_objectives']['opposing_liquidity']['level'], 90)
        self.assertIn('V1', [v['code'] for v in soup['variants']])

    def test_v3_uses_nested_candle_count_not_outer_hour(self):
        soup = life([MODEL, CLEAN, INSIDE, (102, 102, 98, 99)])['super_soup']
        self.assertIn('V3', [v['code'] for v in soup['variants']])

    def test_no_soup_even_though_model1_delivers(self):
        item = life([MODEL, (102, 103, 96, 97), TARGET])
        self.assertEqual(item['super_soup']['structure_status'], 'not_observed_before_csd')
        self.assertEqual(item['model1_parent_objectives']['opposing_liquidity']['status'], 'observed_after_purge')

    def test_sweep_after_csd_is_not_new_preconfirmation_super_soup(self):
        soup = life([MODEL, (102, 103, 96, 97), CLEAN])['super_soup']
        self.assertEqual(soup['structure_status'], 'not_observed_before_csd')
        self.assertIsNone(soup['event'])

    def test_soup_and_csd_may_share_closing_candle(self):
        item = life([MODEL, (102, 104, 98, 98.5)])
        self.assertEqual(item['super_soup']['structural_quality'], 'clean')
        self.assertTrue(item['super_soup']['csd_same_assigned_close'])
        self.assertEqual(item['csd']['body_reference_level'], 99)
        self.assertEqual(item['csd']['status'], 'confirmed')

    def test_csd_reference_is_body_not_wick_extreme(self):
        item = life([MODEL, (102, 103, 98.25, 98.5)])
        self.assertEqual(item['csd']['status'], 'confirmed')
        self.assertGreater(item['csd']['evidence']['close'], 98)

    def test_boundary_touch_is_not_purge(self):
        soup = life([MODEL, (102, 103, 101, 102)])['super_soup']
        self.assertIsNone(soup['event'])

    def test_missing_next_candle_does_not_become_delayed_soup(self):
        data = fixture([MODEL, INSIDE, CLEAN, TARGET])
        data = [b for b in data if b['time'] != T + 300]
        out = crt_review(data, START, T + 1200, 'H1', 300)['model1']['lifecycle'][0]
        self.assertFalse(out['source_coverage_complete'])
        self.assertEqual(out['super_soup']['structure_status'], 'unverified_missing_candles')
        self.assertIsNone(out['super_soup']['event'])

    def test_later_missing_data_preserves_clean_observation(self):
        out = review([MODEL, CLEAN], T + 1200)['model1']['lifecycle'][0]
        self.assertEqual(out['super_soup']['structural_quality'], 'clean')
        self.assertEqual(out['super_soup']['parent_function_outcome'], 'unverified')

    def test_forming_assigned_candle_is_not_clean_or_failed(self):
        data = fixture([MODEL])
        # Keep initial M5 as five complete M1 bars, with correct aggregate open/close.
        anchor = [bar(t) for t in range(START, T, 60)]
        model = [bar(T, *MODEL)] + [bar(T + i * 60, 102, 103, 101, 102) for i in range(1, 5)]
        tail = [bar(T + 300, *CLEAN), bar(T + 360, *INSIDE)]
        out = crt_review(anchor + model + tail, START, T + 420, 'H1', 60)['model1']['lifecycle'][0]
        self.assertTrue(out['assigned_candle_forming'])
        self.assertEqual(out['super_soup']['structure_status'], 'pending_assigned_close')
        self.assertIsNone(out['super_soup']['event'])

    def test_same_source_bar_touch_is_not_claimed_as_ordered_delivery(self):
        soup = life([MODEL, (102, 104, 89, 99)])['super_soup']
        self.assertEqual(soup['parent_range_objectives']['opposing_liquidity']['status'], 'same_purge_bar_order_unresolved')
        self.assertEqual(soup['parent_function_outcome'], 'unverified')

    def test_prior_adverse_close_cannot_be_erased_by_later_return(self):
        # An adverse body close already re-purges: this first event stays not-clean.
        soup = life([MODEL, (102, 105, 101, 104), CLEAN, TARGET])['super_soup']
        self.assertEqual(soup['structural_quality'], 'not_clean')

    def test_body_reference_retest_time_is_separate(self):
        item = life([MODEL, CLEAN, (102, 103, 98, 98.5), (98.5, 100, 98, 99)])
        self.assertEqual(item['body_reference_retest_after_csd']['bar_open_ny'], stamp(T + 900))

    def test_bullish_is_symmetric(self):
        items = [MODEL, INSIDE, CLEAN, TARGET]
        mirror = [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in items]
        # Reflect anchor as well (100..110 instead of 90..100).
        data = [bar(t, 105, 110, 100, 105) for t in range(START, T, 300)]
        data += [bar(T + i * 300, *v) for i, v in enumerate(mirror)]
        out = crt_review(data, START, T + 1200, 'H1', 300)['model1']['lifecycle'][0]
        self.assertEqual(out['super_soup']['structural_quality'], 'clean')
        self.assertEqual(out['super_soup']['parent_function_outcome'], 'opposing_liquidity_delivered')
        self.assertIn('V4', [v['code'] for v in out['super_soup']['variants']])

    def test_wick_and_body_purges_have_assigned_timeframe_times(self):
        out = review([(99, 103, 98, 99), MODEL])['model1']
        self.assertEqual(out['assigned_range_purges'][0]['purge_form'], 'wick_only')
        self.assertEqual(out['assigned_range_purges'][1]['purge_form'], 'body_purge')
        self.assertEqual(out['assigned_range_purges'][0]['timeframe'], 'M5')
        self.assertEqual(out['assigned_range_purges'][1]['bar_open_ny'], stamp(T + 300))

    def test_unknown_assigned_mapping_is_not_guessed(self):
        data = [bar(t) for t in range(START, T + 3600, 300)]
        out = crt_review(data, START, T + 3600, 'H2', 300)
        self.assertEqual(out['model1']['status'], 'assigned_timeframe_required')
        self.assertNotIn('lifecycle', out['model1'])

    def test_retest_is_not_inferred_from_close_only(self):
        item = life([MODEL, CLEAN, (102, 103, 98, 98.5), (97, 98, 94, 95)])
        self.assertIsNone(item['body_reference_retest_after_csd'])

    def test_clean_v6_needs_resoup_of_manipulation_extreme(self):
        soup = life([MODEL, CLEAN, (102, 105, 101, 102), (102, 103, 98, 99)])['super_soup']
        self.assertIn('V6', [v['code'] for v in soup['variants']])
        repeated_boundary = life([MODEL, CLEAN, (102, 103.5, 101, 102), (102, 103, 98, 99)])['super_soup']
        self.assertNotIn('V6', [v['code'] for v in repeated_boundary['variants']])

    def test_lifecycle_payload_is_paginated_without_altering_identity(self):
        out = review([MODEL] + [INSIDE] * 10 + [(102, 103, 99, 99)] + [INSIDE] * 4)['model1']['lifecycle'][0]
        self.assertEqual(len(out['following_candles']), 12)
        self.assertIsNotNone(out['next_detail_start_ny'])

    def test_v2_requires_ordered_finer_candles_within_manipulation(self):
        anchor = [bar(t) for t in range(START, T, 60)]
        model = [bar(T, *MODEL)] + [bar(T + i * 60, 102, 103, 101, 102) for i in range(1, 5)]
        following = [(102, 104, 102, 103), (103, 103, 101, 102),
                     (102, 102, 98, 99), (99, 101, 99, 100), (100, 102, 100, 102)]
        data = anchor + model + [bar(T + 300 + i * 60, *v) for i, v in enumerate(following)]
        soup = crt_review(data, START, T + 600, 'H1', 60)['model1']['lifecycle'][0]['super_soup']
        self.assertIn('V2', [v['code'] for v in soup['variants']])
        self.assertEqual(soup['local_crt_outcome'], 'opposing_liquidity_delivered')

    def test_later_parent_invalidation_cannot_erase_earlier_delivery(self):
        items = [MODEL, CLEAN, TARGET] + [(102, 103, 101, 102)] * 9
        item = life(items)
        self.assertEqual(item['parent_invalidated_at_ny'], stamp(T + 3600))
        self.assertEqual(item['super_soup']['parent_function_outcome'], 'opposing_liquidity_delivered')

    def test_parent_touch_after_parent_invalidation_is_not_valid_delivery(self):
        items = [MODEL, CLEAN] + [(102, 103, 101, 102)] * 10 + [TARGET]
        item = life(items)
        self.assertEqual(item['super_soup']['parent_function_outcome'], 'failed_before_objectives')
        self.assertEqual(item['window_end_ny'], stamp(T + 3600))

    def test_pre_model_gap_keeps_parent_validity_unverified(self):
        data = fixture([INSIDE, MODEL, CLEAN, TARGET])
        data = [b for b in data if b['time'] != T]
        item = crt_review(data, START, T + 1200, 'H1', 300)['model1']['lifecycle'][0]
        self.assertFalse(item['parent_validity_verified_at_model1'])
        self.assertEqual(item['super_soup']['structural_quality'], 'clean')
        self.assertEqual(item['super_soup']['parent_function_outcome'], 'unverified_parent_validity')

    def test_external_purge_classification_does_not_mutate_prices(self):
        source = {'open': 102, 'high': 104, 'low': 101, 'close': 102}
        old = deepcopy(source)
        result = relation(source, {'high': 103, 'low': 98}, True)
        self.assertEqual(source, old)
        self.assertEqual(result['purge_form'], 'wick_only')

    def test_no_following_closed_bars_never_implies_execution(self):
        item = life([MODEL])
        self.assertEqual(item['execution_status'], 'not_assessed')


if __name__ == '__main__':
    unittest.main()
