"""Member-scoped market subscriptions. No broker orders or journal mutations."""
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from gbop_voice_web.trade_photos import schema
from gbop_voice_web.member_access import member_access_error

NY = ZoneInfo('America/New_York')
VERSION = 'tab-watch-completed-fact-cache-2026-10-09'
TABLES = ('gbop_market_watches', 'gbop_market_alerts', 'gbop_watch_runtime', 'gbop_prepared_shifts')
SCHEMA = [
    '''CREATE TABLE IF NOT EXISTS gbop_market_watches (
       id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
       spec TEXT NOT NULL, created_at BIGINT NOT NULL, starts_at BIGINT NOT NULL,
       expires_at BIGINT NOT NULL, state TEXT NOT NULL, last_status TEXT NOT NULL,
       last_checked_at BIGINT NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS gbop_market_alerts (
       id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, guild_id BIGINT NOT NULL,
       user_id BIGINT NOT NULL, event_at BIGINT NOT NULL, payload TEXT NOT NULL,
       state TEXT NOT NULL, updated_at BIGINT NOT NULL, message_id TEXT)''',
    '''CREATE TABLE IF NOT EXISTS gbop_watch_runtime (
       id TEXT PRIMARY KEY, owner TEXT NOT NULL, lease_until BIGINT NOT NULL,
       last_tick BIGINT NOT NULL, state TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS gbop_prepared_shifts (
       asset TEXT NOT NULL, date_ny TEXT NOT NULL, shift TEXT NOT NULL,
       version TEXT NOT NULL, prepared_at BIGINT NOT NULL, payload TEXT NOT NULL,
       PRIMARY KEY(asset,date_ny,shift))''',
    'CREATE INDEX IF NOT EXISTS gbop_watches_due ON gbop_market_watches(state,expires_at)',
    'CREATE INDEX IF NOT EXISTS gbop_alerts_due ON gbop_market_alerts(state,updated_at)',
]


def preparation_rules_hash():
    from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
    return hashlib.sha256(CANONICAL_KNOWLEDGE.encode()).hexdigest()


def prepared_synopsis_valid(brief):
    synopsis = brief.get('shift_synopsis') if isinstance(brief, dict) else None
    return (isinstance(synopsis, dict) and isinstance(synopsis.get('spoken_summary'), str)
            and isinstance(synopsis.get('ranges'), list)
            and all(isinstance(r, dict) for r in synopsis['ranges'])
            and isinstance(synopsis.get('range_index'), list)
            and all(isinstance(r, dict) and isinstance(r.get('label'), str)
                    and isinstance(r.get('anchor_start_ny'), str)
                    and isinstance(r.get('detail_request'), dict)
                    and isinstance(r['detail_request'].get('tool'), str)
                    and isinstance(r['detail_request'].get('args'), dict)
                    and r['detail_request']['args'].get('anchor_start_ny') == r['anchor_start_ny']
                    for r in synopsis['range_index']))


def init_watches(db):
    with db() as conn:
        postgres = hasattr(conn, '_conn')
        if postgres:
            conn.execute('SELECT pg_advisory_xact_lock(739204719)')
        for sql in SCHEMA:
            conn.execute(sql)
        if postgres:
            for table in TABLES:
                conn.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
                conn.execute(f'REVOKE ALL ON {table} FROM PUBLIC, anon, authenticated')
                conn.execute(f'GRANT ALL ON {table} TO service_role')


def runtime_lease(db, owner, now):
    with db() as conn:
        row = conn.execute('''INSERT INTO gbop_watch_runtime(id,owner,lease_until,last_tick,state)
            VALUES ('primary',?,?,?,'ready') ON CONFLICT(id) DO UPDATE SET
            owner=excluded.owner,lease_until=excluded.lease_until,last_tick=excluded.last_tick,state='ready'
            WHERE gbop_watch_runtime.lease_until<=? OR gbop_watch_runtime.owner=? RETURNING id''',
            (owner, now + 90, now, now, owner)).fetchone()
        return bool(row)


