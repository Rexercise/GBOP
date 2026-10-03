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
from gbop_voice_web.candle_evidence import parse_time, stamp, candle_query, crt_review, summarize, next_boundary
from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.candle_lifecycle import lifecycle_review
from gbop_voice_web.market_context import PAIRINGS, enrich_smt, LIFECYCLE_PROMPT
from gbop_voice_web.market_watch import WATCH_PROMPT

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
            'bars': payload['bars'], 'bars_m1': payload.get('bars_m1', [])}


def attach_lifecycle(review, bars, end, step):
    """One conversational view; retain raw sequel facts without duplicate verdicts."""
    invalid = review.get('invalidated_at_ny')
    view = lifecycle_review(bars, review['anchor'], review.get('assigned_timeframe', 'M5'),
                            end, step, parse_time(invalid) if invalid else None)
    model = review.get('model1', {})
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
    return review


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


MARKET_TOOLS = [
    schema('review_market_smt', 'Compare two positively correlated markets at the SAME anchor and moment. Verifies relative boundary sweeps, boneless asset and each own objective, not trade entries. For 9ate8 use each market\'s 8 oclock H1 range; later invalidation never erases an earlier divergence.', {
        'asset': {'type': 'string'}, 'comparison_asset': {'type': 'string'},
        'anchor_start_ny': {'type': 'string'}, 'anchor_timeframe': {'type': 'string'},
        'through_ny': {'type': 'string'}}),
    schema('get_market_price', 'Get latest broker bid/ask ONLY when a quote is requested. Disclose stale or absent data.', {'asset': {'type': 'string'}}),
    schema('review_market_session', 'Review the entire GTOP shift: 9AM-noon or 9PM-midnight New York, beginning with the 8 oclock anchor. Returns hourly range promotions, later CRTs, own objectives, assigned wick/body candle lifecycle, CSD/Super Soup/retests and automatic configured paired-market context. Use for casual references to today’s play as well as direct questions.', {
        'asset': {'type': 'string'}, 'date_ny': {'type': ['string', 'null'], 'description': 'Explicit NY date; null selects the latest shift represented by closed feed candles.'},
        'shift': {'type': 'string', 'enum': ['day', 'night']}}),
    schema('inspect_market_candles', 'Read historical or current candle OHLC and when extremes formed. Explicit ISO start/end in New York (or with offset). M1-M60, H1-H24, D1, W1, MN1; custom anchors supported. Incomplete coverage is not a definitive daily/weekly extreme. Paginate next_start_ny.', {
        'asset': {'type': 'string'}, 'start_ny': {'type': 'string'}, 'end_ny': {'type': 'string'}, 'timeframe': {'type': 'string'}}),
    schema('review_market_crt', 'Inspect ANY selected CRT anchor, subsequent purges and invalidating anchor-timeframe closes. Returns assigned wick/body candles and lifecycle facts separately from execution. Defaults: monthly->daily, weekly->H4, daily->H1, H4->M15, H1->M5. Specify exact anchor start to preserve chart/session alignment.', {
        'asset': {'type': 'string'}, 'anchor_start_ny': {'type': 'string'}, 'through_ny': {'type': 'string'},
        'anchor_timeframe': {'type': 'string'}, 'confirmation_timeframe': {'type': ['string', 'null']}}),
]
MARKET_NAMES = {t['name'] for t in MARKET_TOOLS}
MARKET_PROMPT = """
# TRADING ACCOUNTABILITY BUDDY: GROUNDED MARKET CONVERSATION
For "what did price do today/this shift?", use review_market_session and lead with
shift_story.recap.spoken_summary (voice may expose this as shift_recap.spoken_summary),
not only observations[0]. For configured comparison pairs ALSO include paired_smt
and paired_context when their closed aligned candles verify divergence: the
standalone shift_story describes single-asset CRTs, not the entire paired SMT story.
Paraphrase naturally while retaining later ranges and outcomes. Its chapters provide detail.
Day is 09:00-12:00 and night 21:00-00:00 NY.
Whole-shift recaps are NOT direct terminology questions: use 4-7 concise sentences
to cover the complete sequence. This overrides the usual 1-3 sentence default.
Explain what price actually did, which range took over, its supported variant,
and whether the midpoint/opposing objective delivered or remained unresolved.
Use ranges[].variant_evidence for structural classifications and their reasons.
V1/V2/V3 labels use opposing-liquidity delivery, not just a midpoint touch.
V4/V5 may establish the inside-bar structure while distribution remains unresolved.
Repeated touches of the original boundary alone are not V6. Do not force a label
when variant_evidence is unresolved. Structure labels do not prove a member entry.
Start from 8; explain range_transitions and each later selected range through the
cutoff. An initial 9ate8 failure does NOT mean the shift had no later setup.
Use hourly_progression for candle science; independent_range_context is not an
assertion that the range was selected. Report the objective, purge, return inside,
assigned candle lifecycle, observed target delivery and invalidation in chronological order.
A target observed before later invalidation remains a historical fact. Never call
it a member profit or a target after entry without actual execution evidence.
Same-bar touches have unknown order. Use candle_lifecycle for candle identity and
separate subsequent CSD/Super Soup facts, not a false legacy execution flag.
Explain hindrances only as observed events (e.g. repeat purge, invalidating close,
unreached objective); do not invent causation, news, or intent. State incomplete
coverage and unresolved progression plainly. Do not say you watched the shift live.
Give a concise whole-shift recap first, with deeper times and levels on request.
'988', '9 ate 8', 'nine ate eight' mean 9ate8 in the current GTOP context.
'Did 9ate8 happen today?', 'you saw today’s 988?', 'I took today’s NAS Super Soup',
and follow-ups 'when was that high purged?', 'what time did it invalidate?' are
requests for ACTUAL candle evidence. Call review_market_session or review_market_crt
before answering. Do not start with a definition or a current-price quote.
Only call get_market_price and volunteer bid/ask when a price quote is requested.
Use inspect_market_candles for prices/times of highs/lows, including historical dates.
Resolve asset, date, shift, anchor and timeframe from the current conversation or
unambiguous open trade. Ask one short question only if a necessary fact is missing.
Never silently default the asset to NAS or confuse '9ate8' with the 9 o'clock range:
its initial anchor is 8. Carry the selected range through follow-up questions.
Use the current New York date/time supplied in member context for relative dates.
'Last week Wednesday' means Wednesday of the preceding Monday-Sunday NY week.
Night shift belongs to the date its 9 PM session starts; after midnight 'tonight’s
session' may refer to that preceding date. Clarify only if ambiguous.

Lead with the observed result, then the requested time/level. Talk like a fellow
GTOP trader, usually 1-3 sentences. Do not recite the playbook unless asked.
Use timestamped evidence to relate the member's reported entry to the observed leg,
and ask one relevant follow-up (entry candle, objective or exit) without inventing
an execution. For example, ask whether they held through a VERIFIED midpoint touch
or exited before a VERIFIED invalidating close. Never imply either event happened
without evidence. Keep confirmed market facts separate from member-reported fills.
Save journal facts only through the existing trade/journal tools, with user-reported
execution information; include relevant evidence times in the summary when useful.

Times identify candle intervals, NOT exact ticks. M1 means within that one-minute
candle. M5 cannot establish a specific minute; say 'the 9:15–9:20 candle' rather than
pretending to know 9:17. Invalidating H1 candle 10:00 confirms invalidation at 11:00.
Respect complete=false, missing candles, partial hours, ties and same-bar unknown
ordering. Missing/unfinished data is not evidence that a setup did not occur.
With partial coverage say 'highest/lowest in available candles', not a definitive
session/day/week extreme. Distinguish when the extreme formed from when later purged.
A stale quote does not invalidate historical candle facts, but disclose missing recent
coverage. If a required history window or resolution is absent, say exactly what is
available; never reconstruct fine bars from coarse OHLC. Daily/week/month and custom
CRT anchors use the specified chart start; do not silently equate NY midnight candles
with broker session candles. Ask the anchor boundary only if materially ambiguous.

SMT: use review_market_smt for synchronized anchors and boundary sweeps. One
positively correlated market sweeping buy side while its peer leaves its own high
untouched is bearish SMT; reverse for sell side/bullish. A confirmed divergence
is NOT a confirmed entry. It does NOT require both independent CRTs to deliver,
nor remain valid later. Respect anchors_valid_at_event and missing coverage; do not
use later invalidations or opposite-direction outcomes to deny earlier divergence.
When challenged, inspect matched evidence and correct the answer if warranted;
do not repeat a previous classification instead of checking its factual basis.
Examine assigned-timeframe OHLC and the identified Model 1 candle; a wick-only
purge is a Turtle Wick Soup. Identity does not wait for CSD or member execution.
MOB is discretionary knowledge. Do not spend calls trying to detect PD arrays or claim
an automatically verified MOB. Preserve a member-supplied MOB as their chosen level.
These tools never place/manage/close broker orders or change member trade progress.
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
the short-answer default for definitions. Preserve the backend's later-range outcome.
Preserve missing-data and same-bar uncertainty; body-cross evidence is not an entry.
For SMT use matched paired_smt/review_market_smt and paired_context evidence. A peer's
later independent CRT failure does not erase an earlier boundary divergence; do not
confuse SMT with entry confirmation or require identical later delivery in both markets.
Resolve known asset/date/shift/anchor from conversation; ask only for missing context.
Do not lead with a price quote or a playbook definition. Quote current price only when
asked. Speak the verified event and timestamp naturally, preserving data precision.
Remember follow-up references to the same asset/CRT and distinguish market observations
from the member’s actual fill/exit. MOB explanation is discretionary GTOP knowledge;
automatic PD-array recognition is not required.
""".strip() + '\n\n' + LIFECYCLE_PROMPT + '\n\n' + WATCH_PROMPT


