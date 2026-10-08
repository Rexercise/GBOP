"""Database-validated market caching, with synthetic SQLite payloads only."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market, market_payload_cache as cache
import test_market_bridge as fixture


class MarketPayloadCacheTests(unittest.TestCase):
    def setUp(self):
        fixture.MarketTests.setUp(self)
        self.conn.create_function('md5', 1, lambda value: hashlib.md5(value.encode()).hexdigest())
        self.transferred = []
        self.identity = ('NAS100', 'USTECm', 300, self.now // 86400 * 86400)
        self.namespace = object()
        parent = self
        class Adapter:
            def execute(_, sql, params=()):
                cursor = parent.conn.execute(sql, params)
                if 'payload_fingerprint' not in sql:
                    return cursor
                class Cursor:
                    def fetchone(_):
                        row = cursor.fetchone()
                        parent.transferred.append(len((row['payload'] or '').encode()) if row else 0)
                        return row
                return Cursor()
            def market_history_row(adapter, identity):
                return cache.read_history(adapter, parent.namespace, identity)
            def market_feed_row(adapter, asset):
                return cache.read_feed(adapter, parent.namespace, asset)
        self.adapter = Adapter()
        @contextmanager
        def db():
            with self.conn:
                yield self.adapter
        self.cached_db = db

    tearDown = fixture.MarketTests.tearDown

    def insert(self, payload):
        self.conn.execute('INSERT INTO gbop_market_history VALUES(?,?,?,?,?)', (*self.identity, payload))

    def test_identical_history_returns_identical_bytes_without_retransfer(self):
        payload = json.dumps([{'time': self.now-300, 'notes': 'x'*100000}])
        self.insert(payload)
        for _ in range(5):
            self.assertEqual(self.adapter.market_history_row(self.identity)['payload'], payload)
        self.assertEqual(self.transferred, [len(payload), 0, 0, 0, 0])

    def test_external_correction_is_visible_on_the_very_next_read(self):
        self.insert('[{"time":1,"close":100}]')
        self.adapter.market_history_row(self.identity)
        corrected = '[{"time":1,"close":101}]'
        self.conn.execute('UPDATE gbop_market_history SET payload=?', (corrected,))
        self.assertEqual(self.adapter.market_history_row(self.identity)['payload'], corrected)
        self.assertEqual(self.transferred[-1], len(corrected))

    def test_delete_and_recreate_never_serves_a_cached_missing_row(self):
        self.insert('[]')
        self.adapter.market_history_row(self.identity)
        self.conn.execute('DELETE FROM gbop_market_history')
        self.assertIsNone(self.adapter.market_history_row(self.identity))
        self.insert('[{"time":2}]')
        self.assertEqual(self.adapter.market_history_row(self.identity)['payload'], '[{"time":2}]')

    def test_feed_cache_keeps_tick_and_capture_freshness_checks(self):
        market.ingest(self.cached_db, self.payload, self.now)
        self.assertTrue(market.read_feed(self.cached_db, 'NAS', self.now)['is_live'])
        stale = market.read_feed(self.cached_db, 'NAS', self.now+121)
        self.assertFalse(stale['is_live'])
        self.assertEqual(stale['capture_age_seconds'], 121)
        self.assertEqual(self.transferred[-1], 0)
        self.payload['captured_at'] += 200
        market.ingest(self.cached_db, self.payload, self.now+200)
        updated = market.read_feed(self.cached_db, 'NAS', self.now+200)
        self.assertEqual(updated['capture_age_seconds'], 0)
        self.assertFalse(updated['is_live'])  # An unchanged old tick is still stale.

    def test_ingest_cache_matches_uncached_history_and_preserves_corrections(self):
        self.payload['instruments'][0]['bars'] = fixture.MarketTests.bars(self)
        versions = [deepcopy(self.payload)]
        second = deepcopy(self.payload)
        second['captured_at'] += 30
        second['instruments'][0]['bars'][-1]['close'] += 1
        versions.append(second)
        third = deepcopy(second)
        third['captured_at'] += 30
        versions.append(third)
        snapshots = []
        for db in (self.db, self.cached_db):
            self.conn.execute('DELETE FROM gbop_market_history')
            self.conn.execute('DELETE FROM gbop_market_feed')
            for value in versions:
                market.ingest(db, value, value['captured_at'])
            snapshots.append([tuple(r) for r in self.conn.execute('SELECT * FROM gbop_market_history ORDER BY day_utc')])
        self.assertEqual(*snapshots)

    def test_eviction_during_query_uses_the_exact_validated_snapshot(self):
        self.insert('[]')
        self.adapter.market_history_row(self.identity)
        original = self.adapter.execute
        def execute(sql, params=()):
            cursor = original(sql, params)
            with cache._lock:
                cache._cache.clear()
                cache._bytes = 0
            return cursor
        with patch.object(self.adapter, 'execute', side_effect=execute):
            self.assertEqual(self.adapter.market_history_row(self.identity)['payload'], '[]')

    def test_cache_is_bounded_and_oversized_rows_still_read_losslessly(self):
        with cache._lock:
            cache._cache.clear()
            cache._bytes = 0
        with patch.object(cache, 'MAX_BYTES', 10), patch.object(cache, 'MAX_ENTRIES', 2):
            for i in range(4):
                cache._remember((self.namespace, i), str(i), 'xx')
            self.assertLessEqual(cache._bytes, 10)
            self.assertLessEqual(len(cache._cache), 2)
            payload = '[{"time":1,"notes":"larger than cache"}]'
            self.insert(payload)
            for _ in range(2):
                self.assertEqual(self.adapter.market_history_row(self.identity)['payload'], payload)
            self.assertEqual(self.transferred[-2:], [len(payload), len(payload)])

    def test_database_failure_is_not_hidden_by_cached_market_data(self):
        self.insert('[]')
        self.adapter.market_history_row(self.identity)
        with patch.object(self.adapter, 'execute', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                self.adapter.market_history_row(self.identity)
