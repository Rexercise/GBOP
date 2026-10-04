"""Independent original/reversal outcomes use one range and closed evidence."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.candle_lifecycle import lifecycle_review
from gbop_voice_web.double_purge import double_purge_evidence
from gbop_voice_web.shift_narrative import attach_directional_outcome


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


def t(clock):
    return parse_time(ny(clock))


def fixture(end='10:15', reverse=False):
    bars = [dict(time=time, open=100, high=120, low=80, close=100)
            for time in range(t('09:00'), t('10:00'), 60)]
    bars += [dict(time=time, open=90, high=95, low=85, close=90)
             for time in range(t('10:00'), t(end), 60)]
    def change(clock, **prices):
        next(b for b in bars if b['time'] == t(clock)).update(prices)
    change('10:00', open=110, high=125, low=105, close=118)
    change('10:05', open=90, high=92, low=75, close=78)
    change('10:06', open=78, high=92, low=76, close=90)
    change('10:08', open=94, high=99, low=90, close=95)
    if reverse:
        bars = [{**b, 'open': 200-b['open'], 'high': 200-b['low'],
                 'low': 200-b['high'], 'close': 200-b['close']} for b in bars]
    return bars


def review(bars, end='10:15', step=60):
    result = crt_review(bars, t('09:00'), t(end), 'H1', step)
    invalid = result.get('invalidated_at_ny')
    result['candle_lifecycle'] = lifecycle_review(bars, result['anchor'], 'M5', t(end), step,
                                                parse_time(invalid) if invalid else None)
    attach_directional_outcome(result, bars, t(end), step)
    return result


class DoublePurgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json'
        retained = json.loads(path.read_text())
        cls.retained = [dict(zip(('time', 'open', 'high', 'low', 'close'), row))
                        for item in retained['instruments'] if item['asset'] == 'NAS100'
                        for row in item['candles']]

    def assess(self, bars, end='10:15', reviewed=None, step=60):
        return double_purge_evidence(reviewed or review(bars, end, step), bars, t(end), step)

    def test_missing_prior_close_cannot_verify_original_completion(self):
        bars = [dict(time=time, open=100, high=120, low=80, close=100)
                for time in range(t('09:00'), t('10:00'), 60)]
        bars += [dict(time=time, open=110, high=115, low=105, close=110)
                 for time in range(t('10:00'), t('11:06'), 60) if time != t('10:59')]
        next(b for b in bars if b['time'] == t('10:00')).update(high=125, close=118)
        next(b for b in bars if b['time'] == t('11:02')).update(open=90, high=92, low=75, close=78)
        next(b for b in bars if b['time'] == t('11:03')).update(open=78, high=92, low=76, close=90)
        result = self.assess(bars, '11:06')
        self.assertFalse(result['original_completion_preserved'])
        self.assertEqual(result['original_outcome']['status'], 'unverified')
        self.assertIn('unverified', result['original_outcome']['spoken_summary'])
        self.assertFalse(result['observed'])
        self.assertEqual(result['original_outcome']['opposing_liquidity']['status'],
                         'unresolved_touch_validity_incomplete_coverage')

    def test_retained_same_range_chronology_and_two_independent_outcomes(self):
        reviewed = review(self.retained, '12:00')
        before = deepcopy((reviewed, self.retained))
        out = self.assess(self.retained, '12:00', reviewed)
        self.assertEqual(out['status'], 'observed')
        self.assertEqual(out['range_start_ny'], ny('09:00'))
        self.assertEqual(out['original_outcome']['status'], 'opposing_liquidity_delivered')
        seq = out['sequence']
        self.assertEqual(seq['first_purge']['bar_open_ny'], ny('10:02'))
        self.assertEqual(seq['opposing_delivery']['bar_open_ny'], ny('11:12'))
        self.assertEqual(seq['opposing_purge']['bar_open_ny'], ny('11:12'))
        self.assertEqual(seq['source_return_inside']['bar_open_ny'], ny('11:13'))
        self.assertEqual(seq['source_return_inside']['known_at_ny'], ny('11:14'))
        self.assertEqual(seq['assigned_return_inside']['bar_open_ny'], ny('11:10'))
        self.assertEqual(seq['assigned_return_inside']['known_at_ny'], ny('11:15'))
        wick, body = out['opposite_identities'][:2]
        self.assertEqual((wick['identity'], wick['bar_open_ny'], wick['known_at_ny']),
                         ('Turtle Wick Soup', ny('11:10'), ny('11:15')))
        self.assertEqual((body['identity'], body['bar_open_ny'], body['known_at_ny']),
                         ('Model 1 candle', ny('11:15'), ny('11:20')))
        thesis = out['reversal_thesis']
        self.assertEqual((thesis['direction'], thesis['objective_side'], thesis['objective_level']),
                         ('bullish', 'buy', 30995.59))
        self.assertEqual(thesis['midpoint_role'], 'halfway_progress_not_full_objective')
        self.assertEqual(thesis['status'], 'pending_at_review_cutoff')
        self.assertEqual([v['code'] for v in reviewed['variant_evidence']['labels']], ['V1'])
        self.assertNotIn('variant_evidence', thesis)
        self.assertEqual((reviewed, self.retained), before)

    def test_retained_rebound_uses_post_return_source_high_not_earlier_m5_high(self):
        out = self.assess(self.retained, '12:00')
        mid = out['reversal_thesis']['objectives']['midpoint']
        self.assertEqual(mid['closest_observed_price'], 30879.33)
        self.assertEqual(mid['closest_source_interval']['bar_open_ny'], ny('11:52'))
        self.assertEqual(mid['distance_price_points'], 33.2)
        metric = mid['approach']
        self.assertEqual(metric['full_range_reference']['denominator_price_points'], 166.12)
        self.assertAlmostEqual(metric['full_range_reference']['gap_percent'], 19.985552612569226)
        self.assertEqual(metric['boundary_to_target_reference']['denominator_price_points'], 83.06)
        self.assertAlmostEqual(metric['boundary_to_target_reference']['progress_percent'], 60.02889477486155)
        self.assertIsNone(metric['numeric_inducement_threshold'])
        self.assertEqual(metric['inducement_classification'], 'requires_qualitative_context')
        full = out['reversal_thesis']['objectives']['original_side']
        self.assertEqual(full['distance_price_points'], 116.26)

    def test_stale_full_review_and_future_bars_cannot_leak_past_cutoff(self):
        full = review(self.retained, '12:00')
        no_purge = self.assess(self.retained, '11:12', full)
        self.assertFalse(no_purge['observed'])
        self.assertNotEqual(no_purge['original_outcome']['status'], 'opposing_liquidity_delivered')
        no_return = self.assess(self.retained, '11:13', full)
        self.assertEqual(no_return['status'], 'opposing_purge_observed_return_unverified')
        self.assertIsNone(no_return['sequence']['source_return_inside'])
        self.assertIsNone(no_return['sequence']['assigned_return_inside'])
        source = self.assess(self.retained, '11:14', full)
        self.assertTrue(source['observed'])
        self.assertIsNone(source['sequence']['assigned_return_inside'])
        self.assertEqual(source['opposite_identities'], [])
        wick = self.assess(self.retained, '11:15', full)
        self.assertEqual(len(wick['opposite_identities']), 1)
        self.assertEqual(len(self.assess(self.retained, '11:19', full)['opposite_identities']), 1)
        self.assertEqual(len(self.assess(self.retained, '11:20', full)['opposite_identities']), 2)
        self.assertNotIn('V1', json.dumps(source))

    def test_mirrored_sequences_target_original_side_and_equal_midpoint_is_only_halfway(self):
        for reverse, expected_side, target, midpoint_extreme in [
                (False, 'buy', 120, 'high'), (True, 'sell', 80, 'low')]:
            with self.subTest(reverse=reverse):
                bars = fixture(reverse=reverse)
                next(b for b in bars if b['time'] == t('10:10'))[midpoint_extreme] = 100
                out = self.assess(bars)
                thesis = out['reversal_thesis']
                self.assertTrue(out['observed'])
                self.assertEqual(thesis['objective_side'], expected_side)
                self.assertEqual(thesis['objective_level'], target)
                self.assertEqual(thesis['status'], 'midpoint_only')
                self.assertEqual(thesis['objectives']['midpoint']['distance_price_points'], 0)
                self.assertNotEqual(thesis['objectives']['original_side']['status'], 'observed_after_return')
                next(b for b in bars if b['time'] == t('10:11'))[midpoint_extreme] = target
                complete = self.assess(bars)['reversal_thesis']
                self.assertEqual(complete['status'], 'original_side_delivered')
                self.assertEqual(complete['objectives']['original_side']['distance_price_points'], 0)

    def test_opposite_model1_without_return_is_not_double_purge(self):
        bars = fixture()
        for b in bars:
            if b['time'] >= t('10:05'):
                b.update(open=78, high=79, low=70, close=75)
        next(b for b in bars if b['time'] == t('10:05'))['open'] = 90
        out = self.assess(bars)
        self.assertTrue(any(f['identity'] == 'Model 1 candle' for f in out['opposite_identities']))
        self.assertEqual(out['status'], 'opposing_purge_observed_return_unverified')
        self.assertFalse(out['observed'])
        self.assertEqual(out['reversal_thesis']['status'], 'not_assessed_no_valid_double_purge')

    def test_touch_of_opposing_boundary_is_delivery_but_not_second_purge(self):
        bars = fixture()
        for b in bars:
            if b['time'] >= t('10:05'):
                b['low'] = max(80, b['low'])
                b['open'] = max(80, b['open'])
                b['close'] = max(80, b['close'])
        out = self.assess(bars)
        self.assertEqual(out['original_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertFalse(out['observed'])
        self.assertEqual(out['status'], 'not_observed_no_opposing_purge')

    def test_same_source_bar_double_sweep_cannot_determine_original_direction(self):
        bars = fixture()
        next(b for b in bars if b['time'] == t('10:00'))['low'] = 75
        out = self.assess(bars)
        self.assertFalse(out['observed'])
        self.assertEqual(out['status'], 'unverified_direction')

    def test_missing_or_duplicate_sequence_bars_keep_return_observed_but_unverified(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                bars = fixture()
                selected = next(b for b in bars if b['time'] == t('10:03'))
                bars.append(dict(selected)) if duplicate else bars.remove(selected)
                out = self.assess(bars)
                self.assertEqual(out['status'], 'unverified_incomplete_sequence_coverage')
                self.assertIsNotNone(out['sequence']['source_return_inside'])
                self.assertFalse(out['observed'])

    def test_missing_post_return_bar_prevents_verified_closest_or_delivery(self):
        bars = fixture()
        bars = [b for b in bars if b['time'] != t('10:07')]
        out = self.assess(bars)
        self.assertTrue(out['observed'])
        mid = out['reversal_thesis']['objectives']['midpoint']
        self.assertEqual(mid['observed_distance_price_points'], 1)
        self.assertIsNone(mid['distance_price_points'])
        next(b for b in bars if b['time'] == t('10:10'))['high'] = 120
        full = self.assess(bars)['reversal_thesis']['objectives']['original_side']
        self.assertEqual(full['status'], 'observed_touch_validity_unverified')
        self.assertIsNone(full['distance_price_points'])

    def test_later_gap_does_not_erase_verified_original_or_reverse_delivery(self):
        bars = fixture()
        next(b for b in bars if b['time'] == t('10:08'))['high'] = 120
        bars = [b for b in bars if b['time'] != t('10:12')]
        out = self.assess(bars)
        self.assertTrue(out['observed'])
        self.assertTrue(out['original_completion_preserved'])
        self.assertEqual(out['reversal_thesis']['status'], 'original_side_delivered')

    def test_original_completion_survives_reverse_failure_and_later_invalidation(self):
        bars = fixture('11:02')
        next(b for b in bars if b['time'] == t('10:59')).update(open=85, high=90, low=70, close=75)
        next(b for b in bars if b['time'] == t('11:00')).update(open=75, high=130, low=74, close=120)
        out = self.assess(bars, '11:02')
        self.assertEqual(out['original_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(out['range_invalidated_at_ny'], ny('11:00'))
        self.assertEqual(out['reversal_thesis']['status'], 'failed_before_objectives')
        self.assertEqual(out['reversal_thesis']['objectives']['midpoint']['distance_price_points'], 1)
        self.assertEqual(out['validity_cutoff_ny'], ny('11:00'))

    def test_same_invalidating_source_bar_target_is_unresolved_not_reverse_delivery(self):
        bars = fixture('11:02')
        next(b for b in bars if b['time'] == t('10:59')).update(open=90, high=125, low=70, close=75)
        out = self.assess(bars, '11:02')
        thesis = out['reversal_thesis']
        self.assertEqual(thesis['status'], 'unverified')
        full = thesis['objectives']['original_side']
        self.assertEqual(full['status'], 'unverified_boundary_bar_order')
        self.assertIsNone(full['distance_price_points'])
        self.assertEqual(full['boundary_observations'][-1]['reason'], 'same_invalidating_close_source_bar')
        self.assertTrue(out['original_completion_preserved'])

    def test_later_invalidation_does_not_erase_completed_reverse(self):
        bars = fixture('11:02')
        next(b for b in bars if b['time'] == t('10:10'))['high'] = 120
        next(b for b in bars if b['time'] == t('10:59')).update(open=90, high=95, low=70, close=75)
        out = self.assess(bars, '11:02')
        self.assertEqual(out['reversal_thesis']['status'], 'original_side_delivered')
        self.assertTrue(out['reversal_thesis']['earlier_delivery_preserved_after_invalidation'])
        self.assertTrue(out['original_completion_preserved'])

    def test_source_return_bar_extremum_does_not_establish_post_return_target(self):
        bars = fixture()
        next(b for b in bars if b['time'] == t('10:06'))['high'] = 120
        out = self.assess(bars)
        self.assertTrue(out['observed'])
        full = out['reversal_thesis']['objectives']['original_side']
        self.assertEqual(full['status'], 'unverified_boundary_bar_order')
        self.assertIsNone(full['evidence'])

    def test_other_range_opposite_identity_cannot_be_relabelled_same_range(self):
        bars = fixture()
        reviewed = review(bars)
        fake = deepcopy(reviewed['candle_lifecycle']['purge_candles'][-1])
        fake['range_start_ny'] = ny('08:00')
        fake['range_end_ny'] = ny('09:00')
        reviewed['candle_lifecycle']['purge_candles'] = [fake]
        reviewed['model1']['candles'] = []
        out = self.assess(bars, reviewed=reviewed)
        self.assertEqual(out['opposite_identities'], [])
        self.assertTrue(out['observed'])  # Source sequence still belongs to the selected range.

    def test_return_only_after_selected_range_invalidation_does_not_qualify(self):
        bars = fixture('11:02')
        for b in bars:
            if t('10:05') <= b['time'] < t('11:00'):
                b.update(open=78, high=79, low=70, close=75)
        next(b for b in bars if b['time'] == t('10:05'))['open'] = 90
        out = self.assess(bars, '11:02')
        self.assertEqual(out['range_invalidated_at_ny'], ny('11:00'))
        self.assertEqual(out['status'], 'not_established_before_range_invalidation')
        self.assertFalse(out['observed'])
        self.assertIsNone(out['sequence']['source_return_inside'])
        self.assertTrue(out['original_completion_preserved'])

    def test_missing_original_source_cannot_be_repaired_by_opposite_identity(self):
        bars = fixture()
        reviewed = review(bars)
        missing = [b for b in bars if b['time'] != t('10:00')]
        out = self.assess(missing, reviewed=reviewed)
        self.assertEqual(out['status'], 'unverified_initiating_source_evidence')
        self.assertFalse(out['observed'])

    def test_close_on_selected_boundary_is_inside_and_zero_width_anchor_is_unverified(self):
        bars = fixture()
        next(b for b in bars if b['time'] == t('10:06'))['close'] = 80
        out = self.assess(bars)
        self.assertTrue(out['observed'])
        self.assertEqual(out['sequence']['source_return_inside']['close'], 80)
        reviewed = review(bars)
        reviewed['anchor']['high'] = reviewed['anchor']['low']
        self.assertEqual(self.assess(bars, reviewed=reviewed)['status'], 'unverified_nonpositive_range')
        reviewed['anchor']['complete'] = False
        self.assertEqual(self.assess(bars, reviewed=reviewed)['status'], 'unverified_incomplete_anchor')
        self.assertEqual(double_purge_evidence(reviewed, bars, t('10:15'), 0)['status'],
                         'unverified_source_resolution')

    def test_forming_source_bar_is_excluded_and_coarse_precision_stays_coarse(self):
        bars = fixture()
        reviewed = review(bars)
        out = double_purge_evidence(reviewed, bars, t('10:06') + 30, 60)
        self.assertFalse(out['observed'])
        self.assertIsNone(out['sequence']['source_return_inside'])
        coarse = [dict(time=time, open=100, high=120, low=80, close=100)
                  for time in range(t('09:00'), t('10:00'), 300)]
        coarse += [dict(time=t('10:00'), open=110, high=125, low=105, close=110),
                   dict(time=t('10:05'), open=100, high=110, low=75, close=90),
                   dict(time=t('10:10'), open=90, high=99, low=85, close=95)]
        out = self.assess(coarse, step=300)
        self.assertTrue(out['observed'])
        returned = out['sequence']['source_return_inside']
        self.assertEqual(returned['timeframe'], 'M5')
        self.assertEqual(returned['precision_seconds'], 300)
        self.assertEqual(returned['known_at_ny'], ny('10:10'))
        self.assertFalse(returned['exact_tick_time_known'])


if __name__ == '__main__':
    unittest.main()