def market_clock():
    return 'CURRENT NEW YORK DATE/TIME: ' + datetime.now(NY).isoformat()


def history_bars(db, feed, start, end):
    by_step = {300: {b['time']: b for b in feed.get('bars', [])},
               60: {b['time']: b for b in feed.get('bars_m1', [])}}
    with db() as conn:
        rows = conn.execute("""SELECT step,payload FROM gbop_market_history
            WHERE asset=? AND symbol=? AND day_utc>=? AND day_utc<=? ORDER BY day_utc""",
            (feed['asset'], feed['symbol'], start // 86400 * 86400, end // 86400 * 86400)).fetchall()
    for row in rows:
        by_step[row['step']].update({b['time']: b for b in json.loads(row['payload'])})
    sets = {step: sorted((b for b in data.values() if start <= b['time'] < end), key=lambda b: b['time'])
            for step, data in by_step.items()}
    fine, coarse = sets[60], sets[300]
    covered = sum(1 for b in fine) * 60
    if fine and (not coarse or (fine[0]['time'] <= coarse[0]['time'] and
                fine[-1]['time'] + 60 >= coarse[-1]['time'] + 300 and covered >= len(coarse) * 300)):
        return fine, 60
    return coarse, 300


def paired_market_review(db, asset, comparison_asset, start, end, tf='H1', detect_through=None):
    anchor_end = next_boundary(start, tf)
    if not 0 < end - start <= 90 * 86400 + 3600 or end <= anchor_end:
        raise ValueError('Use a completed anchor and subsequent evidence window within 90 days.')
    pair = []
    for value in (asset, comparison_asset):
        feed = read_feed(db, value)
        if not feed['ok']:
            return {'ok': False, 'status': 'insufficient_paired_evidence', 'missing_asset': value,
                    'error': 'Both market histories are required; missing data does not prove no SMT.'}
        bars, step = history_bars(db, feed, start, end)
        pair.append(dict(asset=feed['asset'], symbol=feed['symbol'], bars=bars, step=step))
    return enrich_smt(compare_ranges(pair[0], pair[1], start, anchor_end, end, tf, detect_through))


def latest_available_shift_date(feed, shift, db=None):
    """Find the latest actual shift in snapshots plus retained broker history."""
    if shift not in ('day','night'):
        raise ValueError('shift must be day or night.')
    first,last=(9,12) if shift=='day' else (21,24)
    now=int(time.time()); dates=[]
    sets=[(feed.get('bars',[]),300),(feed.get('bars_m1',[]),60)]
    if db is not None:
        with db() as conn:
            rows=conn.execute('SELECT step,payload FROM gbop_market_history WHERE asset=? AND symbol=? ORDER BY day_utc DESC,step DESC LIMIT 8',
                              (feed['asset'],feed['symbol'])).fetchall()
        sets += [(json.loads(r['payload']),r['step']) for r in rows]
    for bars,step in sets:
        for bar in bars:
            moment=datetime.fromtimestamp(bar['time'],NY)
            if bar['time']+step<=now and first<=moment.hour<last:
                dates.append(moment.date())
    if not dates:
        raise ValueError('No retained closed candles identify the requested shift. Specify a historical date to inspect its coverage.')
    return max(dates).isoformat()


def market_tool(db, name, args):
    try:
        if name not in MARKET_NAMES:
            return {'ok': False, 'error': 'Unknown market tool.'}
        if name == 'review_market_smt':
            return paired_market_review(db, args['asset'], args['comparison_asset'],
                parse_time(args['anchor_start_ny']), parse_time(args['through_ny']), args['anchor_timeframe'])
        result = read_feed(db, args.get('asset'))
        if not result['ok']:
            return result
        if name == 'get_market_price':
            result.pop('bars', None); result.pop('bars_m1', None)
            return result
        if name == 'review_market_session':
            shift = args.get('shift', 'day')
            day = date.fromisoformat(args.get('date_ny') or latest_available_shift_date(result, shift, db))
            start = int(datetime(day.year, day.month, day.day, 7 if shift == 'day' else 19, tzinfo=NY).timestamp())
            end = start + 5 * 3600
        else:
            start = parse_time(args.get('start_ny') or args['anchor_start_ny'])
            end = parse_time(args.get('end_ny') or args['through_ny'])
        if not 0 < end - start <= 90 * 86400 + 3600:
            raise ValueError('Request a positive window no longer than 90 days.')
        bars, step = history_bars(db, result, start, end)
        result.pop('bars', None); result.pop('bars_m1', None)
        result.pop('bid', None); result.pop('ask', None)
        result['available_precision_seconds'] = step
        result['available_from_ny'] = stamp(bars[0]['time']) if bars else None
        result['available_through_ny'] = stamp(bars[-1]['time'] + step) if bars else None
        if name == 'review_market_session':
            result['review'] = session_review(bars, day.isoformat(), shift, step)
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
        elif name == 'inspect_market_candles':
            result['review'] = candle_query(bars, start, end, args['timeframe'], step)
        else:
            result['review'] = attach_lifecycle(crt_review(bars, start, end, args['anchor_timeframe'],
                step, args.get('confirmation_timeframe')), bars, end, step)
        return result
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        return {'ok': False, 'error': str(exc)}