def shift_window(now, requested=None):
    local = datetime.fromtimestamp(now, NY)
    hour = local.hour
    shift = requested or ('day' if hour < 12 else 'night' if hour < 24 else 'day')
    if shift not in ('day', 'night'):
        raise ValueError('Use day or night shift.')
    start = local.replace(hour=9 if shift == 'day' else 21, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=3)
    if local >= end:
        start += timedelta(days=1); end += timedelta(days=1)
    return shift, start, end


def _public_watch(row, now):
    result = dict(row)
    result['spec'] = json.loads(result['spec'])
    result.pop('guild_id', None); result.pop('user_id', None)
    if result['state'] == 'active' and result['expires_at'] <= now:
        result['state'] = 'expired'
    result['delivery'] = 'private Discord DM; not automatic voice playback'
    return result


def watch_tool(db, guild_id, user_id, owner_id, name, args, now=None):
    now = int(time.time() if now is None else now)
    denial = member_access_error(db, guild_id, user_id, owner_id)
    if denial:
        return {'ok': False, 'error': denial}
    try:
        if name == 'get_prepared_market_brief':
            from gbop_voice_web.market_data import asset_name, market_tool, unavailable_response
            asset = asset_name(args.get('asset'))
            shift = args.get('shift') or 'day'
            if shift not in ('day', 'night'):
                raise ValueError('Use day or night.')
            if args.get('date_ny'):
                options = market_tool(db, 'list_market_shifts', {'asset': asset, 'date_ny': args['date_ny']}, now=now)
                if not options.get('ok'):
                    return options
                requested = next(r for r in options['shifts'] if r['shift'] == shift)
                if not requested['reviewable']:
                    return unavailable_response(requested, options)
            with db() as conn:
                if args.get('date_ny'):
                    rows = conn.execute('SELECT * FROM gbop_prepared_shifts WHERE asset=? AND date_ny=? AND shift=? AND version=?',
                                        (asset, args['date_ny'], shift, VERSION)).fetchall()
                else:
                    rows = conn.execute('SELECT * FROM gbop_prepared_shifts WHERE asset=? AND shift=? AND version=? ORDER BY date_ny DESC LIMIT 180',
                                        (asset, shift, VERSION)).fetchall()
            row, review = None, None
            for candidate in rows:
                try:
                    payload = json.loads(candidate['payload'])
                except (TypeError, ValueError):
                    continue
                if not prepared_synopsis_valid(payload):
                    continue
                cache = payload.get('analysis_cache')
                if not isinstance(cache, dict) or cache.get('rules_hash') != preparation_rules_hash():
                    continue
                availability = payload.get('availability')
                if (isinstance(availability, dict) and availability.get('reviewable') and availability.get('temporal_status') == 'completed'
                        and availability.get('date_ny') == candidate['date_ny']
                        and availability.get('shift') == shift and availability.get('asset') == asset):
                    row, review = candidate, payload
                    break
            if not row:
                return {'ok': False, 'status': 'not_prepared', 'next_action': 'Call list_market_shifts before offering reviews; review_market_session can read a supported shift. Never invent availability.'}
            review.pop('analysis_cache', None)
            return {'ok': True, 'asset': asset, 'date_ny': row['date_ny'], 'shift': shift,
                    'prepared_at_epoch': row['prepared_at'], 'age_seconds': now-row['prepared_at'],
                    'availability': review['availability'], 'review': review,
                    'limits': 'Saved closed-candle briefing. Refresh review_market_session for newer candles or exact follow-up evidence.'}
        if name != 'manage_market_watch':
            return {'ok': False, 'error': 'Unknown watch tool.'}
        action = args.get('action')
        scope = (int(guild_id), int(user_id))
        if action == 'list':
            with db() as conn:
                rows = conn.execute('SELECT * FROM gbop_market_watches WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 20', scope).fetchall()
                deliveries = conn.execute('SELECT watch_id,state,event_at,updated_at FROM gbop_market_alerts WHERE guild_id=? AND user_id=? ORDER BY updated_at DESC LIMIT 10', scope).fetchall()
                runtime = conn.execute("SELECT last_tick,state FROM gbop_watch_runtime WHERE id='primary'").fetchone()
            return {'ok': True, 'watches': [_public_watch(r,now) for r in rows],
                    'recent_deliveries': [dict(r) for r in deliveries],
                    'monitor_ready': bool(runtime and runtime['state']=='ready' and now-runtime['last_tick']<=120)}
        if action == 'cancel':
            key = args.get('watch_id')
            with db() as conn:
                if key:
                    rows = conn.execute("UPDATE gbop_market_watches SET state='cancelled',last_status='cancelled_by_member' WHERE guild_id=? AND user_id=? AND id=? RETURNING id", scope+(str(key),)).fetchall()
                else:
                    rows = conn.execute("UPDATE gbop_market_watches SET state='cancelled',last_status='cancelled_by_member' WHERE guild_id=? AND user_id=? AND state='active' RETURNING id", scope).fetchall()
                for row in rows:
                    conn.execute("UPDATE gbop_market_alerts SET state='cancelled',updated_at=? WHERE watch_id=? AND guild_id=? AND user_id=? AND state='pending'", (now,row['id'])+scope)
            return {'ok': True, 'cancelled_count': len(rows), 'ids': [r['id'] for r in rows]}
        if action != 'start':
            raise ValueError('Use start, list or cancel.')
        from gbop_voice_web.market_data import asset_name, read_feed
        from gbop_voice_web.candle_evidence import timeframe, parse_time
        asset = asset_name(args.get('asset'))
        event = args.get('event') or 'purge'
        if event not in EVENTS:
            raise ValueError('Unsupported event type.')
        shift, begins, ends = shift_window(now, args.get('shift'))
        anchor = args.get('anchor_start_ny')
        tf = timeframe(args.get('anchor_timeframe') or 'H1')
        if anchor:
            value = parse_time(anchor)
            if not int(begins.timestamp())-90*86400 <= value < int(ends.timestamp()):
                raise ValueError('Anchor must be within the retained history and before the watch expires.')
        elif tf != 'H1':
            raise ValueError('Give the selected anchor start for a non-H1 watch.')
        feed = read_feed(db, asset, now)
        if not feed['ok']:
            return {'ok': False, 'status': 'not_connected', 'error': 'This asset has no connected feed. No watch was registered.'}
        spec = dict(asset=asset,event=event,shift=shift,date_ny=begins.date().isoformat(),
                    anchor_start_ny=anchor,anchor_timeframe=tf,
                    range_mode='explicit_anchor' if anchor else 'selected_shift_ranges')
        key = hashlib.sha256((str(scope)+json.dumps(spec,sort_keys=True)).encode()).hexdigest()[:24]
        with db() as conn:
            runtime = conn.execute("SELECT last_tick,state FROM gbop_watch_runtime WHERE id='primary'").fetchone()
            if not runtime or runtime['state'] != 'ready' or now-runtime['last_tick'] > 120:
                return {'ok': False, 'status': 'monitor_unavailable', 'error': 'Live watcher is not ready; no subscription was registered.'}
            own = conn.execute("SELECT count(*) AS n FROM gbop_market_watches WHERE guild_id=? AND user_id=? AND state='active' AND expires_at>? AND id<>?", scope+(now,key)).fetchone()['n']
            total = conn.execute("SELECT count(*) AS n FROM gbop_market_watches WHERE state='active' AND expires_at>? AND id<>?", (now,key)).fetchone()['n']
            if own >= 6 or total >= 24:
                return {'ok': False, 'error': 'Watch capacity reached. Cancel an existing watch before adding another.'}
            existing = conn.execute('SELECT * FROM gbop_market_watches WHERE id=? AND guild_id=? AND user_id=?', (key,)+scope).fetchone()
            if existing and existing['state']=='active' and existing['expires_at']>now:
                return {'ok': True, 'already_registered': True, 'watch': _public_watch(existing,now)}
            status = 'waiting_for_shift' if now<int(begins.timestamp()) else 'watching_closed_candles' if feed['is_live'] else 'paused_stale_feed'
            conn.execute('''INSERT INTO gbop_market_watches(id,guild_id,user_id,spec,created_at,starts_at,expires_at,state,last_status,last_checked_at)
                VALUES (?,?,?,?,?,?,?,'active',?,0) ON CONFLICT(id) DO UPDATE SET
                created_at=excluded.created_at,state='active',last_status=excluded.last_status,last_checked_at=0''',
                (key,)+scope+(json.dumps(spec),now,int(begins.timestamp()),int(ends.timestamp()),status))
            row = conn.execute('SELECT * FROM gbop_market_watches WHERE id=?', (key,)).fetchone()
        return {'ok': True, 'watch': _public_watch(row,now), 'monitor_interval_seconds': 30,
                'limits': 'Closed-candle events only while the existing service and feed are available. No historical alerts, trades or spoken push notifications.'}
    except (ValueError,TypeError,KeyError) as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        return {'ok': False, 'error': 'Watch storage could not be reached. Registration or cancellation is not confirmed.'}


