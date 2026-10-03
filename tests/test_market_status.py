"""Synthetic snapshots only: no broker, network, account or trade operations."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_status import broker_session_status, feed_health
from gbop_voice_web.voice_runtime import compact_voice_tool_result


class FeedHealthTests(unittest.TestCase):
    def test_snapshot_and_quote_have_independent_freshness(self):
        current = feed_health(0, 0)
        self.assertEqual(current['status'], 'recent_snapshot_and_quote')
        self.assertEqual(feed_health(120, 120)['status'], current['status'])
        delayed = feed_health(0, 121)
        self.assertEqual(delayed['status'], 'recent_snapshot_stale_quote')
        self.assertEqual(delayed['capture_status'], 'fresh')
        self.assertEqual(delayed['quote_status'], 'stale')
        self.assertIn('does not prove', delayed['message'])
        self.assertEqual(feed_health(121, 121)['status'], 'stale_snapshot')

    def test_future_or_absent_timestamps_do_not_claim_live_or_closed(self):
        self.assertEqual(feed_health()['status'], 'no_snapshot')
        for capture, tick in [(-1, 0), (0, -1), (-1, -1)]:
            self.assertEqual(feed_health(capture, tick)['status'], 'timestamp_ahead')

    def test_session_evidence_is_explicitly_absent_and_not_mutable_shared_state(self):
        result = broker_session_status()
        self.assertEqual(result, {'status': 'unknown', 'source': None,
                                 'closure_reason': None, 'reason': 'no_verified_session_calendar'})
        result['status'] = 'closed'
        self.assertEqual(broker_session_status()['status'], 'unknown')


class MarketSessionContractTests(unittest.TestCase):
    def setUp(self):
        self.now = parse_time('2026-10-03T10:00:00+00:00')  # Saturday
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        self.db = lambda: self.conn
        self.payload = {'captured_at': self.now, 'instruments': [dict(
            asset='SPX', symbol='US500m', bid=100, ask=101,
            tick_time=self.now-86400, bars=[])]}

    def tearDown(self):
        self.conn.close()

    def assertUnknownSession(self, result):
        self.assertEqual(result['broker_session']['status'], 'unknown')
        self.assertIsNone(result['broker_session']['source'])
        self.assertIsNone(result['broker_session']['closure_reason'])

    def test_existing_collector_contract_needs_no_update_and_keeps_quote_stale(self):
        market.ingest(self.db, self.payload, self.now)
        result = market.read_feed(self.db, 'SPX', self.now)
        self.assertEqual(result['symbol'], 'US500m')
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['is_live'])
        self.assertEqual(result['feed_health']['status'], 'recent_snapshot_stale_quote')
        self.assertUnknownSession(result)
        stored = json.loads(self.conn.execute('SELECT payload FROM gbop_market_feed').fetchone()[0])
        self.assertNotIn('broker_session', stored)

    def test_no_snapshot_does_not_assert_a_broken_connection_or_closed_market(self):
        result = market.read_feed(self.db, 'SPX', self.now)
        self.assertFalse(result['ok'])
        self.assertEqual(result['feed_health']['status'], 'no_snapshot')
        self.assertUnknownSession(result)

    def test_recent_delivery_does_not_freshen_an_old_capture(self):
        self.payload['captured_at'] -= 121
        market.ingest(self.db, self.payload, self.now)
        result = market.read_feed(self.db, 'SPX', self.now)
        self.assertEqual(result['feed_health']['status'], 'stale_snapshot')
        self.assertFalse(result['is_live'])
        self.assertUnknownSession(result)

    def test_any_asset_or_weekday_keeps_closure_unverified(self):
        for asset, symbol in [('SPX', 'US500m'), ('BTCUSD', 'BTCUSDm'), ('EURUSD', 'EURUSDm')]:
            for offset in (0, 2*86400):
                payload = copy.deepcopy(self.payload)
                payload['captured_at'] += offset
                payload['instruments'][0].update(asset=asset, symbol=symbol)
                market.ingest(self.db, payload, self.now+offset)
                self.assertUnknownSession(market.read_feed(self.db, asset, self.now+offset))

    def test_fresh_ticks_do_not_invent_a_dated_session_calendar(self):
        self.payload['instruments'][0]['tick_time'] = self.now
        market.ingest(self.db, self.payload, self.now)
        result = market.read_feed(self.db, 'SPX', self.now)
        self.assertTrue(result['is_live'])
        self.assertEqual(result['feed_health']['status'], 'recent_snapshot_and_quote')
        self.assertUnknownSession(result)

    def test_ingestion_does_not_accept_unverified_calendar_or_trade_mode_claims(self):
        for field, value in [('broker_session', {'status': 'closed'}),
                             ('trade_mode', 0), ('market_closed', True)]:
            payload = copy.deepcopy(self.payload)
            payload['instruments'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'Unexpected instrument fields'):
                market.validate_payload(payload, self.now)

    def test_price_and_unavailable_shift_routes_preserve_provenance_in_voice(self):
        market.ingest(self.db, self.payload, self.now)
        routes = [
            ('get_market_price', {'asset': 'SPX'}),
            ('list_market_shifts', {'asset': 'SPX', 'date_ny': '2026-10-02'}),
            ('review_market_session', {'asset': 'SPX', 'date_ny': '2026-10-02', 'shift': 'night'}),
            ('review_market_session', {'asset': 'SPX', 'date_ny': None, 'shift': 'day'}),
        ]
        with patch.object(market.time, 'time', return_value=self.now):
            for name, args in routes:
                with self.subTest(route=name, args=args):
                    result = market.market_tool(self.db, name, args, self.now)
                    self.assertUnknownSession(result)
                    self.assertUnknownSession(compact_voice_tool_result(name, result))
        self.assertEqual(market.read_feed(self.db, 'SPX', self.now)['status'], 'stale')


if __name__ == '__main__':
    unittest.main()
