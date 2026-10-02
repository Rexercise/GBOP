"""MT5 -> GBOP outbound-only collector. Never calls trading/account APIs."""
from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from urllib.request import Request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ASSETS = {'NAS100', 'US30', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 'EURUSD', 'WTI'}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    url = urlparse(config['endpoint'])
    if not url.hostname or url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.path != '/api/market/ingest':
        raise ValueError('Use the HTTPS GBOP /api/market/ingest endpoint.')
    token = config.get('token', '')
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Bridge token must contain at least 32 characters.')
    symbols = config.get('symbols', {})
    if not isinstance(symbols, dict) or not 1 <= len(symbols) <= 8:
        raise ValueError('Configure 1–8 exact broker symbols.')
    for asset, symbol in symbols.items():
        if asset not in ASSETS:
            raise ValueError('Use canonical GBOP asset names in symbols.')
        if not isinstance(symbol, str) or not 1 <= len(symbol) <= 40 or not all(c.isalnum() or c in '._-#' for c in symbol):
            raise ValueError('Configure exact broker symbol names.')
    return config


def collect(mt5, symbols, now=None):
    now = int(time.time() if now is None else now)
    info = mt5.terminal_info()
    if info is None or not info.connected:
        raise RuntimeError('MT5 is disconnected. Sign in to the terminal with investor access.')
    instruments = []
    for asset, symbol in symbols.items():
        if not mt5.symbol_select(symbol, True):
            logging.warning('Symbol unavailable: %s (%s)', asset, symbol)
            continue
        tick = mt5.symbol_info_tick(symbol)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 2304)
        if tick is None or rates is None or tick.bid <= 0 or tick.ask <= 0:
            logging.warning('No usable market data: %s', asset)
            continue
        # Do not shift server timestamps speculatively; receiver rejects future data.
        bars = [dict(time=int(r['time']), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')})
                for r in rates if now - 14 * 86400 <= int(r['time']) and int(r['time']) + 300 <= now]
        bars.sort(key=lambda b: b['time'])
        instruments.append(dict(asset=asset, symbol=symbol, bid=float(tick.bid), ask=float(tick.ask), tick_time=int(tick.time), bars=bars))
    if not instruments:
        raise RuntimeError('No configured symbols have usable quotes.')
    return dict(captured_at=now, instruments=instruments)


def send(config, payload):
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > 2_000_000:
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
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
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
    while True:
        try:
            path = config.get('terminal_path')
            if not (mt5.initialize(path) if path else mt5.initialize()):
                raise RuntimeError('MT5 initialization failed.')
            result = send(config, collect(mt5, config['symbols']))
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
