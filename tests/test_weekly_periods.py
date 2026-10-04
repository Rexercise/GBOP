"""Source W1 boundary contract; no broker connection or trading calls."""
import contextlib
import copy
from datetime import datetime
import sqlite3
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from gbop_voice_web import market_data as market
from market_bridge.bridge import collect


WEEK = 7 * 86400


def ts(value):
    return int(datetime.fromisoformat(value).timestamp())


def period(start, end):
    return dict(open_time=start, close_time=end, source='MT5', timeframe='W1')


class SourceWeeklyPeriodsTests(unittest.TestCase):
    def setUp(self):
        self.now = ts('2026-10-04T03:00:00+00:00')
        self.calls = []
        self.opens = [self.now - 2 * WEEK, self.now - WEEK, self.now]

    def mt5(self, rates, *, include_w1=True):
        def copy_rates(symbol, timeframe, position, count):
            self.calls.append((symbol, timeframe, position, count))
            if timeframe == 'W1':
                if isinstance(rates, Exception):
                    raise rates
                return rates
            # Position zero can be the last closed M1/M5 during a feed pause.
            step = timeframe
            return [dict(time=self.now-step, open=100, high=103, low=99, close=102),
                    dict(time=self.now, open=102, high=103, low=99, close=101)]
        result = SimpleNamespace(
            TIMEFRAME_M1=60, TIMEFRAME_M5=300,
            terminal_info=lambda: SimpleNamespace(connected=True),
            symbol_select=lambda symbol, enable: True,
            symbol_info_tick=lambda symbol: SimpleNamespace(bid=100, ask=101, time=self.now),
            copy_rates_from_pos=copy_rates)
        if include_w1:
            result.TIMEFRAME_W1 = 'W1'
        return result

    def capture(self, rates, **kwargs):
        return collect(self.mt5(rates, **kwargs), {'BTCUSD': 'BTCUSDm'}, self.now)

    def test_exact_consecutive_source_opens_with_position_zero_as_end_only(self):
        payload = self.capture([{'time': t} for t in self.opens])
        expected = [period(*self.opens[:2]), period(*self.opens[1:])]
        self.assertEqual(payload['instruments'][0]['weekly_periods'], expected)
        self.assertEqual(self.calls, [('BTCUSDm', 300, 0, 4032),
                                      ('BTCUSDm', 60, 0, 20160),
                                      ('BTCUSDm', 'W1', 0, 3)])
        market.validate_payload(payload, self.now)
        item = payload['instruments'][0]
        self.assertEqual([b['time'] for b in item['bars']], [self.now-300])
        self.assertEqual([b['time'] for b in item['bars_m1']], [self.now-60])
        self.assertNotIn(self.now, [p['open_time'] for p in expected])

    def test_incremental_capture_also_uses_bounded_source_read(self):
        payload = collect(self.mt5([{'time': t} for t in self.opens]),
                          {'ETHUSD': 'ETHUSDm'}, self.now, backfill=False)
        self.assertEqual(self.calls, [('ETHUSDm', 300, 0, 24),
                                      ('ETHUSDm', 60, 0, 120),
                                      ('ETHUSDm', 'W1', 0, 3)])
        self.assertEqual(payload['instruments'][0]['weekly_periods'][-1]['close_time'], self.now)

    def test_sorts_observed_times_without_changing_arbitrary_source_anchor(self):
        self.now = ts('2026-10-07T00:00:00+00:00')
        opens = [ts('2026-09-22T19:30:00+00:00'), ts('2026-09-29T19:30:00+00:00'),
                 ts('2026-10-06T19:30:00+00:00')]
        payload = self.capture([{'time': opens[i]} for i in (2, 0, 1)])
        self.assertEqual(payload['instruments'][0]['weekly_periods'],
                         [period(*opens[:2]), period(*opens[1:])])
        market.validate_payload(payload, self.now)

    def test_observed_dst_offset_changes_are_preserved_without_calendar_rounding(self):
        for opens in [
            [ts('2026-03-01T22:00:00+00:00'), ts('2026-03-08T21:00:00+00:00'), ts('2026-03-15T21:00:00+00:00')],
            [ts('2026-10-25T21:00:00+00:00'), ts('2026-11-01T22:00:00+00:00'), ts('2026-11-08T22:00:00+00:00')],
        ]:
            with self.subTest(opens=opens):
                self.now = opens[-1]
                payload = self.capture([{'time': t} for t in opens])
                periods = payload['instruments'][0]['weekly_periods']
                self.assertEqual(periods, [period(*opens[:2]), period(*opens[1:])])
                market.validate_payload(payload, self.now)
                for p in periods:
                    for zone in ('America/New_York', 'America/Bogota'):
                        for key in ('open_time', 'close_time'):
                            self.assertEqual(int(datetime.fromtimestamp(p[key], ZoneInfo(zone)).timestamp()), p[key])

    def test_fixed_utc_week_keeps_exact_endpoints_across_new_york_dst(self):
        opens = [ts('2026-03-02T00:00:00+00:00'), ts('2026-03-09T00:00:00+00:00'),
                 ts('2026-03-16T00:00:00+00:00')]
        self.now = opens[-1]
        periods = self.capture([{'time': t} for t in opens])['instruments'][0]['weekly_periods']
        self.assertTrue(all(p['close_time']-p['open_time'] == WEEK for p in periods))
        ny = ZoneInfo('America/New_York')
        self.assertEqual([datetime.fromtimestamp(t, ny).hour for t in opens[:2]], [19, 20])

    def test_old_position_zero_is_not_closed_without_next_observed_open(self):
        opens = [self.now-4*WEEK, self.now-3*WEEK, self.now-2*WEEK]
        periods = self.capture([{'time': t} for t in opens])['instruments'][0]['weekly_periods']
        self.assertEqual(periods, [period(*opens[:2]), period(*opens[1:])])
        self.assertTrue(all(p['open_time'] < opens[-1] for p in periods))
        self.assertEqual(periods[-1]['close_time'], opens[-1])

    def test_two_observed_opens_make_one_completed_interval(self):
        payload = self.capture([{'time': t} for t in self.opens[-2:]])
        self.assertEqual(payload['instruments'][0]['weekly_periods'], [period(*self.opens[-2:])])
        market.validate_payload(payload, self.now)

    def test_missing_w1_capability_preserves_legacy_feed(self):
        payload = self.capture(None, include_w1=False)
        self.assertNotIn('weekly_periods', payload['instruments'][0])
        self.assertEqual(len(self.calls), 2)
        market.validate_payload(payload, self.now)

    def test_unavailable_or_broken_w1_never_disrupts_quotes_and_m1_m5(self):
        invalid = [None, [], [{'time': self.now}], RuntimeError('source failed'),
                   [{'time': t} for t in self.opens + [self.now + WEEK]],
                   [{'time': self.opens[0]}, {'time': self.opens[0]}],
                   [{'time': self.now-WEEK}, {'time': self.now+1}],
                   [{'time': self.now-2*WEEK}, {'time': self.now}],
                   [{'time': self.now-60}, {'time': self.now}],
                   [{'time': self.now-WEEK}, {'no_time': self.now}]]
        for bad in (True, False, '10', 0, -1, 1.5, float(self.now-WEEK), float('nan'), float('inf')):
            invalid.append([{'time': bad}, {'time': self.now}])
        legacy = self.capture(None, include_w1=False)
        for rates in invalid:
            with self.subTest(rates=rates):
                payload = self.capture(rates)
                self.assertEqual(payload, legacy)
                market.validate_payload(payload, self.now)


