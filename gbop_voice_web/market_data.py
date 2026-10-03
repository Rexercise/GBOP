"""Read-only broker market context. No account data or trading operations."""
from __future__ import annotations
import hmac
import json
import math
import os
import time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
from gbop_voice_web.trade_photos import schema
from gbop_voice_web.shift_review import review_shift
from gbop_voice_web.shift_availability import shift_bounds, assess_shift, choice, alternative_message
from gbop_voice_web.candle_evidence import parse_time, stamp, candle_query, crt_review, summarize, next_boundary
from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.candle_lifecycle import lifecycle_review
from gbop_voice_web.market_context import PAIRINGS, enrich_smt, LIFECYCLE_PROMPT
from gbop_voice_web.market_watch import WATCH_PROMPT
from gbop_voice_web.smt_reference import reconcile_paired_recap
from gbop_voice_web.market_status import broker_session_status, feed_health

NY = ZoneInfo('America/New_York')
ASSETS = {'NAS100', 'SPX', 'US30', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 'EURUSD', 'WTI'}
ALIASES = {'NAS': 'NAS100', 'USTEC': 'NAS100', 'NASDAQ': 'NAS100', 'DJI': 'US30',
           'GOLD': 'XAUUSD', 'SILVER': 'XAGUSD', 'OIL': 'WTI', 'USOIL': 'WTI',
           'BTC': 'BTCUSD', 'ETH': 'ETHUSD', 'US500': 'SPX', 'SP500': 'SPX',
           'SPX500': 'SPX', 'S&P500': 'SPX', 'S&P 500': 'SPX'}
MAX_BYTES = 4_000_000
CREATE_SQL = '''CREATE TABLE IF NOT EXISTS gbop_market_feed (
    asset TEXT PRIMARY KEY, captured_at BIGINT NOT NULL, received_at BIGINT NOT NULL,
    payload TEXT NOT NULL)'''

HISTORY_SQL = """CREATE TABLE IF NOT EXISTS gbop_market_history (
    asset TEXT NOT NULL, symbol TEXT NOT NULL, step INTEGER NOT NULL,
    day_utc BIGINT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(asset, symbol, step, day_utc))"""


def init_market(db):
    with db() as conn:
        # Both web and Discord start together; serialize schema creation.
        conn.execute('SELECT pg_advisory_xact_lock(739204714)')
        conn.execute(CREATE_SQL)
        conn.execute(HISTORY_SQL)
        conn.execute('ALTER TABLE gbop_market_history ENABLE ROW LEVEL SECURITY')
        conn.execute('REVOKE ALL ON gbop_market_history FROM anon, authenticated')
        conn.execute('GRANT ALL ON gbop_market_history TO service_role')
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
        raise ValueError(f'Expected 1–{len(ASSETS)} instruments.')
    clean, seen = [], set()
    for item in instruments:
        if not isinstance(item, dict) or not {'asset', 'symbol', 'bid', 'ask', 'tick_time', 'bars'} <= set(item) or set(item) - {'asset', 'symbol', 'bid', 'ask', 'tick_time', 'bars', 'bars_m1'}:
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
        normalized_sets = {}
        for key, step, maximum in [('bars', 300, 4032), ('bars_m1', 60, 20160)]:
            bars = item.get(key, [])
            if not isinstance(bars, list) or len(bars) > maximum:
                raise ValueError(f'At most {maximum} closed {step}-second bars allowed.')
            normalized, last = [], 0
            for bar in bars:
                if not isinstance(bar, dict) or set(bar) != {'time', 'open', 'high', 'low', 'close'}:
                    raise ValueError('Invalid candle fields.')
                t = epoch(bar['time'])
                o, h, l, c = [number(bar[k]) for k in ('open', 'high', 'low', 'close')]
                if t % step or t <= last or t + step > captured or t < now - 14 * 86400:
                    raise ValueError('Candles must be sorted, unique, closed UTC bars within 14 days.')
                if not l <= min(o, c) <= max(o, c) <= h:
                    raise ValueError('Invalid candle OHLC.')
                normalized.append(dict(time=t, open=o, high=h, low=l, close=c))
                last = t
            normalized_sets[key] = normalized
        clean.append(dict(asset=asset, symbol=symbol, bid=bid, ask=ask, tick_time=tick, **normalized_sets))
    return captured, clean


