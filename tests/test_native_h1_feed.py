"""Synthetic native H1 wire/storage tests; no broker, network or production DB."""
import contextlib
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from gbop_voice_web import market_data as market
from market_bridge.bridge import collect, collect_native_h1, send, NATIVE_H1_SOURCE


NOW = int(datetime(2026, 10, 6, 8, tzinfo=timezone.utc).timestamp())
# Illustrative symbols only: tests do not verify any live broker mapping.
SYMBOLS = dict(NAS100='USTECm', SPX='US500test', US30='US30m', XAUUSD='XAUUSDm',
               XAGUSD='XAGUSDm', BTCUSD='BTCUSDm', ETHUSD='ETHUSDm',
               EURUSD='EURUSDm', WTI='USOILm')


def bar(t, **changes):
    return dict(time=t, open=100, high=105, low=95, close=101, **changes)


def payload(captured=NOW, *, asset='NAS100', symbol='USTECm', native=True):
    item = dict(asset=asset, symbol=symbol, bid=101, ask=102, tick_time=captured,
                bars=[bar(captured // 300 * 300 - 300)],
                bars_m1=[bar(captured // 60 * 60 - 60)])
    if native:
        item.update(bars_h1=[bar(captured // 3600 * 3600 - 3600)],
                    native_h1_source=dict(NATIVE_H1_SOURCE))
    return dict(captured_at=captured, instruments=[item])


class FakeMT5:
    TIMEFRAME_M1 = 60
    TIMEFRAME_M5 = 300
    TIMEFRAME_H1 = 3600

    def __init__(self, captured=NOW, h1=None):
        self.captured, self.h1, self.calls = captured, h1, []

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def symbol_select(self, symbol, enabled):
        return True

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=101, ask=102, time=self.captured)

    def copy_rates_from_pos(self, symbol, timeframe, position, count):
        self.calls.append((symbol, timeframe, position, count))
        if timeframe == 3600 and self.h1 is not None:
            if isinstance(self.h1, Exception):
                raise self.h1
            return self.h1[-count:]
        boundary = self.captured // timeframe * timeframe
        return [bar(boundary - timeframe), bar(boundary)]


class NativeH1CollectorTests(unittest.TestCase):
    def test_all_nine_exact_symbols_and_native_provenance(self):
        mt5 = FakeMT5()
        data = collect(mt5, SYMBOLS, NOW)
        _, clean = market.validate_payload(data, NOW)
        self.assertEqual({i['asset']: i['symbol'] for i in clean}, SYMBOLS)
        for item in clean:
            self.assertEqual(item['bars_h1'], [bar(NOW - 3600)])
            self.assertEqual(item['native_h1_source'], NATIVE_H1_SOURCE)
        h1_calls = [c for c in mt5.calls if c[1] == 3600]
        self.assertEqual(h1_calls, [(s, 3600, 0, 337) for s in SYMBOLS.values()])
        mt5 = FakeMT5()
        collect(mt5, SYMBOLS, NOW, backfill=False)
        self.assertEqual([c for c in mt5.calls if c[1] == 3600],
                         [(s, 3600, 0, 3) for s in SYMBOLS.values()])

    def test_upload_headroom_and_forming_candles_for_all_capture_offsets(self):
        for offset in (0, 121, 180, 181, 3550, 3599):
            captured = NOW + offset
            rates = [bar(t) for t in range(NOW - 15 * 86400, NOW + 3600, 3600)]
            data = collect(FakeMT5(captured, rates), {'NAS100': 'USTECm'}, captured)
            bars = data['instruments'][0]['bars_h1']
            self.assertLessEqual(len(bars), market.MAX_H1_BARS)
            self.assertTrue(all(b['time'] >= captured - 14 * 86400 + 180 for b in bars))
            self.assertTrue(all(b['time'] + 3600 <= captured for b in bars))
            self.assertEqual(bars[-1], bar(NOW - 3600))
            for delay in (0, 1, 179, 180):
                market.validate_payload(data, captured + delay)

    def test_closed_position_zero_survives_a_broker_break(self):
        rates = [bar(NOW - i * 3600) for i in (5, 4, 3)]
        data = collect(FakeMT5(h1=rates), {'NAS100': 'USTECm'}, NOW, backfill=False)
        self.assertEqual(data['instruments'][0]['bars_h1'], rates)

    def test_unavailable_and_malformed_optional_native_data_preserve_ordinary_feed(self):
        cases = [[], RuntimeError('Unavailable'), [bar(NOW - 3600)] * 2,
                 [dict(bar(NOW - 3600), time=float(NOW - 3600))],
                 [bar(NOW - 3600 + 1)], [dict(bar(NOW - 3600), high=float('nan'))],
                 [dict(bar(NOW - 3600), low=110)], [dict(time=NOW - 3600)],
                 [dict(bar(NOW - 3600), open=True)], [dict(bar(NOW - 3600), close=-1)]]
        for bad in cases:
            with self.subTest(data=bad):
                result = collect(FakeMT5(h1=bad), {'NAS100': 'USTECm'}, NOW)['instruments'][0]
                self.assertNotIn('bars_h1', result)
                self.assertNotIn('native_h1_source', result)
                self.assertEqual(result['bars'], [bar(NOW - 300)])
                self.assertEqual(result['bars_m1'], [bar(NOW - 60)])
        missing = FakeMT5()
        missing.TIMEFRAME_H1 = None
        self.assertIsNone(collect_native_h1(missing, 'USTECm', NOW))
        self.assertEqual(missing.calls, [])
        mt5 = FakeMT5()
        mt5.copy_rates_from_pos = lambda *args: None
        self.assertIsNone(collect_native_h1(mt5, 'USTECm', NOW))

    def test_unsorted_native_source_is_sorted_without_shifting_or_filling(self):
        rates = [bar(NOW - 3600), bar(NOW - 4 * 3600)]
        self.assertEqual(collect_native_h1(FakeMT5(h1=rates), 'USTECm', NOW), list(reversed(rates)))


class NativeH1FeedTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)

        @contextlib.contextmanager
        def db():
            with self.conn:
                yield self.conn
        self.db = db

    def tearDown(self):
        self.conn.close()

    def load(self, start=NOW - 86400, end=NOW, asset='NAS100', db=True):
        feed = market.read_feed(self.db, asset, NOW)
        return market.history_native_h1(self.db if db else None, feed, start, end)

    def test_roundtrip_all_nine_assets_retains_provenance_and_capture(self):
        data = dict(captured_at=NOW, instruments=[
            payload(asset=a, symbol=s)['instruments'][0] for a, s in SYMBOLS.items()])
        result = market.ingest(self.db, data, NOW)
        self.assertEqual(result['accepted_assets'], list(SYMBOLS))
        for asset, symbol in SYMBOLS.items():
            for db in (True, False):
                self.assertEqual(self.load(asset=asset, db=db), [dict(bar(NOW - 3600), provenance=dict(
                    NATIVE_H1_SOURCE, asset=asset, symbol=symbol, captured_at=NOW))])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_history WHERE step=3600').fetchone()[0], 9)

    def test_full_nine_asset_m1_m5_h1_capture_splits_and_survives_delayed_receipt(self):
        rates = {step: [bar(t) for t in range(NOW - 14 * 86400, NOW + step, step)]
                 for step in (60, 300, 3600)}
        mt5 = FakeMT5()
        mt5.copy_rates_from_pos = lambda symbol, step, pos, count: rates[step][-count:]
        data = collect(mt5, SYMBOLS, NOW)
        received = []

        class Opener:
            def open(inner, request, timeout):
                self.assertLessEqual(len(request.data), market.MAX_BYTES)
                chunk = json.loads(request.data)
                result = market.ingest(self.db, chunk, NOW + min(60 * len(received), 180))
                received.extend(result['accepted_assets'])
                response = SimpleNamespace(read=lambda: json.dumps(result).encode())
                return contextlib.nullcontext(response)

        with patch('urllib.request.build_opener', return_value=Opener()) as opener:
            result = send(dict(endpoint='https://gbop.onrender.com/api/market/ingest', token='x' * 40), data)
        self.assertGreater(opener.call_count, 1)
        self.assertEqual(result['accepted_assets'], list(SYMBOLS))
        self.assertEqual(received, list(SYMBOLS))
        for asset in SYMBOLS:
            native = self.load(start=NOW - 14 * 86400, asset=asset)
            self.assertEqual(len(native), 335)
            self.assertTrue(all(b['provenance']['asset'] == asset for b in native))

    def test_legacy_payload_still_valid_and_has_no_native_claim(self):
        market.ingest(self.db, payload(native=False), NOW)
        feed = market.read_feed(self.db, 'NAS100', NOW)
        self.assertNotIn('bars_h1', feed)
        self.assertNotIn('native_h1_source', feed)
        self.assertEqual(self.load(), [])
        self.assertEqual(market.history_bars(self.db, feed, NOW - 3600, NOW)[1], 300)

    def test_supplied_malformed_native_candles_rejected_atomically(self):
        bad_bars = [None, {}, [bar(NOW)], [bar(NOW - 3600 + 1)],
                    [bar(NOW - 3600), bar(NOW - 3600)],
                    [bar(NOW - 3600), bar(NOW - 7200)],
                    [dict(bar(NOW - 3600), extra='injected')],
                    [dict(bar(NOW - 3600), high=99)],
                    [dict(bar(NOW - 3600), low=float('nan'))],
                    [dict(bar(NOW - 3600), close=True)],
                    [bar(NOW - 15 * 86400)], [bar(NOW - 3600)] * 337]
        for bars in bad_bars:
            data = payload()
            data['instruments'][0]['bars_h1'] = bars
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                market.ingest(self.db, data, NOW)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_feed').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_history').fetchone()[0], 0)

    def test_source_whitelist_is_exact_and_fields_must_arrive_together(self):
        sources = [None, 'MT5', {}, dict(NATIVE_H1_SOURCE, source='Yahoo'),
                   dict(NATIVE_H1_SOURCE, timeframe='M1'), dict(NATIVE_H1_SOURCE, method='aggregate'),
                   dict(NATIVE_H1_SOURCE, extra='untrusted')]
        for source in sources:
            data = payload()
            data['instruments'][0]['native_h1_source'] = source
            with self.subTest(source=source), self.assertRaises(ValueError):
                market.validate_payload(data, NOW)
        for missing in ('bars_h1', 'native_h1_source'):
            data = payload()
            data['instruments'][0].pop(missing)
            with self.assertRaises(ValueError):
                market.validate_payload(data, NOW)
        data = payload()
        data['instruments'][0]['bars_h1'] = []
        market.validate_payload(data, NOW)

    def test_receiver_native_age_boundary_remains_relative_to_receipt(self):
        data = payload()
        data['instruments'][0]['bars_h1'] = [bar(NOW - 14 * 86400)]
        market.validate_payload(data, NOW)
        with self.assertRaisesRegex(ValueError, 'within 14 days'):
            market.validate_payload(data, NOW + 1)

    def test_retention_merges_native_hours_without_rewriting_earlier_capture(self):
        data = payload()
        data['instruments'][0]['bars_h1'] = [bar(NOW - 7200)]
        market.ingest(self.db, data, NOW)
        newer = payload(NOW + 30)
        market.ingest(self.db, newer, NOW + 30)
        records = self.load(end=NOW + 30)
        self.assertEqual([b['time'] for b in records], [NOW - 7200, NOW - 3600])
        self.assertEqual([b['provenance']['captured_at'] for b in records], [NOW, NOW + 30])
        market.ingest(self.db, payload(NOW + 60, native=False), NOW + 60)
        self.assertEqual(self.load(end=NOW + 60), records)
        self.assertEqual(self.load(end=NOW + 60, db=False), [])

    def test_forming_cutoff_and_feed_capture_bound_history_without_retrieval_lookahead(self):
        market.ingest(self.db, payload(), NOW)
        # Retrieved at 08:00 UTC, usable as an already-closed historical candle.
        self.assertEqual(len(self.load(end=NOW)), 1)
        self.assertEqual(self.load(end=NOW - 1), [])
        feed = market.read_feed(self.db, 'NAS100', NOW)
        feed['captured_at_utc'] = datetime.fromtimestamp(NOW - 1, timezone.utc).isoformat()
        self.assertEqual(market.history_native_h1(self.db, feed, NOW - 7200, NOW + 3600), [])
        # Even a corrupt future row may never supplement a valid earlier feed.
        envelope = dict(source=NATIVE_H1_SOURCE, bars=[dict(bar(NOW), captured_at=NOW + 3600)])
        self.conn.execute('UPDATE gbop_market_history SET payload=? WHERE step=3600', (json.dumps(envelope),))
        self.assertEqual(self.load(end=NOW + 3600), self.load(end=NOW, db=False))

    def test_later_native_retrieval_can_establish_an_already_closed_historical_hour(self):
        market.ingest(self.db, payload(NOW + 30), NOW + 30)
        for db in (True, False):
            result = self.load(end=NOW, db=db)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['time'] + 3600, NOW)
            self.assertEqual(result[0]['provenance']['captured_at'], NOW + 30)

    def test_exact_symbol_and_asset_isolation(self):
        market.ingest(self.db, payload(), NOW)
        feed = market.read_feed(self.db, 'NAS100', NOW)
        feed.update(symbol='USTEC-other', bars_h1=[], native_h1_source=None)
        self.assertEqual(market.history_native_h1(self.db, feed, NOW - 7200, NOW), [])
        feed.update(symbol='USTECm', asset='SPX')
        self.assertEqual(market.history_native_h1(self.db, feed, NOW - 7200, NOW), [])
        market.ingest(self.db, payload(NOW + 30, symbol='USTEC-other', native=False), NOW + 30)
        self.assertEqual(self.load(), [])

    def test_unproven_retained_h1_never_promoted_and_ordinary_sets_ignore_it(self):
        market.ingest(self.db, payload(native=False), NOW)
        day = NOW // 86400 * 86400
        bad_envelopes = [[bar(NOW - 3600)], dict(source=dict(NATIVE_H1_SOURCE, source='Other'), bars=[]),
                         dict(source=NATIVE_H1_SOURCE, bars=[bar(NOW - 3600)]),
                         dict(source=NATIVE_H1_SOURCE, bars=[dict(bar(NOW - 3600), captured_at=NOW - 1)]),
                         dict(source=NATIVE_H1_SOURCE, bars=[dict(bar(NOW - 86400), captured_at=NOW)]),
                         {'unknown': 'not OHLC'}]
        for envelope in bad_envelopes:
            self.conn.execute('INSERT OR REPLACE INTO gbop_market_history VALUES (?,?,?,?,?)',
                              ('NAS100', 'USTECm', 3600, day, json.dumps(envelope)))
            self.assertEqual(self.load(), [])
            feed = market.read_feed(self.db, 'NAS100', NOW)
            sets = market._history_sets(self.db, feed, NOW - 86400, NOW)
            self.assertEqual(set(sets), {60, 300})
            self.assertEqual(sets[60], [bar(NOW - 60)])
            self.assertEqual(sets[300], [bar(NOW - 300)])

    def test_unproven_native_snapshot_fails_closed_without_db(self):
        market.ingest(self.db, payload(), NOW)
        good = market.read_feed(self.db, 'NAS100', NOW)
        for change in ({'native_h1_source': None}, {'captured_at_utc': None},
                       {'captured_at_utc': '2026-10-06T08:00:00'},
                       {'bars_h1': [bar(NOW - 3600)] * 2},
                       {'bars_h1': [bar(NOW)]}, {'bars_h1': None}):
            self.assertEqual(market.history_native_h1(None, dict(good, **change), NOW - 7200, NOW), [])

    def test_out_of_order_ingest_does_not_rewrite_native_history(self):
        market.ingest(self.db, payload(), NOW)
        previous = self.load()
        for captured in (NOW, NOW - 1):
            data = payload(captured)
            data['instruments'][0]['bars_h1'] = [dict(bar(NOW - 7200), high=999)]
            market.ingest(self.db, data, NOW)
            self.assertEqual(self.load(), previous)

    def test_same_symbol_native_correction_keeps_latest_accepted_observation(self):
        market.ingest(self.db, payload(), NOW)
        data = payload(NOW + 30)
        data['instruments'][0]['bars_h1'][0]['high'] = 106
        market.ingest(self.db, data, NOW + 30)
        record = self.load()[0]
        self.assertEqual(record['high'], 106)
        self.assertEqual(record['provenance']['captured_at'], NOW + 30)


if __name__ == '__main__':
    unittest.main()