EVENTS = ('purge','body_soup','wick_soup','model1','csd','super_soup','smt','objective','all')
WATCH_TOOLS = [
    schema('manage_market_watch', 'Start, list or cancel THIS authenticated member\'s private Discord market alerts. Start only on an explicit request to watch/notify. Successful registration is required before promising alerts. Null anchor follows selected H1 ranges from 8 through the chosen shift; explicit anchor watches that CRT. Events use closed candles, expire at shift end, and pause on stale feeds. Cancel with null watch_id cancels only this member\'s active watches.', {
        'action': {'type':'string','enum':['start','list','cancel']},
        'asset': {'type':['string','null']}, 'event': {'type':['string','null'],'enum':list(EVENTS)+[None]},
        'shift': {'type':['string','null'],'enum':['day','night',None]},
        'anchor_start_ny': {'type':['string','null']}, 'anchor_timeframe': {'type':['string','null']},
        'watch_id': {'type':['string','null']}}),
    schema('get_prepared_market_brief', 'Read the automatically prepared closed-candle GTOP shift briefing, including paired SMT when available. This is a saved preparation, not a current quote. Use review_market_session to refresh it or inspect exact candles. Null date reads the latest completed usable stored shift for this asset. Empty/unsupported saved reviews are excluded.', {
        'asset': {'type':'string'}, 'date_ny': {'type':['string','null']},
        'shift': {'type':'string','enum':['day','night']}}),
]
WATCH_NAMES = {x['name'] for x in WATCH_TOOLS}
WATCH_PROMPT = '''
TAB MARKET WATCHES AND PREPARATION
For an explicit "watch NAS", "let me know when you see the purge", "body only",
"tell me when Model 1/Super Soup/CSD appears", register manage_market_watch using
known conversation context. "Body only" maps to body_soup, not wick_soup. Ask only
for an essential missing asset/range; never guess another member's identity.
Do not merely say "I'll watch". Report the actual returned status, range scope,
shift expiry and delivery destination: private Discord DM, not automatic speech.
A successful subscription is not proof of a market event. List/cancel on request.
Do not create subscriptions from a casual recap request, hypotheticals or quotes.
Polling uses closed candles about every 30 seconds while the existing service is
running. Forming candles cannot establish a Model 1 body close. Stale/missing feeds
pause evaluation; neither means no setup. The watcher does not place orders.
Prepared briefings cover connected feeds in rotation without AI polling. Use
get_prepared_market_brief for a prepared overview, and review_market_session/CRT
for exact or fresher facts. State its actual date and as-of time. Do not pretend an
unsupported correlated feed is available or claim uninterrupted monitoring.
Super Soup: read formation, outcome and selected-range function separately.
'''