def ingest(db, data, now=None):
    now = int(time.time() if now is None else now)
    captured, items = validate_payload(data, now)
    with db() as conn:
        for item in items:
            current = conn.execute('SELECT captured_at FROM gbop_market_feed WHERE asset=?', (item['asset'],)).fetchone()
            if current and current['captured_at'] >= captured:
                continue
            for key, step in [('bars', 300), ('bars_m1', 60)]:
                days = {}
                for bar in item.get(key, []):
                    days.setdefault(bar['time'] // 86400 * 86400, []).append(bar)
                for day, incoming in days.items():
                    identity = (item['asset'], item['symbol'], step, day)
                    old = conn.execute('SELECT payload FROM gbop_market_history WHERE asset=? AND symbol=? AND step=? AND day_utc=?', identity).fetchone()
                    merged = {b['time']: b for b in json.loads(old['payload'])} if old else {}
                    merged.update({b['time']: b for b in incoming})
                    payload = json.dumps(sorted(merged.values(), key=lambda b: b['time']), separators=(',', ':'))
                    if not old or old['payload'] != payload:
                        conn.execute("""INSERT INTO gbop_market_history(asset,symbol,step,day_utc,payload)
                            VALUES (?,?,?,?,?) ON CONFLICT(asset,symbol,step,day_utc) DO UPDATE SET payload=excluded.payload""", identity + (payload,))
            conn.execute('''INSERT INTO gbop_market_feed(asset,captured_at,received_at,payload)
                VALUES (?,?,?,?) ON CONFLICT(asset) DO UPDATE SET
                captured_at=excluded.captured_at,received_at=excluded.received_at,payload=excluded.payload
                WHERE excluded.captured_at > gbop_market_feed.captured_at''',
                (item['asset'], captured, now, json.dumps(item, separators=(',', ':'))))
        conn.execute('DELETE FROM gbop_market_history WHERE day_utc < ?', (now - 90 * 86400,))
    return {'ok': True, 'accepted_assets': [i['asset'] for i in items], 'captured_at': captured}


def read_feed(db, asset, now=None):
    now = int(time.time() if now is None else now)
    asset = asset_name(asset)
    with db() as conn:
        row = conn.execute('SELECT * FROM gbop_market_feed WHERE asset=?', (asset,)).fetchone()
    if row is None:
        return {'ok': False, 'asset': asset, 'status': 'not_connected',
                'message': 'No broker data received for this asset.',
                'feed_health': feed_health(), 'broker_session': broker_session_status()}
    payload = json.loads(row['payload'])
    tick_age = now - payload['tick_time']
    capture_age = now - row['captured_at']
    fresh = 0 <= tick_age <= 120 and 0 <= capture_age <= 120
    return {'ok': True, 'asset': asset, 'source': 'MT5 broker feed', 'symbol': payload['symbol'],
            'status': 'fresh' if fresh else 'stale', 'is_live': fresh,
            'feed_health': feed_health(capture_age, tick_age),
            'broker_session': broker_session_status(),
            'bid': payload['bid'], 'ask': payload['ask'], 'tick_time_utc': datetime.fromtimestamp(payload['tick_time'], timezone.utc).isoformat(),
            'tick_age_seconds': tick_age, 'capture_age_seconds': capture_age,
            'received_at_utc': datetime.fromtimestamp(row['received_at'], timezone.utc).isoformat(),
            'bars': payload['bars'], 'bars_m1': payload.get('bars_m1', [])}


def attach_lifecycle(review, bars, end, step):
    """One conversational view; retain raw sequel facts without duplicate verdicts."""
    invalid = review.get('invalidated_at_ny')
    view = lifecycle_review(bars, review['anchor'], review.get('assigned_timeframe', 'M5'),
                            end, step, parse_time(invalid) if invalid else None)
    model = review.get('model1', {})
    model.pop('lifecycle', None)  # authoritative details are in candle_lifecycle
    model.pop('assigned_range_purges', None)
    sequels = {x['model1_candle_open_ny']: x for x in model.get('lifecycles', [])}
    for fact in view.get('purge_candles', []):
        sequel = sequels.get(fact['bar_open_ny']) if fact['purge_type'] == 'body_soup' else None
        if not sequel:
            continue
        fact['next_assigned_candle'] = sequel.get('next_assigned_candle')
        fact['next_assigned_candle_status'] = sequel.get('next_assigned_candle_status')
        sweep = sequel.get('super_soup', {}).get('sweep')
        if sweep:
            # Preserve the observed wick/body sweep even if pre-CSD order/rejection
            # is unverified. The explicit view status controls any confirmation claim.
            fact['subsequent_extreme_sweep'] = {k: sweep[k] for k in (
                'candle', 'purged_level', 'form', 'immediate_next_assigned_candle',
                'close_back_inside_model1', 'close_back_through_swept_extreme') if k in sweep}
            fact['subsequent_extreme_sweep']['ordering_status_ref'] = 'super_soup.status'
        adverse = sequel.get('later_adverse_close_beyond_model1_extreme')
        if adverse:
            fact['later_close_beyond_original_extreme'] = {
                'candle': adverse, 'not_a_member_stop_or_parent_invalidation': True}
    review['candle_lifecycle'] = view
    if 'model1' in review:
        # Core crt_review retains its audit evidence. Market-tool answers get one
        # status authority and immutable identity, not two competing lifecycle trees.
        model.pop('lifecycles', None)
        model.pop('wick_soups', None)
        model['lifecycle_ref'] = 'candle_lifecycle.purge_candles'
        model['lifecycle_contract'] = view['response_contract']
        model['csd_status'] = model['super_soup_status'] = 'see_candle_lifecycle'
    from gbop_voice_web.shift_narrative import attach_directional_outcome
    return attach_directional_outcome(review, bars, end, step)


def session_review(bars, day, shift, step=300):
    """H1 structure plus independently timestamped assigned-candle lifecycle."""
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    day = date.fromisoformat(day)
    base = 0 if shift == 'day' else 12
    def hour(h):
        start = int(datetime(day.year, day.month, day.day, h, tzinfo=NY).timestamp())
        subset = [b for b in bars if start <= b['time'] < start + 3600]
        if [b['time'] for b in subset] != list(range(start, start + 3600, step)):
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
        anchor_start = int(datetime(day.year, day.month, day.day, anchor_h, tzinfo=NY).timestamp())
        shift_end = int((datetime(day.year, day.month, day.day, base + 9, tzinfo=NY) + timedelta(hours=3)).timestamp())
        available_end = min(shift_end, max((b['time'] + step for b in bars), default=anchor_start + 3600))
        if available_end >= anchor_start + 3600:
            result['evidence'] = attach_lifecycle(crt_review(bars, anchor_start, available_end, 'H1', step), bars, available_end, step)
        results.append(result)
    story = review_shift(bars, day.isoformat(), shift, step)
    cutoff = parse_time(story['end_ny'])
    for row in story['ranges']:
        attach_lifecycle(row, bars, cutoff, step)
    story['recap']['candle_timeline'] = [
        {'anchor_start_ny': row['anchor_start_ny'], 'summary': row['candle_lifecycle'].get('spoken_summary', ''),
         'evidence_ref': 'shift_story.ranges[].candle_lifecycle'}
        for row in story['ranges'] if row['role'] == 'selected_range']
    return {'date_ny': day.isoformat(), 'shift': shift, 'timezone': 'America/New_York',
            'shift_story': story, 'observations': results,
            'source_resolution_seconds': step,
            'limits': 'Closed source candles aggregated to H1. Event times identify source bars, not ticks. '
                      'Use variant_evidence for H1 structure and candle_lifecycle for wick/body, CSD, Super Soup and reference retests. '
                      'Neither proves a member execution. paired_smt and paired_context evaluate relative behavior separately. '
                      'Missing/unfinished hours are not evidence of no setup.'}


FRACTAL_NAMES = {'review_market_fractal', 'inspect_market_fractal_node'}
FRACTAL_ARGS = {
    'asset': {'type': 'string'}, 'anchor_start_ny': {'type': 'string'},
    'anchor_timeframe': {'type': 'string'}, 'through_ny': {'type': 'string'},
    'node_path': {'type': ['array', 'null'], 'items': {'type': 'string'},
                  'description': 'Verified child opening timestamps from the root; null selects root.'},
    'expected_node_id': {'type': ['string', 'null']},
    'expected_scope_id': {'type': ['string', 'null'], 'description': 'Preserve returned scope on expansion/pages; null for a new analysis.'},
    'page_from_ny': {'type': ['string', 'null']},
    'following_from_ny': {'type': ['string', 'null'], 'description': 'Node detail only: returned sequel-candle cursor. Null starts after the node anchor.'},
    'page_size': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 4},
}

