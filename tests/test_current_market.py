"""Synthetic current snapshots: no broker, network, account or journal writes."""
import json
import sqlite3
import unittest
from datetime import datetime

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import NY, parse_time
from gbop_voice_web.current_market import is_current_request, current_request_args


DAY = '2026-10-02'


def ts(clock, day=DAY):
    return parse_time(f'{day}T{clock}:00-04:00')


def candles(start, end, step=60, high=105, low=95):
    return [dict(time=t, open=100, high=high, low=low, close=100)
            for t in range(start, end, step)]


class CurrentMarketTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        self.db = lambda: self.conn

    def tearDown(self):
        self.conn.close()

    def seed(self, now, bars=None, *, capture=None, tick=None, received=None, asset='NAS100', coarse=None):
        capture = now if capture is None else capture
        payload = dict(asset=asset, symbol=asset + 'm', bid=101, ask=102,
                       tick_time=now if tick is None else tick, bars=coarse or [], bars_m1=bars or [])
        self.conn.execute('INSERT OR REPLACE INTO gbop_market_feed VALUES (?,?,?,?)',
                          (asset, capture, now if received is None else received, json.dumps(payload)))

    def review(self, now, **args):
        result = market.market_tool(self.db, 'review_current_market', dict(asset='NAS',
            anchor_start_ny=None, anchor_timeframe=None, confirmation_timeframe=None, **args), now=now)
        self.assertTrue(result.get('ok'), result)
        return result

    def test_pre_shift_seven_is_valid_eight_is_forming_no_future_lookahead(self):
        now = ts('08:50')
        bars = candles(ts('07:00'), ts('12:00'))
        bars[80].update(high=112, close=111)
        bars[119].update(high=120, close=119)  # Future hourly invalidation.
        self.seed(now, bars)
        result = self.review(now)
        review = result['review']
        self.assertTrue(result['is_live'])  # injected now, not process wall time
        self.assertEqual(review['session_clock']['phase'], 'pre_shift')
        self.assertEqual(review['current_scope']['anchor_start_ny'], market.stamp(ts('07:00')))
        seven, eight = review['ranges']
        self.assertEqual(seven['setup_status'], 'initiated')
        self.assertIsNone(seven['invalidated_at_ny'])
        self.assertEqual(seven['variant']['status'], 'pending')
        self.assertEqual(eight['setup_status'], 'pending_reference_close')
        self.assertTrue(eight['anchor']['forming'])
        self.assertFalse(eight['anchor']['complete'])
        self.assertEqual(eight['anchor']['end_ny'], market.stamp(ts('09:00')))
        self.assertNotIn('detail_request', eight)
        self.assertEqual(seven['detail_request']['args']['through_ny'], market.stamp(now))
        self.assertEqual(result['available_through_ny'], market.stamp(now))

    def test_early_pending_absent_and_initiated_are_distinct(self):
        for clock, expected in [('07:30', 'pending_reference_close'),
                                ('08:00', 'pending_observation'),
                                ('08:50', 'not_observed_in_complete_window')]:
            now = ts(clock)
            self.seed(now, candles(ts('07:00'), now))
            review = self.review(now)['review']
            self.assertEqual(review['ranges'][0]['setup_status'], expected)
            self.assertEqual(review['ranges'][0]['model1_count'], 0)

    def test_missing_seven_cannot_establish_a_purge(self):
        now = ts('08:50')
        bars = candles(ts('07:00'), now)
        bars.pop(10)
        bars[-1].update(high=120)
        self.seed(now, bars)
        row = self.review(now)['review']['ranges'][0]
        self.assertEqual(row['setup_status'], 'reference_unverified')
        self.assertIsNone(row['first_purge'])
        self.assertEqual(row['confirmed_cisd_count'], 0)

    def test_in_shift_promotes_only_after_actual_complete_hour(self):
        bars = candles(ts('07:00'), ts('12:00'))
        bars[179].update(high=120, close=119)  # 09:59 close invalidates eight.
        for clock, selected in [('09:59', '08:00'), ('10:00', '09:00'), ('11:20', '09:00')]:
            now = ts(clock)
            self.seed(now, bars)
            review = self.review(now)['review']
            self.assertEqual(review['session_clock']['phase'], 'in_shift')
            self.assertEqual(review['current_scope']['anchor_start_ny'], market.stamp(ts(selected)))
            self.assertTrue(review['current_candle']['forming'])
        bars.pop(150)
        self.seed(ts('11:20'), bars)
        review = self.review(ts('11:20'))['review']
        self.assertEqual(review['current_scope']['anchor_start_ny'], market.stamp(ts('08:00')))
        self.assertEqual(review['selection_status'], 'unverified_missing_hour')

    def test_night_and_offshift_use_actual_hour_not_completed_shift(self):
        for clock, phase, shift, selected in [
            ('06:30', 'off_shift', None, '05:00'),
            ('14:30', 'off_shift', None, '13:00'),
            ('20:50', 'pre_shift', 'night', '19:00'),
            ('21:30', 'in_shift', 'night', '20:00'),
            ('23:30', 'in_shift', 'night', '20:00')]:
            now = ts(clock)
            self.seed(now, candles(ts('00:00'), now))
            review = self.review(now)['review']
            self.assertEqual(review['session_clock']['phase'], phase)
            self.assertEqual(review['current_scope']['shift'], shift)
            self.assertEqual(review['current_scope']['anchor_start_ny'], market.stamp(ts(selected)))
            self.assertLessEqual(len(review['ranges']), 5)
        now = ts('00:10', '2026-10-03')
        self.seed(now, candles(ts('23:00'), now))
        review = self.review(now)['review']
        self.assertEqual(review['session_clock']['phase'], 'off_shift')
        self.assertEqual(review['current_scope']['anchor_start_ny'], market.stamp(ts('23:00')))

    def test_recent_snapshot_old_quote_and_stale_bars_remain_separate(self):
        now = ts('09:32') + 15
        self.seed(now, candles(ts('07:00'), ts('09:25')), capture=now-31, tick=now-600)
        result = self.review(now)
        fresh = result['review']['freshness']
        self.assertEqual(fresh['feed_health']['status'], 'recent_snapshot_stale_quote')
        self.assertEqual(fresh['capture_age_seconds'], 31)
        self.assertEqual(fresh['tick_age_seconds'], 600)
        self.assertEqual(fresh['received_age_seconds'], 0)
        self.assertEqual(fresh['bar_age_seconds'], 7*60+15)
        self.assertEqual(result['review']['current_scope']['through_ny'], market.stamp(ts('09:31')))
        self.assertEqual(result['review']['current_candle']['observed_through_ny'], market.stamp(ts('09:25')))
        self.assertFalse(fresh['instantaneous_tick_observation'])
        self.assertEqual(fresh['source_resolution_seconds'], 60)
        self.assertEqual(result['broker_session']['status'], 'unknown')
        self.assertIn('Never say purging as we speak', result['review']['response_contract'])
        self.assertNotIn('bid', result)

    def test_capture_cutoff_bounds_retained_bars_even_when_future_rows_exist(self):
        now = ts('09:32')
        self.seed(now, candles(ts('07:00'), ts('12:00')), capture=ts('08:50'), tick=ts('08:50'))
        result = self.review(now)
        self.assertEqual(result['available_through_ny'], market.stamp(ts('08:50')))
        self.assertFalse(result['review']['anchor']['complete'])
        self.assertEqual(result['review']['ranges'][1]['setup_status'], 'reference_unverified')

    def test_fine_current_data_wins_over_older_coarse_history(self):
        now = ts('09:32')
        self.seed(now, candles(ts('07:00'), now), coarse=candles(ts('07:00'), ts('09:30'), step=300))
        result = self.review(now)
        self.assertEqual(result['available_precision_seconds'], 60)
        self.assertEqual(result['available_through_ny'], market.stamp(now))

    def test_cisd_waits_for_assigned_close_beyond_full_extreme(self):
        bars = candles(ts('07:00'), ts('09:00'))
        for b in bars:
            if ts('08:20') <= b['time'] < ts('08:25'):
                b.update(high=112, low=97, close=111)
            if ts('08:25') <= b['time'] < ts('08:30'):
                b.update(low=96, close=99)  # Below body open, still above full low.
            if ts('08:30') <= b['time'] < ts('08:35'):
                b.update(low=94, close=96)
        for clock, confirmed in [('08:30', 0), ('08:32', 0), ('08:35', 1)]:
            now = ts(clock)
            self.seed(now, bars)
            row = self.review(now)['review']['ranges'][0]
            self.assertEqual(row['model1_count'], 1)
            self.assertEqual(row['confirmed_cisd_count'], confirmed)
            self.assertEqual(row['variant']['status'], 'pending')

    def test_explicit_higher_and_custom_anchors_preserve_mapping_and_forming(self):
        now = ts('10:32')
        self.seed(now, candles(ts('00:00'), now))
        for tf, anchor, assigned, complete in [('H4', '04:00', 'M15', True),
                                              ('H6', '04:00', 'M30', True),
                                              ('D1', '00:00', 'H1', False)]:
            args = dict(asset='NAS', anchor_start_ny=market.stamp(ts(anchor)),
                        anchor_timeframe=tf, confirmation_timeframe='M30' if tf == 'H6' else None)
            result = market.market_tool(self.db, 'review_current_market', args, now)
            self.assertTrue(result['ok'], result)
            review = result['review']
            self.assertEqual(len(review['ranges']), 1)
            self.assertEqual(review['current_scope']['anchor_timeframe'], tf)
            self.assertEqual(review['current_scope']['assigned_timeframe'], assigned)
            self.assertEqual(review['anchor']['complete'], complete)
            self.assertNotIn('fractal', review)
        bad = market.market_tool(self.db, 'review_current_market',
                                 {'asset': 'NAS', 'anchor_timeframe': 'D1'}, now)
        self.assertFalse(bad['ok'])
        self.assertIn('explicit chart anchor', bad['error'])

    def test_historical_shift_route_retains_completed_review_intent(self):
        now = ts('14:00')
        self.seed(now, candles(ts('07:00'), ts('12:00')))
        result = market.market_tool(self.db, 'review_market_session',
                                   {'asset': 'NAS', 'date_ny': DAY, 'shift': 'day'}, now)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('current_scope', result['review'])
        self.assertEqual(result['review']['shift_story']['end_ny'], market.stamp(ts('12:00')))

    def test_no_today_bars_never_substitutes_a_retained_shift(self):
        now = ts('08:50', '2026-10-03')
        self.seed(now, candles(ts('07:00'), ts('12:00')), tick=ts('12:00'))
        review = self.review(now)['review']
        self.assertEqual(review['current_scope']['date_ny'], '2026-10-03')
        self.assertIsNone(review['observed_through_ny'])
        self.assertEqual(review['ranges'][0]['setup_status'], 'reference_unverified')

    def test_peer_forming_setup_is_only_potential_with_same_current_cutoff(self):
        now = ts('09:32')
        bars = candles(ts('07:00'), now)
        bars[135].update(high=112)
        self.seed(now, bars)
        self.seed(now, candles(ts('07:00'), now), asset='SPX')
        paired = self.review(now)['review']['paired_context']
        self.assertEqual(paired['comparison_asset'], 'SPX')
        self.assertEqual(paired['event_count'], 1)
        self.assertTrue(paired['events'][0]['setup_interval']['potential_smt'])
        self.assertFalse(paired['events'][0]['setup_interval']['qualified_smt'])
        self.assertEqual(paired['detail_request']['args']['through_ny'], market.stamp(now))

    def test_exact_session_boundaries_with_one_minute_behind_capture(self):
        for clock, phase, shift, anchor in [
            ('00:00', 'off_shift', None, '23:00'), ('07:00', 'pre_shift', 'day', '07:00'),
            ('09:00', 'in_shift', 'day', '08:00'), ('12:00', 'off_shift', None, '11:00'),
            ('19:00', 'pre_shift', 'night', '19:00'), ('21:00', 'in_shift', 'night', '20:00')]:
            with self.subTest(clock=clock):
                now = ts(clock, '2026-10-03')
                self.seed(now, candles(now-3*3600, now+3600), capture=now-60)
                review = self.review(now)['review']
                self.assertEqual(review['session_clock']['phase'], phase)
                self.assertEqual(review['current_scope']['shift'], shift)
                self.assertTrue(review['current_candle']['forming'])
                selected = review['anchor']
                self.assertIn('T'+anchor, selected['start_ny'])
                if clock in ('07:00', '19:00'):
                    self.assertEqual(selected['status'], 'forming')
                    self.assertIsNone(review['current_scope']['through_ny'])
                else:
                    self.assertEqual(selected['status'], 'closed_time_incomplete_evidence')
                self.assertFalse(selected['complete'])
                self.assertNotIn('detail_request', next(row for row in review['ranges']
                                                       if row['role'] == 'selected_range'))

    def test_old_snapshot_never_exposes_reversed_executable_scope(self):
        now = ts('14:30', '2026-10-03')
        self.seed(now, candles(ts('07:00'), ts('12:00')), capture=ts('12:00'), tick=ts('12:00'))
        review = self.review(now)['review']
        self.assertEqual(review['current_scope']['evidence_status'], 'no_current_range_evidence')
        self.assertIsNone(review['current_scope']['through_ny'])
        self.assertIsNone(review['observed_through_ny'])
        self.assertFalse(any('detail_request' in row for row in review['ranges']))

    def test_custom_timeframe_without_assigned_mapping_is_explicitly_unverified(self):
        now = ts('10:32')
        self.seed(now, candles(ts('00:00'), now))
        result = market.market_tool(self.db, 'review_current_market',
            {'asset': 'NAS', 'anchor_start_ny': market.stamp(ts('04:00')), 'anchor_timeframe': 'H6'}, now)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(result['review']['current_scope']['assigned_timeframe'])
        self.assertEqual(result['review']['ranges'][0]['model1_status'], 'assigned_timeframe_required')


