"""Price-point distances preserve direction, source windows and uncertainty."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.objective_approach import objective_approach


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


def bar(clock, low, high, close=None):
    return dict(time=parse_time(ny(clock)), open=(low + high) / 2,
                low=low, high=high, close=(low + high) / 2 if close is None else close)


def review(direction='bearish', invalid=None):
    return {'anchor': {'start_ny': ny('08:00'), 'end_ny': ny('09:00'),
            'timeframe': 'H1', 'complete': True, 'low': 80, 'high': 120, 'midpoint': 100},
            'observed_direction': direction, 'invalidated_at_ny': ny(invalid) if invalid else None,
            'events': [{'kind': ('buy' if direction == 'bearish' else 'sell') + '_side_purge',
                        'bar_open_ny': ny('09:00'), 'bar_close_ny': ny('09:01')}]}


class ObjectiveApproachTests(unittest.TestCase):
    def metric(self, r, bars, cutoff='09:04'):
        return objective_approach(r, bars, parse_time(ny(cutoff)), 60)

    def test_retained_nas_eight_midpoint_missed_by_7_76_price_points(self):
        fixture = json.loads((Path(__file__).parent /
            'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        candles = next(i['candles'] for i in fixture['instruments'] if i['asset'] == 'NAS100')
        bars = [dict(zip(('time', 'open', 'high', 'low', 'close'), c)) for c in candles]
        r = crt_review(bars, parse_time(ny('08:00')), parse_time(ny('12:00')), 'H1', 60)
        before = deepcopy((r, bars))
        out = self.metric(r, bars, '12:00')
        fact = out['objectives']['midpoint']
        self.assertEqual(fact['status'], 'closest_approach_verified')
        self.assertEqual(fact['level'], 30821.71)
        self.assertEqual(fact['closest_observed_price'], 30829.47)
        self.assertEqual(fact['distance_price_points'], 7.76)
        self.assertEqual(fact['closest_source_interval']['bar_open_ny'], ny('09:32'))
        self.assertEqual(out['window_start_ny'], ny('09:02'))
        self.assertEqual(out['validity_cutoff_ny'], ny('10:00'))
        self.assertEqual(out['coverage']['end_ny'], ny('09:59'))
        self.assertEqual(out['coverage']['bar_count'], 57)
        self.assertTrue(out['coverage']['complete'])
        self.assertEqual((r, bars), before)

    def test_bearish_and_bullish_use_directional_extrema(self):
        for direction, candles, price in [
                ('bearish', [bar('09:01', 110, 118), bar('09:02', 103, 110), bar('09:03', 105, 115)], 103),
                ('bullish', [bar('09:01', 82, 90), bar('09:02', 90, 97), bar('09:03', 85, 95)], 97)]:
            with self.subTest(direction=direction):
                fact = self.metric(review(direction), candles)['objectives']['midpoint']
                self.assertEqual(fact['distance_price_points'], 3)
                self.assertEqual(fact['closest_observed_price'], price)
                self.assertEqual(fact['closest_source_interval']['bar_open_ny'], ny('09:02'))

    def test_missing_minute_is_observation_not_verified_closest(self):
        fact = self.metric(review(), [bar('09:01', 110, 120), bar('09:03', 105, 115)])['objectives']['midpoint']
        self.assertEqual(fact['status'], 'unverified_incomplete_window')
        self.assertIsNone(fact['distance_price_points'])
        self.assertEqual(fact['observed_distance_price_points'], 5)

    def test_purge_bar_touch_does_not_prove_post_purge_delivery(self):
        candles = [bar('09:00', 99, 125), bar('09:01', 105, 120),
                   bar('09:02', 106, 118), bar('09:03', 109, 119)]
        fact = self.metric(review(), candles)['objectives']['midpoint']
        self.assertEqual(fact['status'], 'unverified_boundary_bar_order')
        self.assertIsNone(fact['distance_price_points'])
        self.assertEqual(fact['observed_distance_price_points'], 5)
        self.assertEqual(fact['boundary_observations'][0]['distance_price_points'], 0)

    def test_invalidating_source_bar_is_separate_from_prior_source_bars(self):
        candles = [bar('09:01', 109, 120), bar('09:02', 105, 119),
                   bar('09:03', 99, 130, close=125), bar('09:04', 90, 99)]
        out = self.metric(review(invalid='09:04'), candles, '09:05')
        fact = out['objectives']['midpoint']
        self.assertEqual(out['coverage']['bar_count'], 2)
        self.assertEqual(fact['status'], 'unverified_boundary_bar_order')
        self.assertEqual(fact['observed_distance_price_points'], 5)
        self.assertEqual(fact['boundary_observations'][0]['reason'], 'same_invalidating_close_source_bar')
        self.assertEqual(fact['closest_source_interval']['bar_open_ny'], ny('09:02'))

    def test_cutoff_closed_bar_is_included_without_invalidation(self):
        fact = self.metric(review(), [bar('09:01', 109, 120), bar('09:02', 105, 119),
                                      bar('09:03', 99, 110)])['objectives']['midpoint']
        self.assertEqual(fact['distance_price_points'], 0)
        self.assertEqual(fact['closest_source_interval']['bar_close_ny'], ny('09:04'))

    def test_touch_remains_zero_after_later_invalidation_and_missing_bars(self):
        fact = self.metric(review(invalid='09:05'), [bar('09:01', 99, 110),
            bar('09:04', 115, 130, close=125)], '09:06')['objectives']['midpoint']
        self.assertEqual(fact['status'], 'target_reached_in_verified_post_purge_bar')
        self.assertEqual(fact['distance_price_points'], 0)
        self.assertTrue(fact['first_touch_order_verified'])
        self.assertTrue(fact['coverage_through_touch']['complete'])

    def test_missing_bar_before_touch_keeps_physical_touch_but_not_validity_claim(self):
        out = self.metric(review(), [bar('09:01', 105, 115), bar('09:03', 99, 110)])
        fact = out['objectives']['midpoint']
        self.assertEqual(fact['status'], 'observed_target_touch_validity_unverified')
        self.assertEqual(fact['observed_distance_price_points'], 0)
        self.assertIsNone(fact['distance_price_points'])
        self.assertFalse(fact['first_touch_order_verified'])
        self.assertFalse(fact['coverage_through_touch']['complete'])
        self.assertEqual(fact['coverage_through_touch']['missing_bar_count'], 1)

    def test_gap_after_touch_does_not_change_previously_verified_zero(self):
        candles = [bar('09:01', 105, 115), bar('09:02', 99, 110), bar('09:04', 108, 118)]
        early = self.metric(review(), candles, '09:03')['objectives']['midpoint']
        late = self.metric(review(), candles, '09:05')['objectives']['midpoint']
        for key in ('status', 'distance_price_points', 'first_touch_order_verified',
                    'closest_source_interval', 'coverage_through_touch'):
            self.assertEqual(early[key], late[key])
        self.assertEqual(late['status'], 'target_reached_in_verified_post_purge_bar')
        self.assertEqual(late['distance_price_points'], 0)

    def test_missing_close_before_purge_cannot_verify_later_zero_or_positive_distance(self):
        # A real CRT review has no verified 9AM H1 closing price: its 09:59
        # source candle is missing. A complete sequel after 10:01 cannot repair it.
        for low, observed in ((95, 0), (103, 3)):
            candles = [dict(time=t, open=100, high=110, low=90, close=100)
                       for t in range(parse_time(ny('08:00')), parse_time(ny('09:00')), 60)]
            candles += [dict(time=t, open=105, high=109, low=104, close=105)
                        for t in range(parse_time(ny('09:00')), parse_time(ny('10:01')), 60)
                        if t != parse_time(ny('09:59'))]
            candles += [bar('10:01', 105, 125, close=115),
                        bar('10:02', low, 108), bar('10:03', 106, 109)]
            r = crt_review(candles, parse_time(ny('08:00')), parse_time(ny('10:04')), 'H1', 60)
            out = self.metric(r, candles, '10:04')
            fact = out['objectives']['midpoint']
            self.assertFalse(out['pre_purge_coverage_complete'])
            self.assertEqual(out['pre_purge_missing_bar_count'], 1)
            self.assertEqual(fact['observed_distance_price_points'], observed)
            self.assertIsNone(fact['distance_price_points'])
            self.assertEqual(fact['status'], 'observed_target_touch_validity_unverified'
                             if observed == 0 else 'unverified_incomplete_window')

    def test_ambiguous_direction_and_incomplete_anchor_stay_unverified(self):
        r = review(); r['sweep_order'] = 'unknown_within_same_bar'
        self.assertEqual(self.metric(r, [])['objectives'], {})
        r = review(); r['anchor']['complete'] = False
        self.assertEqual(self.metric(r, [])['status'], 'unverified_incomplete_anchor')

    def test_forming_cutoff_and_duplicate_data_do_not_establish_closest(self):
        candles = [bar('09:01', 105, 110), bar('09:01', 105, 110),
                   bar('09:02', 106, 115), bar('09:03', 108, 115)]
        self.assertEqual(self.metric(review(), candles)['objectives']['midpoint']['status'],
                         'unverified_incomplete_window')
        out = objective_approach(review(), candles[1:], parse_time(ny('09:04')) + 30, 60)
        self.assertFalse(out['coverage']['complete'])

    def test_before_purge_and_after_cutoff_prices_cannot_improve_distance(self):
        candles = [bar('08:59', 90, 99), bar('09:01', 105, 115),
                   bar('09:02', 106, 116), bar('09:03', 107, 117), bar('09:04', 80, 99)]
        fact = self.metric(review(), candles)['objectives']['midpoint']
        self.assertEqual(fact['distance_price_points'], 5)
        self.assertEqual(fact['closest_source_interval']['bar_open_ny'], ny('09:01'))

    def test_coarse_source_retains_coarse_intervals_and_price_point_units(self):
        r = review(); r['events'][0]['bar_close_ny'] = ny('09:05')
        out = objective_approach(r, [bar('09:05', 105, 120), bar('09:10', 102.5, 115)],
                                 parse_time(ny('09:15')), 300)
        fact = out['objectives']['midpoint']
        self.assertEqual(out['distance_unit'], 'provider_price_points')
        self.assertEqual(fact['distance_price_points'], 2.5)
        self.assertEqual(fact['closest_source_interval']['precision_seconds'], 300)
        self.assertEqual(fact['closest_source_interval']['bar_close_ny'], ny('09:15'))
        self.assertFalse(fact['closest_source_interval']['exact_tick_time_known'])


if __name__ == '__main__':
    unittest.main()