MARKET_TOOLS = [
    schema('review_market_fractal', 'ON REQUEST ONLY for fractal thesis formation/deeper price analysis. Link assigned Model 1 candles as independent child CRTs, bounded Monthly->Daily->H1->M5 or Weekly->H4->M15. Never call automatically for ordinary shift recaps. Null depth uses one level; pages and node detail retain exact root scope.', {**FRACTAL_ARGS, 'max_depth': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 3}}),
    schema('inspect_market_fractal_node', 'Expand one evidenced CRT node on request, retaining root asset/anchor/timeframe/cutoff plus returned node_path, expected_node_id and expected_scope_id. Returns own CSD/Super Soup/objectives independently of parent failure. No mapping is invented below M5/M15. Use page_from_ny for siblings or following_from_ny for same-node sequel OHLC; never switch to a shift-scoped candle query.', FRACTAL_ARGS),
    schema('list_market_shifts', 'Check actual retained candles before offering day/night reviews. Explicit NY date returns only usable choices for that date, with checked alternatives if none. Null date lists latest completed usable shifts and separates ongoing ones. Missing data never proves market closure.', {
        'asset': {'type': 'string'}, 'date_ny': {'type': ['string', 'null']}}),
    schema('review_market_smt', 'Compare two positively correlated markets at the SAME anchor and moment. Verifies relative boundary sweeps, boneless asset and each own objective, not trade entries. For 9ate8 use each market\'s 8 oclock H1 range; later invalidation never erases an earlier divergence.', {
        'asset': {'type': 'string'}, 'comparison_asset': {'type': 'string'},
        'anchor_start_ny': {'type': 'string'}, 'anchor_timeframe': {'type': 'string'},
        'through_ny': {'type': 'string'}}),
    schema('get_market_price', 'Get latest broker bid/ask ONLY when a quote is requested. Disclose stale or absent data.', {'asset': {'type': 'string'}}),
    schema('review_market_session', 'Review the entire GTOP shift: 9AM-noon or 9PM-midnight New York, beginning with the 8 oclock anchor. Returns hourly range promotions, later CRTs, own objectives, assigned wick/body candle lifecycle, CSD/Super Soup/retests and automatic configured paired-market context. Use for broad shift recaps. For Model 1 identity, Super Soup, CSD, wick/body, or objective-distance followups use review_market_crt for the specific range and exact named candle instead; overview omissions never establish absence.', {
        'asset': {'type': 'string'}, 'date_ny': {'type': ['string', 'null'], 'description': 'Explicit NY date; null selects the latest completed usable shift. Unavailable dates are never silently changed.'},
        'shift': {'type': 'string', 'enum': ['day', 'night']}}),
    schema('inspect_market_candles', 'Read historical or current candle OHLC and when extremes formed. Explicit ISO start/end in New York (or with offset). M1-M60, H1-H24, D1, W1, MN1; custom anchors supported. Incomplete coverage is not a definitive daily/weekly extreme. Paginate next_start_ny.', {
        'asset': {'type': 'string'}, 'start_ny': {'type': 'string'}, 'end_ny': {'type': 'string'}, 'timeframe': {'type': 'string'}}),
    schema('review_market_crt', 'Inspect ANY selected CRT anchor, subsequent purges and invalidating anchor-timeframe closes. Returns assigned wick/body candles and lifecycle facts separately from execution. Defaults: monthly->daily, weekly->H4, daily->H1, H4->M15, H1->M5. REQUIRED for focused Model 1, Super Soup, CSD, wick/body identity and objective-distance questions; a shift overview cannot substitute. Specify exact anchor start, retained cutoff, and detail_candle_start_ny when a candle was named. Preserve that exact candle on elliptical followups; never select a different candle because its direction or outcome is easier to explain.', {
        'asset': {'type': 'string'}, 'anchor_start_ny': {'type': 'string'}, 'through_ny': {'type': 'string'},
        'anchor_timeframe': {'type': 'string'}, 'confirmation_timeframe': {'type': ['string', 'null']},
        'blessed_thief_timeframe': {'type': ['string', 'null'], 'description': 'Candle opens to review; null uses anchor timeframe, independent of Model 1 mapping.'},
        'blessed_thief_from_ny': {'type': ['string', 'null'], 'description': 'Next Blessed Thief page cursor; preserve original anchor and through_ny. Null starts first page.'},
        'detail_candle_start_ny': {'type': ['string', 'null'], 'description': 'Exact assigned-candle opening to inspect. Never substitute another identity. Null chooses the first lifecycle page.'},
        'detail_from_ny': {'type': ['string', 'null'], 'description': 'Next voice lifecycle page cursor. Keep original asset, anchor, confirmation timeframe and through_ny; do not combine with exact detail_candle_start_ny.'}}),
]
MARKET_NAMES = {t['name'] for t in MARKET_TOOLS}
MARKET_PROMPT = """
# GROUNDED MARKET CONVERSATION
Use review_market_session/review_market_crt for actual setups, not definitions/quotes. '988', '9 ate 8', 'nine ate eight' mean 9ate8.
For a whole shift use shift_story.recap.spoken_summary (voice: shift_recap), including
paired_smt/paired_context, not observations[0]. Give 4-7 concise sentences. Start at
8, follow selected range_transitions through noon/midnight NY; later setups survive
failed 9ate8. Day is 09:00-12:00, night 21:00-00:00 NY. Use hourly_crt_summary and
range_summaries, naming 8/9/10/11 instead of 'one/another'. Independent hourly context
is not selected. Use hourly_progression for candle science. Include supported variants in the initial answer:
V1/V2/V3 require opposing delivery, not midpoint; V4/V5 may have unresolved delivery.
Incomplete/cutoff is not failure; repeated original-boundary touches alone are not V6.
Honor range-specific coverage, uncertainty and chronological invalidation. Earlier
delivery survives later invalidation; source-bar ties leave order unknown. Structure
and delivery do not prove fills/profit. Use candle_lifecycle for Model 1/CSD/Super Soup,
not legacy execution flags. Explain observed hindrances, never invented causation.
Resolve asset/date/shift/anchor from conversation or an unambiguous open trade.
NAS/NASDAQ=NAS100; oil/USOIL=WTI. Use known aliases directly; never default to NAS.
Ask only for genuinely missing/ambiguous context. Retain the selected range on follow-ups.
Before offering shifts or asking day/night, call list_market_shifts. Offer only checked
available_shifts; use a sole option if unspecified. For an unavailable explicit shift,
relay its message and ask about the checked same-day alternative; never silently switch date/shift.
Use NY dates. Last week Wednesday means the preceding Monday-Sunday week. Night belongs
to its 9PM start date; after midnight, tonight may mean yesterday. Clarify ambiguity.
Quotes only on request; inspect_market_candles provides historical levels/times.
Missing data does not prove closure. Unknown broker_session is not a calendar;
feed_health ages do not diagnose closure/gaps. Stale quotes do not negate history.
Partial coverage bounds extremes to available bars; ongoing means unfinished. Never reconstruct fine candles from coarse OHLC. Daily/week/
month/custom anchors use chart boundaries, not assumed NY midnight; clarify if needed.
Name candles by opening and 'closure'; speak closing timestamps only when requested.
Respect source precision: a 9:15 M5 candle is not a verified 9:17 tick.
Model 1/CSD/Super Soup/distance follow-ups require review_market_crt using exact
range/candle detail_request. Omitted facts are not absent. Keep each direction and
attempt separate; pure definitions need no retrieval.
On challenges, recheck disputed facts in the same asset/date/shift/range/candle; correct verified
errors. Answer in 1-3 sentences; omit routine execution disclaimers unless execution is at issue.
Relate evidence to member-reported fills only; save actual journals through tools.
MOB is discretionary: preserve member-supplied levels, do not auto-detect PD arrays.
Tools never place/manage broker orders or change member progress.
""".strip() + '\n\n' + LIFECYCLE_PROMPT + '\n\n' + WATCH_PROMPT

