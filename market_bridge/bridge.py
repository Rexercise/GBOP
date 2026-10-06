"""MT5 -> GBOP outbound-only collector. Never calls trading/account APIs."""
from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from numbers import Integral, Real
from pathlib import Path
import ssl
import time
from urllib.request import Request
from urllib.parse import urlparse
import certifi

ROOT = Path(__file__).resolve().parent
ASSETS = {'NAS100', 'SPX', 'US30', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 'EURUSD', 'WTI'}
MAX_CAPTURE_AGE_SECONDS = 180  # Receiver's existing capture-age allowance.
MIN_WEEK_SECONDS = 6 * 86400
MAX_WEEK_SECONDS = 8 * 86400
NATIVE_H1_SOURCE = dict(source='MT5', timeframe='H1', method='copy_rates_from_pos')
MAX_H1_BARS = 14 * 24


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    url = urlparse(config['endpoint'])
    if not url.hostname or url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.path != '/api/market/ingest':
        raise ValueError('Use the HTTPS GBOP /api/market/ingest endpoint.')
    token = config.get('token', '')
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Bridge token must contain at least 32 characters.')
    symbols = config.get('symbols', {})
    if not isinstance(symbols, dict) or not 1 <= len(symbols) <= len(ASSETS):
        raise ValueError('Configure 1–9 exact broker symbols, including SPX only with its verified broker mapping.')
    for asset, symbol in symbols.items():
        if asset not in ASSETS:
            raise ValueError('Use canonical GBOP asset names in symbols.')
        if not isinstance(symbol, str) or not 1 <= len(symbol) <= 40 or not all(c.isalnum() or c in '._-#' for c in symbol):
            raise ValueError('Configure exact broker symbol names.')
    return config


def collect_weekly_periods(mt5, symbol, captured):
    """Use only observed consecutive W1 opens; the newest bar has no known end."""
    try:
        timeframe = getattr(mt5, 'TIMEFRAME_W1', None)
        if timeframe is None:
            return None
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 3)
        if rates is None or not 2 <= len(rates) <= 3:
            return None
        opens = []
        for rate in rates:
            value = rate['time']
            # MT5 returns numpy integer epochs. Preserve the exact UTC value;
            # never truncate a float or apply a guessed broker-time offset.
            if isinstance(value, bool) or not isinstance(value, Integral) or not 0 < value <= captured:
                raise ValueError('Invalid W1 source timestamp.')
            opens.append(int(value))
        opens.sort()
        if len(set(opens)) != len(opens):
            raise ValueError('Duplicate W1 source timestamp.')
        periods = []
        for start, end in zip(opens, opens[1:]):
            if not MIN_WEEK_SECONDS <= end - start <= MAX_WEEK_SECONDS:
                raise ValueError('Nonweekly source interval.')
            periods.append(dict(open_time=start, close_time=end, source='MT5', timeframe='W1'))
        return periods
    except Exception as exc:
        # W1 is optional evidence. Its absence or failure must not interrupt the
        # established quote/M1/M5 feed; no fabricated calendar is substituted.
        logging.warning('W1 boundaries unavailable for %s (%s); retaining M1/M5 coverage', symbol, type(exc).__name__)
        return None


def collect_native_h1(mt5, symbol, captured, *, backfill=True):
    """Read bounded native H1 OHLC; never synthesize it from finer candles."""
    try:
        timeframe = getattr(mt5, 'TIMEFRAME_H1', None)
        if timeframe is None:
            return None
        # Include position zero, which may be closed between broker sessions.
        # One extra position allows for the currently forming hourly candle.
        count = MAX_H1_BARS + 1 if backfill else 3
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or not len(rates):
            return None
        if len(rates) > count:
            raise ValueError('Unexpected H1 source size.')
        cutoff = captured - 14 * 86400 + MAX_CAPTURE_AGE_SECONDS
        bars = []
        for rate in rates:
            t = rate['time']
            if isinstance(t, bool) or not isinstance(t, Integral) or t <= 0 or t % 3600:
                raise ValueError('Invalid H1 UTC source timestamp.')
            if t < cutoff or t + 3600 > captured:
                continue
            prices = [rate[k] for k in ('open', 'high', 'low', 'close')]
            if any(isinstance(p, bool) or not isinstance(p, Real) or not math.isfinite(p) or p <= 0 for p in prices):
                raise ValueError('Invalid H1 source price.')
            o, h, l, c = map(float, prices)
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('Invalid H1 source OHLC.')
            bars.append(dict(time=int(t), open=o, high=h, low=l, close=c))
        bars.sort(key=lambda bar: bar['time'])
        if len(bars) > MAX_H1_BARS or len({b['time'] for b in bars}) != len(bars):
            raise ValueError('Duplicate or excessive H1 source candles.')
        return bars or None
    except Exception as exc:
        # Optional evidence must not interrupt the established quote/M1/M5 feed.
        logging.warning('Native H1 unavailable for %s (%s); retaining M1/M5 coverage', symbol, type(exc).__name__)
        return None


