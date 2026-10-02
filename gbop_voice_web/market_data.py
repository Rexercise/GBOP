"""Read-only broker market context. No account data or trading operations."""
from __future__ import annotations
import hmac
import json
import math
import os
import time
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from gbop_voice_web.trade_photos import schema

NY = ZoneInfo('America/New_York')
ASSETS = {'NAS100', 'US30', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 'EURUSD', 'WTI'}
ALIASES = {'NAS': 'NAS100', 'USTEC': 'NAS100', 'NASDAQ': 'NAS100', 'DJI': 'US30',
           'GOLD': 'XAUUSD', 'SILVER': 'XAGUSD', 'OIL': 'WTI', 'USOIL': 'WTI',
           'BTC': 'BTCUSD', 'ETH': 'ETHUSD'}
MAX_BYTES = 2_000_000
CREATE_SQL = '''CREATE TABLE IF NOT EXISTS gbop_market_feed (
    asset TEXT PRIMARY KEY, captured_at BIGINT NOT NULL, received_at BIGINT NOT NULL,
    payload TEXT NOT NULL)'''


def init_market(db):
    with db() as conn:
        # Both web and Discord start together; serialize schema creation.
        conn.execute('SELECT pg_advisory_xact_lock(739204714)')
        conn.execute(CREATE_SQL)
        conn.execute('ALTER TABLE gbop_market_feed ENABLE ROW LEVEL SECURITY')
        conn.execute('REVOKE ALL ON gbop_market_feed FROM anon, authenticated')
        conn.execute('GRANT ALL ON gbop_market_feed TO service_role')


def asset_name(value):
    value = str(value or '').strip().upper()
    value = ALIASES.get(value, value)
    if value not in ASSETS:
        raise ValueError('Supported assets: ' + ', '.join(sorted(ASSETS)))
    return value


def authorized(header):
    secret = os.getenv('GBOP_MARKET_BRIDGE_TOKEN', '')
    return bool(len(secret) >= 32 and isinstance(header, str) and header.isascii() and secret.isascii() and hmac.compare_digest(header, 'Bearer ' + secret))


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Prices and timestamps must be finite positive numbers.')
    return value


def epoch(value):
    value = number(value)
    if int(value) != value:
        raise ValueError('Timestamps must be integer UTC seconds.')
    return int(value)


def validate_payload(data, now=None):
    now = int(time.time() if now is None else now)
    if not isinstance(data, dict) or set(data) != {'captured_at', 'instruments'}:
        raise ValueError('Expected captured_at and instruments only.')
    captured = epoch(data['captured_at'])
    if not now - 180 <= captured <= now + 30:
        raise ValueError('Bridge clock or capture is stale. Synchronize Windows time.')
    instruments = data['instruments']
    if not isinstance(instruments, list) or not 1 <= len(instruments) <= len(ASSETS):
        raise ValueError('Expected 1–8 instruments.')
    clean, seen = [], set()
    for item in instruments:
        if not isinstance(item, dict) or set(item) != {'asset', 'symbol', 'bid', 'ask', 'tick_time', 'bars'}:
            raise ValueError('Unexpected instrument fields.')
        asset = asset_name(item['asset'])
        if asset in seen:
            raise ValueError('Duplicate asset.')
        seen.add(asset)
        symbol = item['symbol']
        if not isinstance(symbol, str) or not 1 <= len(symbol) <= 40 or not all(c.isalnum() or c in '._-#' for c in symbol):
            raise ValueError('Invalid broker symbol.')
        bid, ask, tick = number(item['bid']), number(item['ask']), epoch(item['tick_time'])
        if ask < bid or tick > captured + 30:
            raise ValueError('Invalid spread or future tick; check broker timestamp semantics.')
        bars = item['bars']
        if not isinstance(bars, list) or len(bars) > 2304:
            raise ValueError('At most 2304 closed M5 bars allowed.')
        normalized, last = [], 0
        for bar in bars:
            if not isinstance(bar, dict) or set(bar) != {'time', 'open', 'high', 'low', 'close'}:
                raise ValueError('Invalid candle fields.')
            t = epoch(bar['time'])
            o, h, l, c = [number(bar[k]) for k in ('open', 'high', 'low', 'close')]
            if t % 300 or t <= last or t + 300 > captured or t < now - 14 * 86400:
                raise ValueError('Candles must be sorted, unique, closed M5 UTC bars within 14 days.')
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('Invalid candle OHLC.')
            normalized.append(dict(time=t, open=o, high=h, low=l, close=c))
            last = t
        clean.append(dict(asset=asset, symbol=symbol, bid=bid, ask=ask, tick_time=tick, bars=normalized))
    return captured, clean


def ingest(db, data, now=None):
    now = int(time.time() if now is None else now)
    captured, items = validate_payload(data, now)
    with db() as conn:
        for item in items:
            conn.execute('''INSERT INTO gbop_market_feed(asset,captured_at,received_at,payload)
                VALUES (?,?,?,?) ON CONFLICT(asset) DO UPDATE SET
                captured_at=excluded.captured_at,received_at=excluded.received_at,payload=excluded.payload
                WHERE excluded.captured_at > gbop_market_feed.captured_at''',
                (item['asset'], captured, now, json.dumps(item, separators=(',', ':'))))
    return {'ok': True, 'accepted_assets': [i['asset'] for i in items], 'captured_at': captured}