LIVE_MARKET_PROMPT = """
MARKET-DEPENDENT QUESTIONS MUST BE DELEGATED TO THE BACKEND, even if they contain
familiar GTOP terminology. Examples: 'did 988 happen today?', 'you seen today’s 9ate8?',
'I took the Super Soup on NAS today', 'when was that high purged?', 'when did it
invalidate?', 'last Wednesday’s low', or a timeframe-specific CRT review. These are
not definition questions. Delegate first; never invent today’s candle behavior.
'What did price do this shift/today?' requires shift_story.recap: start at 8,
follow hourly range transitions after invalidation through noon/midnight NY, and
include later selected CRT objectives, supported variants and delivery. Do not stop
at failed 9ate8. Give the complete recap in 4-7 concise sentences; this overrides
the short-answer default. For H1 CRTs use recap.hourly_crt_summary; named-hour questions
use range_summaries in order, explicitly naming 8/9/10/11. Never substitute 'one/another'.
Preserve missing-data and same-bar uncertainty; body-cross evidence is not an entry.
Use setup_interval.qualified_smt: same-setup-hour corresponding purges mean both
bones, no boneless/SMT label or minute-asynchrony recap. Forming hours are provisional.
On a challenge, delegate a recheck of that same date/shift/range and answer the disputed
fact first. Omit routine execution disclaimers unless actual execution is at issue.
Resolve known asset/date/shift/anchor from conversation. NAS/NASDAQ=NAS100; oil/USOIL=WTI.
Use recognized aliases directly; ask only for genuinely missing/ambiguous context.
Before offering reviews or asking day/night,
delegate list_market_shifts. Offer only checked available_shifts; use a sole option
when shift is unspecified. For an unavailable explicit shift, relay its message and
ask about the checked same-day alternative first; never silently switch date/shift.
Partial means limited candles; ongoing is not completed. Missing data does not prove
closure; explain a closure only with verified calendar/session evidence.
broker_session.status=unknown and source=null mean closure is unverified.
Use feed_health to distinguish a recent snapshot with an old quote from a stale
snapshot. Neither diagnoses closure, a broken feed, or historical candle gaps.
Do not lead with a price quote or a playbook definition. Quote current price only when
asked. Speak the verified event and timestamp naturally, preserving data precision.
Remember follow-up references to the same asset/CRT and distinguish market observations
from the member’s actual fill/exit. MOB explanation is discretionary GTOP knowledge;
automatic PD-array recognition is not required.
""".strip() + '\n\n' + LIFECYCLE_PROMPT + '\n\n' + WATCH_PROMPT


