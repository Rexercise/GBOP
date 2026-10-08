"""Bounded market-only cache, revalidated by the database on every read.

Only unchanged payload bytes stay local. Every call still reads the current
row's fingerprint and metadata, including feed capture/receipt timestamps.
No TTL, cached negative results, journal data, or inferred freshness.
"""
from collections import OrderedDict
import threading

MAX_ENTRIES = 512
MAX_BYTES = 32 * 1024 * 1024
_cache = OrderedDict()
_bytes = 0
_lock = threading.RLock()


def _remember(key, fingerprint, payload):
    global _bytes
    size = len(payload.encode('utf-8'))
    with _lock:
        old = _cache.pop(key, None)
        if old:
            _bytes -= old[2]
        if size <= MAX_BYTES:
            _cache[key] = (fingerprint, payload, size)
            _bytes += size
        while len(_cache) > MAX_ENTRIES or _bytes > MAX_BYTES:
            _, removed = _cache.popitem(last=False)
            _bytes -= removed[2]


def _read(conn, namespace, table, identity):
    key = (namespace, table, *identity)
    with _lock:
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
    fingerprint = cached[0] if cached else None
    if table == 'gbop_market_history':
        sql = '''SELECT md5(payload) AS payload_fingerprint,
            CASE WHEN md5(payload)=? THEN NULL ELSE payload END AS payload
            FROM gbop_market_history WHERE asset=? AND symbol=? AND step=? AND day_utc=?'''
    elif table == 'gbop_market_feed':
        sql = '''SELECT asset,captured_at,received_at,md5(payload) AS payload_fingerprint,
            CASE WHEN md5(payload)=? THEN NULL ELSE payload END AS payload
            FROM gbop_market_feed WHERE asset=?'''
    else:
        raise ValueError('Only market payloads may use this cache.')
    row = conn.execute(sql, (fingerprint, *identity)).fetchone()
    if row is None:
        # Absence is returned immediately; a cached record cannot resurrect it.
        return None
    result = dict(row)
    current_fingerprint = result.pop('payload_fingerprint')
    payload = result['payload']
    if payload is None:
        # Use the exact snapshot sent with this query, even if another thread
        # replaced or evicted the shared cache while the database was reading.
        if cached is None or cached[0] != current_fingerprint:
            raise RuntimeError('Market payload validation did not match the read.')
        payload = cached[1]
    _remember(key, current_fingerprint, payload)
    result['payload'] = payload
    return result


def read_history(conn, namespace, identity):
    return _read(conn, namespace, 'gbop_market_history', identity)


def read_feed(conn, namespace, asset):
    return _read(conn, namespace, 'gbop_market_feed', (asset,))