def read_feed(db, asset, now=None):
    now = int(time.time() if now is None else now)
    asset = asset_name(asset)
    with db() as conn:
        row = conn.execute('SELECT * FROM gbop_market_feed WHERE asset=?', (asset,)).fetchone()
    if row is None:
        return {'ok': False, 'asset': asset, 'status': 'not_connected', 'message': 'No broker data received for this asset.'}
    payload = json.loads(row['payload'])
    tick_age = now - payload['tick_time']
    capture_age = now - row['captured_at']
    fresh = 0 <= tick_age <= 120 and 0 <= capture_age <= 120
    return {'ok': True, 'asset': asset, 'source': 'MT5 broker feed', 'symbol': payload['symbol'],
            'status': 'fresh' if fresh else 'stale', 'is_live': fresh,
            'bid': payload['bid'], 'ask': payload['ask'], 'tick_time_utc': datetime.fromtimestamp(payload['tick_time'], timezone.utc).isoformat(),
            'tick_age_seconds': tick_age, 'capture_age_seconds': capture_age,
            'received_at_utc': datetime.fromtimestamp(row['received_at'], timezone.utc).isoformat(),
            'bars': payload['bars']}


def session_review(bars, day, shift):
    """Conservative H1 range observations, never inferred CSD/entry confirmation."""
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    day = date.fromisoformat(day)
    base = 0 if shift == 'day' else 12
    def hour(h):
        start = int(datetime(day.year, day.month, day.day, h, tzinfo=NY).timestamp())
        subset = [b for b in bars if start <= b['time'] < start + 3600]
        if [b['time'] for b in subset] != list(range(start, start + 3600, 300)):
            return None
        return dict(time=start, open=subset[0]['open'], high=max(b['high'] for b in subset),
                    low=min(b['low'] for b in subset), close=subset[-1]['close'])
    nine = hour(base + 9)
    results = []
    for play, anchor_h in [('9ate8', base + 8), ('Young Lefty', base + 7)]:
        anchor = hour(anchor_h)
        result = {'play': play, 'anchor_hour_ny': anchor_h, 'execution_hour_ny': base + 9,
                  'status': 'insufficient_closed_candles', 'entry_confirmed': False}
        if anchor and nine:
            high, low = anchor['high'], anchor['low']
            above, below = nine['high'] > high, nine['low'] < low
            outside = nine['close'] > high or nine['close'] < low
            # Young Lefty cannot survive an intervening 8 o'clock close outside 7.
            intervening = hour(base + 8) if play == 'Young Lefty' else anchor
            if intervening is None:
                results.append(result)
                continue
            invalid_before = intervening['close'] > high or intervening['close'] < low
            if outside or invalid_before:
                status = 'invalidated_by_hourly_close'
            elif above and below:
                status = 'both_sides_swept_order_unknown'
            elif above or below:
                status = 'range_sweep_candidate'
            else:
                status = 'no_9_oclock_sweep'
            result.update(status=status, anchor_high=high, anchor_low=low, midpoint=(high + low) / 2,
                          swept_buy_side=above, swept_sell_side=below,
                          direction=('bearish' if above else 'bullish') if above != below and status == 'range_sweep_candidate' else None,
                          primary_target=(low if above else high) if above != below and status == 'range_sweep_candidate' else None,
                          nine_close=nine['close'])
            # Follow subsequent full hours only; do not call a target hit or a confirmed entry.
            if status == 'range_sweep_candidate':
                for h in (base + 10, base + 11):
                    later = hour(h)
                    if later is None:
                        break
                    if later['close'] > high or later['close'] < low:
                        result['status'] = 'invalidated_by_hourly_close'
                        result['invalidating_hour_ny'] = h
                        result['direction'] = None
                        result['primary_target'] = None
                        break
        results.append(result)
    return {'date_ny': day.isoformat(), 'shift': shift, 'timezone': 'America/New_York', 'observations': results,
            'limits': 'Closed complete M5 candles aggregated to H1. Range observations only: no CSD, Super Soup, Blessed Thief execution, SMT, or CRT variant confirmation. Missing/unfinished hours are not evidence of no setup.'}


MARKET_TOOLS = [
    schema('get_market_price', 'Get latest broker bid/ask with freshness and timestamp. Always disclose stale or absent data.', {'asset': {'type': 'string'}}),
    schema('review_market_session', 'Review closed candles for 9ate8 and Young Lefty range-sweep candidates. Does not confirm an entry or executed trade. Dates and shifts use New York time.', {
        'asset': {'type': 'string'}, 'date_ny': {'type': ['string', 'null']},
        'shift': {'type': 'string', 'enum': ['day', 'night']}}),
]
MARKET_NAMES = {t['name'] for t in MARKET_TOOLS}
MARKET_PROMPT = '''
# BROKER MARKET CONTEXT
Use get_market_price for current prices and review_market_session for observed
9ate8/Young Lefty ranges. Only status=fresh may be described as a current quote;
include broker symbol and timestamp. Stale data may mean a disconnected bridge
or a closed market; do not guess which. With not_connected, say no feed yet.
Session candidates are observations, never confirmed CSD, Super Soup, SMT,
Blessed Thief entries, signals, or evidence that a member executed a trade.
Do not invent other play detection. A closure outside the anchor invalidates it.
Use New York time with DST. These tools never place/manage/close broker orders.
Do not mark member progress, target completion, stops, or exits automatically.
'''.strip()


def market_tool(db, name, args):
    try:
        result = read_feed(db, args.get('asset'))
        bars = result.pop('bars', [])
        if name == 'review_market_session' and result['ok']:
            result['review'] = session_review(bars, args.get('date_ny') or datetime.now(NY).date().isoformat(), args.get('shift', 'day'))
        elif name != 'get_market_price' and name != 'review_market_session':
            return {'ok': False, 'error': 'Unknown market tool.'}
        return result
    except (TypeError, ValueError) as exc:
        return {'ok': False, 'error': str(exc)}
