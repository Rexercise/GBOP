"""Bounded owner diagnostics; old quotes never establish a market closure.

No messages, credentials, account data, repair commands or persistent settings.
"""
import time
from gbop_voice_web.market_data import ASSETS
from gbop_voice_web.market_status import feed_health

CADENCE_SECONDS = 30
FRESH_SECONDS = 120
OUTAGE_SECONDS = 300


def read_feed_metadata(db):
    # Only small metadata columns cross the DB boundary, not candle arrays.
    with db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT asset,captured_at,received_at,CAST(payload AS JSONB)->>'tick_time' AS tick_time "
            "FROM gbop_market_feed WHERE asset IN (" + ','.join('?' for _ in ASSETS) + ')',
            tuple(sorted(ASSETS))).fetchall()]


def assess_feeds(rows, now=None):
    now = int(time.time() if now is None else now)
    by_asset = {r['asset']: r for r in rows if r.get('asset') in ASSETS}
    feeds = []
    for asset in sorted(ASSETS):
        row = by_asset.get(asset, {})
        ages = {}
        for field in ('captured_at', 'received_at', 'tick_time'):
            value = row.get(field)
            try:
                if isinstance(value, bool) or value is None or str(int(value)) != str(value):
                    raise ValueError()
                ages[field] = now - int(value)
            except (ValueError, TypeError, OverflowError):
                ages[field] = None
        health = feed_health(ages['captured_at'], ages['tick_time'])
        invalid = bool(row) and (None in ages.values() or any(v < 0 for v in ages.values() if v is not None))
        capture = ages['captured_at']
        attention = ('timestamp_unverified' if invalid else 'no_snapshot' if not row
                     else 'collector_upload_overdue' if capture > OUTAGE_SECONDS
                     else 'upload_delayed' if capture > FRESH_SECONDS
                     else 'quote_unavailable' if health['quote_status'] != 'fresh' else 'none')
        feeds.append({'asset': asset, 'capture_age_seconds': capture,
                      'receipt_age_seconds': ages['received_at'], 'quote_age_seconds': ages['tick_time'],
                      'health': health['status'], 'attention': attention})
    overdue = [f['asset'] for f in feeds if f['attention'] in ('no_snapshot', 'collector_upload_overdue')]
    uncertain = [f['asset'] for f in feeds if f['attention'] == 'timestamp_unverified']
    delayed = [f['asset'] for f in feeds if f['attention'] == 'upload_delayed']
    old_quotes = [f['asset'] for f in feeds if f['attention'] == 'quote_unavailable']
    if overdue:
        status = 'collector_attention'
        notice = 'Market uploads are missing or over five minutes old for ' + ', '.join(overdue) + '. Check the existing VM, Windows login and collector. Live alerts pause when data is stale.'
    elif uncertain:
        status = 'unverified'
        notice = 'Feed timestamps cannot be verified for ' + ', '.join(uncertain) + '. Do not treat these quotes as live.'
    elif delayed:
        status = 'delayed'
        notice = 'Market uploads are over two minutes old for ' + ', '.join(delayed) + '. Live alerts are paused for stale data.'
    elif old_quotes:
        status = 'quotes_unavailable'
        notice = 'Uploads are arriving, but quotes are old for ' + ', '.join(old_quotes) + '. Market closure or a broker issue is unverified; these quotes are not live.'
    else:
        status = 'healthy'
        notice = 'All nine market feeds have recent uploads and quotes.'
    return {'ok': True, 'status': status, 'notice': notice, 'checked_at': now,
            'expected_cadence_seconds': CADENCE_SECONDS, 'freshness_limit_seconds': FRESH_SECONDS,
            'collector_attention_after_seconds': OUTAGE_SECONDS, 'feeds': feeds,
            'broker_session': 'unknown', 'external_notification_sent': False}


def operations_health(db, now=None):
    return assess_feeds(read_feed_metadata(db), now)


def log_transition(previous, report, logger):
    """One operational log on state change; no private/recurring external send."""
    current = tuple((f['asset'], f['attention']) for f in report['feeds'])
    if current != previous:
        method = logger.warning if report['status'] in ('collector_attention', 'unverified', 'delayed') else logger.info
        method('[GBOP-FEED] %s: %s', report['status'], report['notice'])
    return current