MARKET_RESPONSE_CONTRACT = """
NAMED-RANGE ANSWERS
For outcome questions, first name the range and direction: full opposing delivery,
midpoint only, pending, failed before objective, or unverified. Then give mechanism.
Use directional_outcome/variant_evidence; later invalidation preserves earlier V2 delivery.
A later opposite-direction Model 1 cannot replace the earlier wick setup or paired
thesis. Name both directions and targets. Exact Model 1 questions need the selected
range's assigned candle/open first, source purge and CSD separately. Follow detail_request.
No qualifying body Model 1 does not mean no setup: verified Turtle Wick Soup has its
own midpoint/full outcomes, never a body Model 1 or Super Soup. Forming/missing is unverified.
Say 'boneless' clearly, as bone-less: partner purged, this asset did not in that interval.
Potential/pending boneless may exist before delivery; completion is separate. Both
same-hour matching purges mean both bones, no boneless. Use setup_interval.qualified_smt;
forming qualification is provisional. Partner purge alone is not CSD or peer CSD.
Contextual 'what about Young Lefty?' needs backend evidence for the same asset/date's
7AM day / 7PM night range, not a definition or a chart request. Historical delivery
does not prove HTF permission, pre-9 execution, member fills or profit.
""".strip()

MARKET_PROMPT += '\n\n' + MARKET_RESPONSE_CONTRACT
LIVE_MARKET_PROMPT += '\n\n' + MARKET_RESPONSE_CONTRACT


