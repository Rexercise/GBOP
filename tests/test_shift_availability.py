"""No provider calls: synthetic retained broker candles and the real tool routes."""
import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.shift_availability import assess_shift, shift_bounds
from gbop_voice_web.market_watch import init_watches, watch_tool, VERSION
from gbop_voice_web.market_watch_runtime import prepare_next_shift
from gbop_voice_web.voice_runtime import compact_voice_tool_result, READ_ONLY_RECOVERY_NAMES

DAY = '2026-10-02'
NOW = parse_time('2026-10-03T13:00:00-04:00')


def candles(day=DAY, shift='day', step=300, anchor=True):
    start, end = shift_bounds(day, shift)
    return [dict(time=t, open=100, high=102, low=98, close=101)
            for t in range(start - (3600 if anchor else 0), end, step)]


class AvailabilityFactsTests(unittest.TestCase):
    def test_full_requires_eight_anchor_but_not_seven_context(self):
        result = assess_shift(candles(), DAY, 'day', 300, NOW)
        self.assertEqual(result['review_scope'], 'full')
        self.assertTrue(result['anchor_complete'])
        self.assertEqual(result['closed_bar_count'], 36)
        result = assess_shift(candles(anchor=False), DAY, 'day', 300, NOW)
        self.assertTrue(result['reviewable'])
        self.assertEqual(result['review_scope'], 'partial')
        self.assertTrue(result['shift_complete'])
        self.assertFalse(result['anchor_complete'])

    def test_anchor_only_does_not_make_a_shift_exist(self):
        result = assess_shift(candles()[:12], DAY, 'day', 300, NOW)
        self.assertEqual(result['status'], 'no_data')
        self.assertFalse(result['reviewable'])

    def test_incomplete_shift_keeps_supported_subset_and_gap(self):
        bars = candles(); bars.pop(24)
        result = assess_shift(bars, DAY, 'day', 300, NOW)
        self.assertTrue(result['reviewable'])
        self.assertEqual(result['review_scope'], 'partial')
        self.assertEqual(len(result['complete_hours_ny']), 2)
        self.assertEqual(len(result['observed_windows_ny']), 2)
        self.assertEqual(result['market_closure'], 'unverified')

    def test_meaningful_partial_is_one_complete_m5_not_scattered_minutes(self):
        start, _ = shift_bounds(DAY, 'day')
        sparse = [dict(time=start+i*60) for i in (0, 2, 4, 6, 8)]
        self.assertFalse(assess_shift(sparse, DAY, 'day', 60, NOW)['reviewable'])
        contiguous = [dict(time=start+i*60) for i in range(5)]
        self.assertEqual(assess_shift(contiguous, DAY, 'day', 60, NOW)['review_scope'], 'partial')
        self.assertTrue(assess_shift([dict(time=start)], DAY, 'day', 300, NOW)['reviewable'])

    def test_forming_future_and_cutoff_bars_are_excluded(self):
        start, end = shift_bounds(DAY, 'day')
        self.assertFalse(assess_shift(candles(), DAY, 'day', 300, start+299)['reviewable'])
        result = assess_shift(candles(), DAY, 'day', 300, start+300)
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(result['closed_bar_count'], 1)
        self.assertEqual(result['review_scope'], 'partial')
        self.assertFalse(assess_shift([dict(time=end)], DAY, 'day', 300, NOW)['reviewable'])

    def test_not_started_is_not_no_data_or_closed(self):
        start, _ = shift_bounds(DAY, 'night')
        result = assess_shift([], DAY, 'night', 300, start-1)
        self.assertEqual(result['status'], 'not_started')
        self.assertEqual(result['market_closure'], 'unverified')

    def test_night_belongs_to_start_date_and_ends_at_next_midnight(self):
        result = assess_shift(candles(shift='night'), DAY, 'night', 300, NOW)
        self.assertEqual(result['start_ny'], '2026-10-02T21:00:00-04:00')
        self.assertEqual(result['end_ny'], '2026-10-03T00:00:00-04:00')
        self.assertEqual(result['date_ny'], DAY)
        self.assertEqual(result['review_scope'], 'full')

    def test_ny_dst_offsets_and_boundaries(self):
        for day, offset in [('2026-03-07', '-05:00'), ('2026-03-08', '-04:00'),
                            ('2026-10-31', '-04:00'), ('2026-11-01', '-05:00')]:
            for shift in ('day', 'night'):
                with self.subTest(day=day, shift=shift):
                    start, end = shift_bounds(day, shift)
                    result = assess_shift(candles(day, shift), day, shift, 300, end)
                    self.assertTrue(result['start_ny'].endswith(offset))
                    self.assertEqual(end-start, 3*3600)
                    self.assertEqual(result['review_scope'], 'full')


class AvailabilityToolTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.db = lambda: self.conn
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        init_watches(self.db)

    def tearDown(self):
        self.conn.close()

    def store(self, asset='EURUSD', bars=None, fine=None, history_only=False, symbol=None):
        bars = candles() if bars is None else bars
        symbol = symbol or asset+'test'
        payload = dict(asset=asset, symbol=symbol, bid=100, ask=101, tick_time=NOW,
                       bars=[] if history_only else bars, bars_m1=[] if history_only else (fine or []))
        self.conn.execute('INSERT OR REPLACE INTO gbop_market_feed VALUES (?,?,?,?)',
                          (asset, NOW, NOW, json.dumps(payload)))
        for step, source in ((300, bars), (60, fine or [])):
            days = {}
            for bar in source:
                days.setdefault(bar['time']//86400*86400, []).append(bar)
            for day, values in days.items():
                self.conn.execute('INSERT OR REPLACE INTO gbop_market_history VALUES (?,?,?,?,?)',
                                  (asset, symbol, step, day, json.dumps(values)))
        self.conn.commit()

    def call(self, name='list_market_shifts', day=DAY, shift='night', asset='EURUSD', now=NOW):
        return market.market_tool(self.db, name, dict(asset=asset, date_ny=day, shift=shift), now)

    def test_weekend_crypto_uses_actual_candles_not_weekday_blacklist(self):
        saturday = '2026-10-03'
        self.store('BTCUSD', candles(saturday))
        self.store('NAS100', candles())
        crypto = self.call(day=saturday, asset='BTCUSD')
        index = self.call(day=saturday, asset='NAS100')
        self.assertEqual([r['shift'] for r in crypto['available_shifts']], ['day'])
        self.assertEqual(index['available_shifts'], [])
        self.assertTrue(all(r['market_closure']=='unverified' for r in index['shifts']))
        # A non-crypto broker feed with real weekend bars is also evidence, not a calendar guess.
        self.store('EURUSD', candles(saturday))
        self.assertEqual(self.call(day=saturday)['available_shifts'][0]['review_scope'], 'full')

    def test_unavailable_night_offers_checked_same_day_first_without_review(self):
        self.store(bars=candles()+candles('2026-10-01', 'night'))
        with patch.object(market, 'session_review') as review:
            result = self.call('review_market_session')
        review.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'shift_unavailable')
        self.assertEqual([(r['date_ny'],r['shift']) for r in result['alternatives']], [(DAY,'day')])
        self.assertIn('I don’t have night-shift data', result['message'])
        self.assertIn('reason is unverified', result['message'])
        self.assertIn('available day shift for that day', result['message'])
        self.assertNotIn('review', result)
        self.assertEqual(compact_voice_tool_result('review_market_session', result), result)

    def test_no_data_has_no_offer_without_verified_alternative(self):
        self.store(bars=[])
        result = self.call('review_market_session')
        self.assertEqual(result['alternatives'], [])
        self.assertNotIn('Would you like', result['message'])
        self.assertEqual(result['availability']['market_closure'], 'unverified')

    def test_nearest_date_only_when_same_day_unavailable_and_no_silent_switch(self):
        self.store(bars=candles('2026-10-01'))
        result = self.call('review_market_session')
        self.assertFalse(result['ok'])
        self.assertNotIn('review', result)
        self.assertEqual(result['availability']['date_ny'], DAY)
        self.assertEqual(result['alternatives'][0]['date_ny'], '2026-10-01')
        self.assertIn('2026-10-01', result['message'])

    def test_partial_same_day_is_offered_as_limited(self):
        self.store(bars=candles()[12:24])
        result = self.call('review_market_session')
        self.assertIn('limited day shift for that day', result['message'])
        day = self.call('review_market_session', shift='day')
        self.assertTrue(day['ok'])
        self.assertEqual(day['availability']['review_scope'], 'partial')
        self.assertFalse(day['review']['shift_story']['coverage']['complete'])

    def test_live_shift_cannot_displace_latest_completed_default(self):
        self.store(bars=candles('2026-10-01')+candles())
        now = parse_time(DAY+'T10:30:00-04:00')
        choices = self.call(day=None, now=now)
        self.assertEqual(choices['available_shifts'][0]['date_ny'], '2026-10-01')
        self.assertEqual(choices['ongoing_shifts'][0]['date_ny'], DAY)
        default = self.call('review_market_session', day=None, shift='day', now=now)
        self.assertEqual(default['review']['date_ny'], '2026-10-01')
        live = self.call('review_market_session', shift='day', now=now)
        self.assertEqual(live['availability']['status'], 'in_progress')
        self.assertEqual(live['availability']['closed_bar_count'], 18)
        self.assertEqual(live['available_through_ny'], DAY+'T10:30:00-04:00')

    def test_no_completed_default_lists_live_separately(self):
        self.store()
        result = self.call('review_market_session', day=None, shift='day', now=parse_time(DAY+'T10:00:00-04:00'))
        self.assertEqual(result['status'], 'no_completed_shift')
        self.assertEqual(result['alternatives'], [])
        self.assertEqual(result['ongoing_shifts'][0]['temporal_status'], 'in_progress')

    def test_latest_completed_fallback_scans_retained_history_beyond_four_days(self):
        source = candles('2026-09-20')
        for day in range(21, 31):
            # More than eight daily history buckets, none containing shift candles.
            source.append(dict(time=parse_time(f'2026-09-{day}T16:00:00-04:00'), open=1, high=2, low=1, close=1))
        self.store(bars=source, history_only=True)
        choices = self.call(day=None)
        self.assertEqual(choices['available_shifts'][0]['date_ny'], '2026-09-20')
        feed = market.read_feed(self.db, 'EURUSD')
        self.assertEqual(market.latest_available_shift_date(feed, 'day', self.db, NOW), '2026-09-20')

    def test_same_day_alternative_reads_only_that_date_before_offering(self):
        self.store(bars=candles()+candles('2026-09-20'))
        with patch.object(market, '_history_sets', wraps=market._history_sets) as reads:
            result = self.call('review_market_session')
        self.assertEqual(result['alternatives'][0]['date_ny'], DAY)
        self.assertTrue(all(call.args[3]-call.args[2] <= 86400 for call in reads.call_args_list))

    def test_background_latest_scan_loads_newest_payload_first(self):
        self.store(bars=candles()+candles('2026-09-20'), history_only=True)
        feed = market.read_feed(self.db, 'EURUSD')
        with patch.object(market, '_history_sets', wraps=market._history_sets) as reads:
            self.assertEqual(market.latest_available_shift_date(feed, 'day', self.db, NOW), DAY)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(reads.call_args.args[3]-reads.call_args.args[2], 5*3600)

    def test_broker_symbol_and_asset_do_not_leak_choices(self):
        self.store('NAS100')
        self.store(bars=candles(), symbol='old_symbol')
        self.store(bars=[], symbol='new_symbol')
        self.assertEqual(self.call()['available_shifts'], [])

    def test_source_with_full_coverage_beats_sparse_finer_feed(self):
        fine = candles(step=60); fine.pop(62)
        self.store(fine=fine)
        choices = self.call(shift='day')
        day = next(r for r in choices['shifts'] if r['shift']=='day')
        self.assertEqual(day['source_resolution_seconds'], 300)
        result = self.call('review_market_session', shift='day')
        self.assertEqual(result['availability']['review_scope'], 'full')
        self.assertEqual(result['available_precision_seconds'], 300)

    def test_preparation_only_saves_supported_completed_shift_and_retains_limits(self):
        self.store(bars=candles()[12:24])
        prepared = prepare_next_shift(self.db, {}, NOW)
        self.assertEqual(prepared['shift'], 'day')
        saved = watch_tool(self.db,1,42,42,'get_prepared_market_brief',dict(asset='EURUSD',shift='day'),NOW)
        self.assertTrue(saved['ok'])
        self.assertEqual(saved['availability']['review_scope'], 'partial')
        unavailable = watch_tool(self.db,1,42,42,'get_prepared_market_brief',
                                 dict(asset='EURUSD',date_ny=DAY,shift='night'),NOW)
        self.assertEqual(unavailable['status'], 'shift_unavailable')
        self.assertIn('limited day shift for that day', unavailable['message'])

    def test_no_data_or_ongoing_only_is_never_prepared(self):
        self.store(bars=[])
        self.assertIsNone(prepare_next_shift(self.db, {}, NOW))
        self.store()
        self.assertIsNone(prepare_next_shift(self.db, {}, parse_time(DAY+'T10:00:00-04:00')))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_prepared_shifts').fetchone()[0], 0)

    def test_prepared_selection_skips_newer_invalid_and_legacy_briefs(self):
        self.store(bars=candles('2026-10-01'))
        prepare_next_shift(self.db, {}, NOW)
        self.conn.execute('INSERT INTO gbop_prepared_shifts VALUES (?,?,?,?,?,?)',
                          ('EURUSD', DAY, 'day', VERSION, NOW, json.dumps({'recap':'no evidence'})))
        saved = watch_tool(self.db,1,42,42,'get_prepared_market_brief',dict(asset='EURUSD',shift='day'),NOW)
        self.assertEqual(saved['date_ny'], '2026-10-01')

    def test_both_transports_authorize_choices_and_recovery_allows_read_only(self):
        from test_member_readiness import function
        self.assertIn('list_market_shifts', READ_ONLY_RECOVERY_NAMES)
        for path, name, owner in [('bot.py','ai_execute_tool','GTOP_OWNER_USER_ID'),
                                  ('gbop_voice_web/server.py','run_tool','OWNER_USER_ID')]:
            runner = Mock(return_value={'ok':True})
            access = Mock(return_value='revoked')
            ns = dict(member_access_error=access,db=object(),GTOP_GUILD_ID=1,
                      MARKET_NAMES=market.MARKET_NAMES,market_tool=runner,WATCH_NAMES=set())
            ns[owner] = 99
            invoke = function(path,name,ns)
            self.assertFalse(invoke(10,'list_market_shifts',{'asset':'EURUSD','user_id':99})['ok'])
            runner.assert_not_called()
            access.return_value = None
            self.assertTrue(invoke(10,'list_market_shifts',{'asset':'EURUSD'})['ok'])

    def test_text_and_live_prompts_check_before_spoken_shift_choices(self):
        for prompt in (market.MARKET_PROMPT, market.LIVE_MARKET_PROMPT):
            self.assertIn('list_market_shifts', prompt)
            self.assertIn('same-day', prompt)
            self.assertIn('never silently switch date/shift', prompt)
            self.assertIn('Missing data does not prove', prompt)


if __name__ == '__main__':
    unittest.main()