class CurrentTextIntentTests(unittest.TestCase):
    def test_conservative_current_requests_leave_historical_intent_alone(self):
        for text in ('What do you see on NAS?', 'What are you seeing on gold?',
                     'How does BTC look?', 'Review NAS right now', 'Refresh this H4 range'):
            self.assertTrue(is_current_request(text), text)
        for text in ('What do you see on NAS last night?', 'Recap today on NAS',
                     'What do you see on 2026-10-01?', 'What do you see on Wednesday?',
                     'Now oil', 'What did NAS do today?', 'How did nine ate eight play out?',
                     'Define current market analysis', None):
            self.assertFalse(is_current_request(text), text)

    def test_current_named_hour_does_not_inherit_old_review_date(self):
        selected = dict(asset='NAS100', date_ny='2026-10-01', shift='day',
                        anchor_start_ny='2026-10-01T08:00:00-04:00', anchor_timeframe='H1')
        args = current_request_args('What do you see on the 8 AM range now?',
            {'anchor_start_ny': '2026-10-01T08:00:00-04:00'}, selected, ts('09:32'))
        self.assertEqual(args['anchor_start_ny'], market.stamp(ts('08:00')))

    def test_current_higher_timeframe_uses_explicit_or_matching_selected_anchor(self):
        selected = dict(asset='NAS100', anchor_start_ny=market.stamp(ts('04:00')),
                        anchor_timeframe='H4', assigned_timeframe='M15')
        args = current_request_args('Refresh this H4 range', {}, selected, ts('09:32'))
        self.assertEqual(args['anchor_start_ny'], selected['anchor_start_ny'])
        self.assertEqual(args['confirmation_timeframe'], 'M15')
        args = current_request_args('Currently review NAS H4 anchor 2026-10-02T04:00:00-04:00',
                                    {'asset': 'NAS100'}, None, ts('09:32'))
        self.assertEqual(args['anchor_timeframe'], 'H4')
        self.assertEqual(args['anchor_start_ny'], market.stamp(ts('04:00')))
        with self.assertRaisesRegex(ValueError, 'chart opening'):
            current_request_args('What do you see on daily NAS?', {'asset': 'NAS100'}, selected, ts('09:32'))

    def test_generic_current_drops_old_anchor_but_missing_or_multiple_assets_clarify(self):
        args = current_request_args('What do you see on NAS?', {'asset': 'NAS100'},
            dict(asset='NAS100', anchor_start_ny=market.stamp(ts('04:00')), anchor_timeframe='H4'))
        self.assertIsNone(args['anchor_start_ny'])
        with self.assertRaisesRegex(ValueError, 'Which market'):
            current_request_args('What do you see?', {}, None)
        with self.assertRaisesRegex(ValueError, 'Which one market'):
            current_request_args('What do you see on NAS and gold?', {}, {'asset': 'NAS100'})


if __name__ == '__main__':
    unittest.main()