def market_clock():
    return 'CURRENT NEW YORK DATE/TIME: ' + datetime.now(NY).isoformat()


def _history_sets(db, feed, start, end):
    by_step = {300: {b['time']: b for b in feed.get('bars', [])},
               60: {b['time']: b for b in feed.get('bars_m1', [])}}
    if db is not None:
        with db() as conn:
            rows = conn.execute("""SELECT step,payload FROM gbop_market_history
                WHERE asset=? AND symbol=? AND day_utc>=? AND day_utc<=? ORDER BY day_utc""",
                (feed['asset'], feed['symbol'], start // 86400 * 86400, end // 86400 * 86400)).fetchall()
        for row in rows:
            by_step[row['step']].update({b['time']: b for b in json.loads(row['payload'])})
    return {step: sorted((b for b in data.values() if start <= b['time'] < end), key=lambda b: b['time'])
            for step, data in by_step.items()}


def _select_history(sets, start, end):
    sets = {step: [b for b in bars if start <= b['time'] < end] for step, bars in sets.items()}
    fine, coarse = sets[60], sets[300]
    covered = len(fine) * 60
    if fine and (not coarse or (fine[0]['time'] <= coarse[0]['time'] and
                fine[-1]['time'] + 60 >= coarse[-1]['time'] + 300 and covered >= len(coarse) * 300)):
        return fine, 60
    return coarse, 300


def _select_shift_history(sets, day, shift, now):
    start, end = shift_bounds(day, shift)
    candidates = []
    for step, data in sets.items():
        bars = [b for b in data if start - 7200 <= b['time'] and b['time'] + step <= min(end, now)]
        availability = assess_shift(bars, day, shift, step, now)
        score = (availability['review_scope'] == 'full', availability['reviewable'],
                 len(availability['complete_hours_ny']), availability['anchor_complete'],
                 availability['closed_bar_count'] * step, -step)
        candidates.append((score, bars, step))
    _, bars, step = max(candidates, key=lambda candidate: candidate[0])
    return bars, step


def history_bars(db, feed, start, end, shift_date=None, shift=None, now=None):
    sets = _history_sets(db, feed, start, end)
    if shift_date is not None:
        return _select_shift_history(sets, shift_date, shift, int(time.time() if now is None else now))
    return _select_history(sets, start, end)


def shift_catalog(db, feed, now, requested_day=None, requested_only=False):
    """Read retained history once, partition by NY date, then check each shift.

    This is asset/symbol scoped and has no weekday or exchange-hours heuristic.
    It scans the actual 90-day retention rather than only the latest four days.
    """
    first, last = now - 90 * 86400, now
    if requested_only:
        local = datetime.combine(date.fromisoformat(requested_day), datetime.min.time(), NY)
        first, last = int(local.timestamp()), min(now, int((local + timedelta(days=1)).timestamp()))
    sets = _history_sets(db, feed, first, last)
    daily = {}
    for step, bars in sets.items():
        for bar in bars:
            if bar['time'] + step <= now:
                day = datetime.fromtimestamp(bar['time'], NY).date().isoformat()
                daily.setdefault(day, {60: [], 300: []})[step].append(bar)
    if requested_day:
        requested_day = date.fromisoformat(requested_day).isoformat()
        daily.setdefault(requested_day, {60: [], 300: []})
    rows = []
    for day, sources in sorted(daily.items(), reverse=True):
        for shift in ('night', 'day'):
            bars, step = _select_shift_history(sources, day, shift, now)
            rows.append(dict(asset=feed['asset'], **assess_shift(bars, day, shift, step, now)))
    return rows


def shift_choices(db, feed, day=None, now=None):
    now = int(time.time() if now is None else now)
    rows = shift_catalog(db, feed, now, day, requested_only=bool(day))
    if day and not any(row['reviewable'] for row in rows):
        rows = shift_catalog(db, feed, now, day)
    supported = [r for r in rows if r['reviewable']]
    completed = [r for r in supported if r['temporal_status'] == 'completed']
    requested = [r for r in rows if day and r['date_ny'] == day]
    current = [r for r in supported if r['temporal_status'] == 'in_progress']
    if day:
        available = [r for r in requested if r['reviewable']]
        alternatives = [] if available else sorted(completed, key=lambda r: (
            abs((date.fromisoformat(r['date_ny']) - date.fromisoformat(day)).days),
            r['date_ny'] > day, -parse_time(r['start_ny'])))[:2]
    else:
        available = [next((r for r in completed if r['shift'] == shift), None) for shift in ('day', 'night')]
        available = [r for r in available if r]
        alternatives = []
    return {'ok': True, 'asset': feed['asset'], 'date_ny': day, 'checked_at_ny': stamp(now),
            'broker_session': broker_session_status(),
            'status': 'available' if available else 'no_supported_shifts',
            'shifts': requested, 'available_shifts': [choice(r) for r in available],
            'ongoing_shifts': [choice(r) for r in current if not day or r['date_ny'] == day],
            'alternatives': [choice(r) for r in alternatives],
            'limits': 'Choices use closed retained candles, not weekday assumptions. Partial reviews are limited to observed candles. '
                      'No broker session calendar is available: absent data cannot confirm closure. Do not silently change a requested date.'}


def unavailable_shift(db, feed, availability, now):
    options = shift_choices(db, feed, availability['date_ny'], now)
    return unavailable_response(availability, options)


def unavailable_response(availability, options):
    alternatives = [r for r in options['available_shifts'] if r['shift'] != availability['shift']]
    alternatives = alternatives or options['alternatives']
    return {'ok': False, 'asset': options['asset'], 'status': 'shift_unavailable',
            'broker_session': broker_session_status(),
            'availability': availability, 'alternatives': alternatives,
            'message': alternative_message(availability, alternatives)}


def paired_market_review(db, asset, comparison_asset, start, end, tf='H1', detect_through=None):
    anchor_end = next_boundary(start, tf)
    if not 0 < end - start <= 90 * 86400 + 3600 or end < anchor_end:
        raise ValueError('Use a completed anchor and subsequent evidence window within 90 days.')
    if end == anchor_end:
        # A cutoff-selected range has a real anchor but no later evidence. Do
        # not let optional paired context turn that useful local answer into an
        # error, and do not interpret the empty window as absence of future SMT.
        return {'ok': True, 'status': 'no_subsequent_evidence_window',
                'assessment_status': 'not_assessed',
                'assets': [asset_name(asset), asset_name(comparison_asset)],
                'anchor_start_ny': stamp(start), 'anchor_end_ny': stamp(anchor_end),
                'anchor_timeframe': tf, 'through_ny': stamp(end), 'events': [],
                'divergence_confirmed': False, 'entry_confirmed': False,
                'message': 'The review ends at the anchor closure. No subsequent candles are available to assess paired SMT; this is not evidence of no setup.'}
    pair = []
    for value in (asset, comparison_asset):
        feed = read_feed(db, value)
        if not feed['ok']:
            return {'ok': False, 'status': 'insufficient_paired_evidence', 'missing_asset': value,
                    'error': 'Both market histories are required; missing data does not prove no SMT.'}
        bars, step = history_bars(db, feed, start, end)
        pair.append(dict(asset=feed['asset'], symbol=feed['symbol'], bars=bars, step=step))
    return enrich_smt(compare_ranges(pair[0], pair[1], start, anchor_end, end, tf, detect_through))


def latest_available_shift_date(feed, shift, db=None, now=None):
    """Latest completed, usable shift; ongoing/empty windows cannot displace it."""
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    now = int(time.time() if now is None else now)
    # Preparation runs every 30 seconds: inspect newest candidate dates first,
    # loading older payloads only when needed, instead of decoding 90 days each tick.
    dates = set()
    for step, bars in ((300, feed.get('bars', [])), (60, feed.get('bars_m1', []))):
        dates.update(datetime.fromtimestamp(b['time'], NY).date()
                     for b in bars if now-90*86400 <= b['time'] and b['time']+step <= now)
    if db is not None:
        with db() as conn:
            buckets = conn.execute('SELECT DISTINCT day_utc FROM gbop_market_history WHERE asset=? AND symbol=? AND day_utc>=?',
                                   (feed['asset'], feed['symbol'], (now-90*86400)//86400*86400)).fetchall()
        for bucket in buckets:
            dates.update(datetime.fromtimestamp(bucket['day_utc'] + offset, NY).date() for offset in (0, 86399))
    for day in sorted(dates, reverse=True):
        start, end = shift_bounds(day, shift)
        if end > now:
            continue
        bars, step = history_bars(db, feed, start-7200, end, day.isoformat(), shift, now)
        if assess_shift(bars, day, shift, step, now)['reviewable']:
            return day.isoformat()
    raise ValueError('No completed usable shift was found in retained candles.')


def market_tool(db, name, args, now=None):
    now = int(time.time() if now is None else now)
    try:
        if name not in MARKET_NAMES:
            return {'ok': False, 'error': 'Unknown market tool.'}
        if name == 'review_market_smt':
            return paired_market_review(db, args['asset'], args['comparison_asset'],
                parse_time(args['anchor_start_ny']), parse_time(args['through_ny']), args['anchor_timeframe'])
        if name == 'review_market_crt':
            if args.get('detail_candle_start_ny') and args.get('detail_from_ny'):
                raise ValueError('Choose detail_candle_start_ny or detail_from_ny, not both.')
            for key in ('detail_candle_start_ny', 'detail_from_ny'):
                if args.get(key):
                    parse_time(args[key])
        result = read_feed(db, args.get('asset'))
        if not result['ok']:
            return result
        if name == 'list_market_shifts':
            return shift_choices(db, result, args.get('date_ny'), now)
        if name == 'get_market_price':
            result.pop('bars', None); result.pop('bars_m1', None)
            return result
        if name == 'review_market_session':
            shift = args.get('shift', 'day')
            if args.get('date_ny'):
                day = date.fromisoformat(args['date_ny'])
            else:
                options = shift_choices(db, result, now=now)
                selected = next((r for r in options['available_shifts'] if r['shift'] == shift), None)
                if selected is None:
                    return {'ok': False, 'asset': result['asset'], 'status': 'no_completed_shift',
                            'broker_session': broker_session_status(),
                            'message': 'No completed usable requested shift is retained. Choose only from the checked alternatives.',
                            'alternatives': options['available_shifts'], 'ongoing_shifts': options['ongoing_shifts']}
                day = date.fromisoformat(selected['date_ny'])
            opening, end = shift_bounds(day, shift)
            start = opening - 7200
        else:
            start = parse_time(args.get('start_ny') or args['anchor_start_ny'])
            end = parse_time(args.get('end_ny') or args['through_ny'])
        if not 0 < end - start <= 90 * 86400 + 3600:
            raise ValueError('Request a positive window no longer than 90 days.')
        source_feed = dict(result)
        bars, step = (history_bars(db, result, start, end, day.isoformat(), shift, now)
                      if name == 'review_market_session' else history_bars(db, result, start, end))
        if name == 'review_market_session':
            bars = [b for b in bars if b['time'] + step <= now]
        result.pop('bars', None); result.pop('bars_m1', None)
        result.pop('bid', None); result.pop('ask', None)
        result['available_precision_seconds'] = step
        result['available_from_ny'] = stamp(bars[0]['time']) if bars else None
        result['available_through_ny'] = stamp(bars[-1]['time'] + step) if bars else None
        if name == 'review_market_session':
            availability = dict(asset=result['asset'], **assess_shift(bars, day.isoformat(), shift, step, now))
            if not availability['reviewable']:
                return unavailable_shift(db, source_feed, availability, now)
            result['availability'] = availability
            result['review'] = session_review(bars, day.isoformat(), shift, step)
            result['review']['availability'] = availability
            peer = PAIRINGS.get(result['asset'])
            if peer:
                opening = paired_market_review(db, result['asset'], peer, start + 3600, end,
                                               detect_through=start + 3 * 3600)
                result['review']['paired_smt'] = opening
                context = {'comparison_asset': peer, 'ranges': [],
                           'status': 'available' if opening.get('ok') else 'insufficient_paired_evidence'}
                if not opening.get('ok'):
                    context['missing_asset'] = opening.get('missing_asset')
                    context['message'] = opening.get('error')
                else:
                    for row in result['review']['shift_story']['ranges']:
                        anchor_start = parse_time(row['anchor_start_ny'])
                        if row['role'] == 'selected_range' and anchor_start + 3600 < end:
                            context['ranges'].append({'anchor_start_ny': row['anchor_start_ny'],
                                'role': row['role'], 'paired_review': paired_market_review(
                                    db, result['asset'], peer, anchor_start, end)})
                result['review']['paired_context'] = context
            else:
                result['review']['paired_context'] = {'status': 'no_configured_comparison_pair', 'asset': result['asset']}
        elif name in FRACTAL_NAMES:
            from gbop_voice_web.fractal_lineage import review_fractal
            result['review'] = review_fractal(bars, start, end, args['anchor_timeframe'], step,
                result['asset'], result['symbol'], as_of=now, node_path=args.get('node_path'),
                expected_node_id=args.get('expected_node_id'), expected_scope_id=args.get('expected_scope_id'),
                max_depth=(0 if name == 'inspect_market_fractal_node' else args.get('max_depth')),
                page_size=args.get('page_size'), page_from_ny=args.get('page_from_ny'),
                following_from_ny=args.get('following_from_ny'),
                detail=name == 'inspect_market_fractal_node')
        elif name == 'inspect_market_candles':
            result['review'] = candle_query(bars, start, end, args['timeframe'], step)
        else:
            result['review'] = attach_lifecycle(crt_review(bars, start, end, args['anchor_timeframe'],
                step, args.get('confirmation_timeframe'), args.get('blessed_thief_timeframe'),
                args.get('blessed_thief_from_ny')), bars, end, step)
            peer = PAIRINGS.get(result['asset'])
            if peer:
                result['review']['paired_smt'] = paired_market_review(
                    db, result['asset'], peer, start, end, args['anchor_timeframe'])
        if name in ('review_market_session', 'review_market_crt'):
            reconcile_paired_recap(result['review'], result['asset'])
        if name == 'review_market_crt':
            from gbop_voice_web.objective_approach import objective_approach
            result['review']['objective_approach'] = objective_approach(result['review'], bars, end, step)
            result['voice_detail_selection'] = {key: args.get(key) for key in (
                'detail_candle_start_ny', 'detail_from_ny', 'through_ny',
                'confirmation_timeframe', 'blessed_thief_timeframe', 'blessed_thief_from_ny')}
        return result
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        return {'ok': False, 'error': str(exc)}
