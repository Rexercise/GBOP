"""Versioned Weekly Structure Study facts, separate from member reflection.

No trading, model calls, public messages, runtime DDL or assumed broker calendar.
Closed retained candles support observed facts; unavailable intervals never mean
the broker was closed. The canonical SS launchpad/synthesis remains human work.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import time as clock

NY = ZoneInfo('America/New_York')
VERSION = 'ss-weekly-v1'
CRYPTO = {'BTCUSD', 'ETHUSD'}
QUESTIONS = [
    ('over_leverage', 'Did you over-leverage?'),
    ('trade_limit_exceeded', 'Did you exceed the number of permitted trades?'),
    ('boredom_trades', 'Did you take any boredom trades?'),
    ('closed_too_early', 'Did you close too early?'),
    ('exited_too_late', 'Did you exit too late?'),
    ('prediction_correct', "Did you predict last week's candle correctly?"),
    ('prediction_miss_reason', 'If the prior-week prediction was not correct, why not?'),
]


def _iso(epoch):
    return datetime.fromtimestamp(epoch, NY).isoformat()


def week_window(week_start=None, *, now=None):
    """Monday labels the noncrypto Sunday-reopen through Friday-close week."""
    local = datetime.fromtimestamp(clock.time() if now is None else now, NY)
    if week_start is None:
        monday = local.date() - timedelta(days=local.weekday())
        if local < datetime.combine(monday + timedelta(days=4), time(17), NY):
            monday -= timedelta(days=7)
    else:
        monday = date.fromisoformat(str(week_start))
        if monday.weekday() != 0:
            raise ValueError('week_start must be the Monday label for this SS week.')
    start = datetime.combine(monday - timedelta(days=1), time(17), NY)
    end = datetime.combine(monday + timedelta(days=4), time(17), NY)
    return {'week_start': monday.isoformat(), 'start_ny': start.isoformat(),
            'end_ny': end.isoformat(), 'start_epoch': int(start.timestamp()),
            'end_epoch': int(end.timestamp()), 'timezone': 'America/New_York',
            'convention': 'Sunday reopen–Friday close; nominal 17:00 NY envelope',
            'broker_calendar_verified': False,
            'calendar_limit': 'Exact broker reopen, maintenance and holiday sessions are not verified. Unobserved intervals may be scheduled closures or unavailable history.'}


def source_week_window(periods, week_start=None, *, now=None):
    """Use two observed broker W1 opens, never a guessed crypto calendar."""
    now = int(clock.time() if now is None else now)
    candidates = []
    for item in periods or []:
        if not isinstance(item, dict):
            continue
        start, end = item.get('open_time'), item.get('close_time')
        if (item.get('source') != 'MT5' or item.get('timeframe') != 'W1'
                or type(start) is not int or type(end) is not int
                or not 0 < start < end <= now or not 6*86400 <= end-start <= 8*86400):
            continue
        # Existing SS uses a Monday key. This is only a reporting label around
        # the observed interval's midpoint, never used to construct boundaries.
        midpoint = datetime.fromtimestamp((start+end)//2, NY).date()
        label = (midpoint - timedelta(days=midpoint.weekday())).isoformat()
        if week_start and label != str(week_start):
            continue
        candidates.append({'week_start': label, 'start_ny': _iso(start), 'end_ny': _iso(end),
            'start_utc': datetime.fromtimestamp(start, timezone.utc).isoformat(),
            'end_utc': datetime.fromtimestamp(end, timezone.utc).isoformat(),
            'start_epoch': start, 'end_epoch': end, 'timezone': 'America/New_York',
            'source': 'MT5', 'timeframe': 'W1', 'source_boundary_verified': True,
            'convention': 'Actual completed broker W1 interval, from consecutive observed UTC opens',
            'week_label_note': 'week_start is a Monday reporting key; the observed interval above defines the actual week.',
            'broker_calendar_verified': False,
            'calendar_limit': 'The W1 boundaries are observed; broker maintenance/trading sessions within the interval are not certified.'})
    return max(candidates, key=lambda w: w['end_epoch']) if candidates else None


def _gaps(bars, step, start, end):
    cursor = start
    gaps = []
    for bar in bars:
        if bar['time'] > cursor:
            gaps.append({'start_ny': _iso(cursor), 'end_ny': _iso(bar['time'])})
        cursor = max(cursor, bar['time'] + step)
    if cursor < end:
        gaps.append({'start_ny': _iso(cursor), 'end_ny': _iso(end)})
    return gaps


def summarize_week(sets, window):
    start, end = window['start_epoch'], window['end_epoch']
    candidates = []
    for step in (60, 300):
        bars = sorted({b['time']: b for b in sets.get(step, [])
                       if start <= b['time'] and b['time'] + step <= end}.values(), key=lambda b: b['time'])
        # Broader coverage beats nominal precision; never call a sparse M1
        # subset a finer description of the entire M5 week.
        candidates.append(((len(bars) * step, -step), bars, step))
    _, bars, step = max(candidates, key=lambda item: item[0])
    gaps = _gaps(bars, step, start, end)
    coverage = {'status': 'continuous_envelope' if bars and not gaps else 'unverified_intervals' if bars else 'unavailable',
                'bar_count': len(bars), 'source_timeframe': 'M1' if step == 60 else 'M5',
                'bar_seconds': step, 'covered_seconds': len(bars) * step,
                'envelope_seconds': end-start, 'unobserved_interval_count': len(gaps),
                'unobserved_intervals': gaps[:24], 'intervals_omitted': max(0, len(gaps)-24),
                'first_observed_open_ny': _iso(bars[0]['time']) if bars else None,
                'last_observed_close_ny': _iso(bars[-1]['time'] + step) if bars else None,
                'nominal_final_bar_present': bool(bars and bars[-1]['time'] + step == end),
                'full_broker_week_verified': False,
                'limit': 'Unobserved intervals are not automatically missing trading bars or confirmed market closures. Extremes describe retained candles, not a certified full broker week.'}
    if not bars:
        return {'coverage': coverage, 'observed_ohlc': None, 'high': None, 'low': None}
    def extreme(field, fn):
        value = fn(b[field] for b in bars)
        matches = [b for b in bars if b[field] == value]
        intervals = [{'day_ny': datetime.fromtimestamp(b['time'], NY).strftime('%A'),
                      'bar_open_ny': _iso(b['time']), 'bar_close_ny': _iso(b['time'] + step)} for b in matches]
        return {'price': value, 'label': 'observed ' + field,
                'tie_count': len(matches), 'occurrences': intervals[:12],
                'occurrences_omitted': max(0, len(matches)-12),
                'last_occurrence': intervals[-1],
                'time_precision': f'{step//60}-minute candle interval, not an exact tick time',
                'definitive_weekly_extreme': False}
    ohlc = {'open': bars[0]['open'], 'high': max(b['high'] for b in bars),
            'low': min(b['low'] for b in bars), 'close': bars[-1]['close'],
            'body_direction': 'bullish' if bars[-1]['close'] > bars[0]['open'] else 'bearish' if bars[-1]['close'] < bars[0]['open'] else 'unchanged',
            'label': 'OHLC of retained observations; broker-week completeness unverified'}
    return {'coverage': coverage, 'observed_ohlc': ohlc,
            'high': extreme('high', max), 'low': extreme('low', min)}


def build_report(db, asset, week_start=None, *, now=None):
    from gbop_voice_web.market_data import asset_name, read_feed, _history_sets
    now = int(clock.time() if now is None else now)
    asset = asset_name(asset)
    feed = read_feed(db, asset, now)
    if asset in CRYPTO:
        window = source_week_window(feed.get('weekly_periods', []), week_start, now=now)
        if window is None:
            return {'ok': False, 'asset': asset, 'status': 'source_weekly_boundary_unavailable',
                    'message': 'The feed has not supplied the actual completed broker W1 interval for this week. No calendar cutoff is guessed.'}
    else:
        window = week_window(week_start, now=now)
    if now < window['end_epoch']:
        return {'ok': False, 'asset': asset, 'status': 'week_not_closed', 'window': window}
    if asset in CRYPTO:
        previous = source_week_window([p for p in feed.get('weekly_periods', [])
            if isinstance(p, dict) and type(p.get('close_time')) is int and p['close_time'] <= window['start_epoch']], now=now)
    else:
        previous = week_window((date.fromisoformat(window['week_start']) - timedelta(days=7)).isoformat(), now=now)
    sets = _history_sets(db, feed, (previous or window)['start_epoch'], window['end_epoch']) if feed.get('ok') else {}
    facts = summarize_week(sets, window)
    prior = summarize_week(sets, previous) if previous else {'observed_ohlc': None}
    current_ohlc, prior_ohlc = facts['observed_ohlc'], prior['observed_ohlc']
    close_comparison = None
    if current_ohlc and prior_ohlc:
        c, p = current_ohlc['close'], prior_ohlc['close']
        close_comparison = {'relationship': 'above' if c > p else 'below' if c < p else 'equal',
                            'observed_close': c, 'previous_observed_close': p,
                            'previous_week_start': previous['week_start'],
                            'previous_coverage': prior['coverage'],
                            'limit': 'Observed last closes only; no invented SS closure taxonomy or certified weekly close.'}
    report = {'ok': True, 'kind': 'Weekly Structure Study', 'algorithm_version': VERSION,
              'asset': asset, 'week_start': window['week_start'], 'window': window,
              'source': {'provider': 'MT5 broker closed candles', 'symbol': feed.get('symbol'),
                         'history_retention_days': 90},
              **facts, 'closure_vs_previous': close_comparison,
              'human_structure': {'high_launchpad': None, 'high_details': None,
                                 'low_launchpad': None, 'low_details': None,
                                 'structural_summary': None, 'next_week_hypothesis': None,
                                 'hypothesis_invalidation': None},
              'execution_questions': [{'field': k, 'question': q} for k, q in QUESTIONS],
              'reflection_contract': 'Review the quantitative candle facts, then supply launchpad/PDA and structural synthesis. The member may optionally answer the canonical execution questions and save them to this asset/week/report version. No answer is inferred from outcome or from silence.'}
    encoded = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)
    report['facts_hash'] = hashlib.sha256(encoded.encode()).hexdigest()
    report['report_version'] = VERSION + '-' + report['facts_hash'][:20]
    report['generated_at_utc'] = datetime.fromtimestamp(now, timezone.utc).isoformat()
    return report


def persist_report(db, report):
    if not report.get('ok'):
        return report
    report = dict(report)
    with db() as conn:
        if hasattr(conn, '_conn'):
            # Serialize report revisions even during a leased-process handover.
            lock = int.from_bytes(hashlib.sha256(('ss:' + report['asset'] + ':' + report['week_start']).encode()).digest()[:7], 'big')
            conn.execute('SELECT pg_advisory_xact_lock(?)', (lock,))
        latest = conn.execute("""SELECT payload,revision FROM gbop_ss_reports WHERE asset=? AND week_start=?
            ORDER BY revision DESC LIMIT 1""", (report['asset'], report['week_start'])).fetchone()
        if latest:
            prior = json.loads(latest['payload'])
            if prior.get('facts_hash') == report['facts_hash']:
                return prior
        revision = int(latest['revision']) + 1 if latest else 1
        exists = conn.execute('SELECT 1 FROM gbop_ss_reports WHERE asset=? AND week_start=? AND report_version=?',
            (report['asset'], report['week_start'], report['report_version'])).fetchone()
        if exists:
            report['report_version'] += '-r' + str(revision)
        report['report_revision'] = revision
        payload = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)
        conn.execute("""INSERT INTO gbop_ss_reports(asset,week_start,report_version,revision,generated_at,payload)
            VALUES (?,?,?,?,?,?)""",
            (report['asset'], report['week_start'], report['report_version'], revision, report['generated_at_utc'], payload))
    return report


def read_report(conn, asset, week_start, report_version=None):
    if report_version:
        row = conn.execute('''SELECT payload FROM gbop_ss_reports
            WHERE asset=? AND week_start=? AND report_version=?''', (asset, week_start, report_version)).fetchone()
    else:
        row = conn.execute('''SELECT payload FROM gbop_ss_reports WHERE asset=? AND week_start=?
            ORDER BY revision DESC LIMIT 1''', (asset, week_start)).fetchone()
    return json.loads(row['payload']) if row else None


def get_weekly_structure_study(db, guild, user, args=None):
    from gbop_voice_web.market_data import ASSETS, asset_name
    args = args or {}
    asset = asset_name(args['asset']) if args.get('asset') else None
    window = week_window(args.get('week_start'))
    with db() as conn:
        if asset:
            if asset in CRYPTO and not args.get('week_start'):
                latest = conn.execute('SELECT week_start FROM gbop_ss_reports WHERE asset=? ORDER BY week_start DESC,revision DESC LIMIT 1', (asset,)).fetchone()
                if latest:
                    window['week_start'] = str(latest['week_start'])
            report = read_report(conn, asset, window['week_start'], args.get('report_version'))
            contributions = conn.execute('''SELECT report_version,revision,answers,created_at
                FROM gbop_ss_contributions WHERE guild_id=? AND user_id=? AND asset=? AND week_start=? AND report_version=?
                ORDER BY revision DESC LIMIT 12''',
                (guild, user, asset, window['week_start'], (report or {}).get('report_version', ''))).fetchall()
        else:
            reports = []
            for name in sorted(ASSETS):
                target = window['week_start']
                if name in CRYPTO and not args.get('week_start'):
                    latest = conn.execute('SELECT week_start FROM gbop_ss_reports WHERE asset=? ORDER BY week_start DESC,revision DESC LIMIT 1', (name,)).fetchone()
                    if latest:
                        target = str(latest['week_start'])
                row = read_report(conn, name, target)
                reports.append({'asset': name, 'week_start': target, 'window': row['window'] if row else None, 'report_version': row['report_version'] if row else None,
                                'coverage': {k: row['coverage'][k] for k in ('status', 'source_timeframe', 'unobserved_interval_count', 'nominal_final_bar_present', 'full_broker_week_verified')} if row else None,
                                'status': 'prepared' if row else 'source_weekly_boundary_unavailable' if name in CRYPTO else 'not_prepared'})
            return {'ok': True, 'kind': 'Weekly Structure Study', 'week_start': window['week_start'],
                    'assets': reports, 'next_step': 'Which asset would you like to review?'}
    if not report:
        return {'ok': False, 'asset': asset, 'week_start': window['week_start'],
                'status': 'source_weekly_boundary_unavailable' if asset in CRYPTO else 'not_prepared',
                'message': 'No saved report for this exact asset, week and version. Never substitute another week or asset.'}
    compact_contributions = []
    for row in contributions:
        payload = json.loads(row['answers'])
        answers = payload.get('answers', {})
        clipped = {key: (value[:500] if isinstance(value, str) else value) for key, value in answers.items()}
        compact_contributions.append({'report_version': row['report_version'], 'revision': row['revision'],
            'answers': {**payload, 'answers': clipped}, 'created_at': str(row['created_at']),
            'preview_truncated': any(isinstance(value, str) and len(value)>500 for value in answers.values())})
    return {**report, 'member_contributions': compact_contributions[:3],
            'contribution_history_limit': 3, 'older_contributions_omitted': len(contributions)>3,
            'preview_limit': 'Member answer previews are capped at 500 characters per field; complete version-scoped answers remain in get_ss_review with the matching report_version; immutable revisions remain saved.', 'saving': 'Optional. Use save_ss_review with this exact asset, week_start and report_version, and only answers the member actually supplied.'}


def prepare_next_weekly(db, checked, *, now=None):
    """One asset per existing leased tick, once/hour to absorb late history."""
    from gbop_voice_web.market_data import ASSETS
    now = int(clock.time() if now is None else now)
    window = week_window(now=now)
    for asset in sorted(ASSETS):
        if asset not in CRYPTO and now < window['end_epoch'] + 3600:
            continue
        key = (asset, 'source_w1' if asset in CRYPTO else window['week_start'])
        if now - checked.get(key, 0) < 3600:
            continue
        checked[key] = now
        report = build_report(db, asset, None if asset in CRYPTO else window['week_start'], now=now)
        if report.get('ok'):
            if now < report.get('window', {}).get('end_epoch', now-3600) + 3600:
                return {'asset': asset, 'week_start': report['week_start'], 'status': 'awaiting_close_buffer', 'report_version': None}
            report = persist_report(db, report)
        for old in list(checked):
            if old[0] == asset and old != key:
                checked.pop(old, None)
        return {'asset': asset, 'week_start': report.get('week_start'),
                'status': report.get('status', 'prepared' if report.get('report_version') else 'not_prepared'),
                'report_version': report.get('report_version')}
    return None