def collect(mt5, symbols, now=None, *, backfill=True):
    now = int(time.time() if now is None else now)
    # Keep the oldest bars valid for the full allowed upload age, including splits.
    history_cutoff = now - 14 * 86400 + MAX_CAPTURE_AGE_SECONDS
    info = mt5.terminal_info()
    if info is None or not info.connected:
        raise RuntimeError('MT5 is disconnected. Sign in to the terminal with investor access.')
    instruments = []
    for asset, symbol in symbols.items():
        if not mt5.symbol_select(symbol, True):
            logging.warning('Symbol unavailable: %s (%s)', asset, symbol)
            continue
        tick = mt5.symbol_info_tick(symbol)
        # Position zero can already be closed when the broker is between
        # sessions. Select it, then prove closure by timestamp below.
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 4032 if backfill else 24)
        if tick is None or rates is None or tick.bid <= 0 or tick.ask <= 0:
            logging.warning('No usable market data: %s', asset)
            continue
        # Do not shift server timestamps speculatively; receiver rejects future data.
        bars = [dict(time=int(r['time']), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')})
                for r in rates if history_cutoff <= int(r['time']) and int(r['time']) + 300 <= now]
        bars.sort(key=lambda b: b['time'])
        rates_m1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 20160 if backfill else 120)
        bars_m1 = [] if rates_m1 is None else [
            dict(time=int(r['time']), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')})
            for r in rates_m1 if history_cutoff <= int(r['time']) and int(r['time']) + 60 <= now]
        bars_m1.sort(key=lambda b: b['time'])
        if not bars_m1:
            logging.warning('M1 history unavailable for %s; retaining M5 coverage', asset)
        item = dict(asset=asset, symbol=symbol, bid=float(tick.bid), ask=float(tick.ask), tick_time=int(tick.time), bars=bars, bars_m1=bars_m1)
        bars_h1 = collect_native_h1(mt5, symbol, now, backfill=backfill)
        if bars_h1 is not None:
            item['bars_h1'] = bars_h1
            item['native_h1_source'] = dict(NATIVE_H1_SOURCE)
        weekly_periods = collect_weekly_periods(mt5, symbol, now)
        if weekly_periods is not None:
            item['weekly_periods'] = weekly_periods
        instruments.append(item)
    if not instruments:
        raise RuntimeError('No configured symbols have usable quotes.')
    return dict(captured_at=now, instruments=instruments)


def send(config, payload):
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > 4_000_000:
        items = payload['instruments']
        if len(items) < 2:
            raise ValueError('Single instrument exceeds receiver payload limit.')
        midpoint = len(items) // 2
        accepted = []
        for part in (items[:midpoint], items[midpoint:]):
            result = send(config, dict(captured_at=payload['captured_at'], instruments=part))
            accepted.extend(result['accepted_assets'])
        return dict(ok=True, accepted_assets=accepted, captured_at=payload['captured_at'])
    request = Request(config['endpoint'], data=encoded,
                      headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'}, method='POST')
    # Reject redirects so a moved endpoint cannot receive the bridge credential.
    from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    # Fresh Windows hosts may not have populated their system CA store yet.
    # Use Mozilla's CA bundle while retaining certificate and hostname checks.
    context = ssl.create_default_context(cafile=certifi.where())
    with build_opener(NoRedirect(), HTTPSHandler(context=context)).open(request, timeout=60) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Bridge receiver did not acknowledge capture.')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[
        RotatingFileHandler(ROOT / 'bridge.log', maxBytes=1_000_000, backupCount=2), logging.StreamHandler()])
    config = load_config(args.config)
    import MetaTrader5 as mt5
    delay = 30
    last_backfill = 0
    while True:
        try:
            path = config.get('terminal_path')
            if not (mt5.initialize(path) if path else mt5.initialize()):
                raise RuntimeError('MT5 initialization failed.')
            backfill = time.monotonic() - last_backfill >= 3600 or last_backfill == 0
            result = send(config, collect(mt5, config['symbols'], backfill=backfill))
            if backfill:
                last_backfill = time.monotonic()
            logging.info('Feed acknowledged: %s', ', '.join(result['accepted_assets']))
            delay = 30
        except Exception as exc:
            # Never dump configuration, headers, or broker credentials to logs.
            logging.error('Capture/upload failed (%s). Check terminal connection, symbol mappings and receiver.', type(exc).__name__)
            delay = min(delay * 2, 300)
            if args.once:
                raise SystemExit(1)
        finally:
            mt5.shutdown()
        if args.once:
            return
        time.sleep(delay)


if __name__ == '__main__':
    main()