class WeeklyPeriodReceiverTests(unittest.TestCase):
    def setUp(self):
        self.now = ts('2026-10-04T03:00:00+00:00')
        self.periods = [period(self.now-2*WEEK, self.now-WEEK), period(self.now-WEEK, self.now)]
        self.payload = dict(captured_at=self.now, instruments=[dict(
            asset='BTCUSD', symbol='BTCUSDm', bid=100, ask=101, tick_time=self.now,
            bars=[dict(time=self.now-300, open=100, high=103, low=99, close=102)],
            bars_m1=[dict(time=self.now-60, open=100, high=103, low=99, close=102)])])
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        @contextlib.contextmanager
        def db():
            with self.conn:
                yield self.conn
        self.db = db
        self.addCleanup(self.conn.close)

    def test_optional_metadata_round_trips_without_schema_or_bar_changes(self):
        market.ingest(self.db, self.payload, self.now)
        legacy = market.read_feed(self.db, 'BTC', self.now)
        self.assertEqual(legacy['weekly_periods'], [])
        self.payload['captured_at'] += 1
        self.payload['instruments'][0]['weekly_periods'] = self.periods
        market.ingest(self.db, self.payload, self.now+1)
        result = market.read_feed(self.db, 'BTC', self.now+1)
        self.assertEqual(result['weekly_periods'], self.periods)
        for key in ('bid', 'ask', 'symbol', 'bars', 'bars_m1', 'status', 'is_live'):
            self.assertEqual(result[key], legacy[key])
        self.assertEqual([r[0] for r in self.conn.execute('SELECT DISTINCT step FROM gbop_market_history ORDER BY step')], [60, 300])
        # A subsequent legacy/failed-W1 capture clears optional evidence rather
        # than silently presenting stale boundaries as newly observed.
        self.payload['captured_at'] += 1
        del self.payload['instruments'][0]['weekly_periods']
        market.ingest(self.db, self.payload, self.now+2)
        self.assertEqual(market.read_feed(self.db, 'BTC', self.now+2)['weekly_periods'], [])

    def test_empty_metadata_is_valid(self):
        self.payload['instruments'][0]['weekly_periods'] = []
        _, items = market.validate_payload(self.payload, self.now)
        self.assertEqual(items[0]['weekly_periods'], [])

    def test_existing_market_price_output_does_not_gain_weekly_metadata(self):
        self.payload['instruments'][0]['weekly_periods'] = self.periods
        market.ingest(self.db, self.payload, self.now)
        feed = market.read_feed(self.db, 'BTC', self.now)
        self.assertEqual(feed['weekly_periods'], self.periods)
        result = market.market_tool(self.db, 'get_market_price', {'asset': 'BTC'}, self.now)
        expected = {k: v for k, v in feed.items() if k not in ('bars', 'bars_m1', 'weekly_periods')}
        self.assertEqual(result, expected)

    def test_invalid_metadata_rejected_atomically_without_replacing_existing_feed(self):
        market.ingest(self.db, self.payload, self.now)
        self.payload['captured_at'] += 1
        bad_lists = [None, {}, 'W1', self.periods + [self.periods[-1]],
                     list(reversed(self.periods)), [self.periods[0], self.periods[0]],
                     [dict(self.periods[0], close_time=self.periods[1]['open_time']+1), self.periods[1]],
                     [self.periods[0], dict(self.periods[1], open_time=self.periods[1]['open_time']+1)],
                     [dict(self.periods[0], source='calendar')], [dict(self.periods[0], timeframe='D1')],
                     [dict(self.periods[0], extra=True)], [dict(self.periods[0], close_time=self.now+2)],
                     [dict(self.periods[0], close_time=self.periods[0]['open_time'])],
                     [period(self.now-2*WEEK, self.now)], [period(self.now-60, self.now)]]
        for key in ('open_time', 'close_time'):
            bad_lists.append([{k: v for k, v in self.periods[0].items() if k != key}])
            for value in (True, False, 0, -1, '10', 1.5, float(self.periods[0][key]), float('nan'), float('inf')):
                bad_lists.append([dict(self.periods[0], **{key: value})])
        for values in bad_lists:
            with self.subTest(values=values):
                payload = copy.deepcopy(self.payload)
                payload['instruments'][0]['weekly_periods'] = values
                with self.assertRaises(ValueError):
                    market.ingest(self.db, payload, self.now+1)
                self.assertEqual(self.conn.execute('SELECT captured_at FROM gbop_market_feed').fetchone()[0], self.now)


if __name__ == '__main__':
    unittest.main()
