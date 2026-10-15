"""Compare a declared Butterfly snapshot with independently retained broker data.

Snapshots never enter market storage, authorize trades, or establish feed identity.
"""
import json
import math
from decimal import Decimal

from gbop_voice_web import shared_gtop_rules as rules
from gbop_voice_web.candle_evidence import parse_time, stamp

MAX_CONTEXT_BYTES = 4096
TIMEFRAMES = {'3': 'M3', '5': 'M5', '15': 'M15', '60': 'H1', '240': 'H4',
              '1D': 'D1', '1W': 'W1', '1M': 'MN1', '3M': '3M', '6M': '6M', '12M': '12M'}
FIELDS = {'schema', 'rules_version', 'rules_hash', 'source', 'symbol', 'chart_tf',
          'range_tf', 'range_open', 'range_end', 'high', 'low', 'cutoff',
          'captured_at', 'timezone', 'range_definition', 'coverage'}


def parse_snapshot(raw, now, allow_unmapped=False, allow_historical_anchor=False):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_CONTEXT_BYTES:
        raise ValueError('Paste one Butterfly context message of at most 4096 bytes.')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Unsupported or incomplete Butterfly context fields.')
    if value['schema'] != 'gtop.chart.v1' or value['source'] != 'TradingView':
        raise ValueError('Expected a GTOP Butterfly chart snapshot.')
    allowed = TIMEFRAMES if allow_unmapped else {'60', '240', '1D', '1W', '1M'}
    if value['range_tf'] not in allowed:
        raise ValueError('Unsupported range timeframe.')
    for name in ('rules_version', 'rules_hash', 'symbol', 'chart_tf', 'timezone'):
        if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 100 or any(ord(c) < 32 for c in value[name]):
            raise ValueError('Invalid chart context text.')
    if value['range_definition'] not in ('native_chart', 'gtop_ny_1_5_9') or value['coverage'] not in ('closed_source', 'unverified'):
        raise ValueError('Invalid range definition or coverage.')
    for name in ('range_open', 'range_end', 'cutoff', 'captured_at'):
        if type(value[name]) is not int or not 0 < value[name] <= now + 30:
            raise ValueError('Context timestamps must be past integer UTC seconds.')
    if not value['range_open'] < value['range_end'] <= value['cutoff'] <= value['captured_at']:
        raise ValueError('Range, cutoff and capture must be chronological.')
    # Long native source candles can precede the bounded observation window.
    window_days = {'3M': 190, '6M': 280, '12M': 460}.get(value['range_tf'], 90)
    # Bounded chart captures can retain an old still-relevant parent without
    # fetching its entire history. The legacy broker comparison keeps its cap.
    if not allow_historical_anchor and value['cutoff'] - value['range_open'] > window_days*86400 + 3600:
        raise ValueError('Context exceeds the supported 90-day review window.')
    for name in ('high', 'low'):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] <= 0:
            raise ValueError('Range prices must be finite positive numbers.')
    if value['high'] <= value['low']:
        raise ValueError('Range high must exceed low.')
    return value


def compare_snapshot(snapshot, broker, now):
    checks = []
    def check(name, status, detail):
        checks.append(dict(field=name, status=status, detail=detail))
    review = broker.get('review', {})
    anchor = review.get('anchor', {})
    check('rules', 'match' if snapshot['rules_version'] == rules.RULESET_VERSION and snapshot['rules_hash'] == rules.RULESET_SHA256 else 'mismatch',
          f"Butterfly {snapshot['rules_version']}; GBOP {rules.RULESET_VERSION}.")
    check('timezone', 'match' if snapshot['timezone'] == rules.TIMEZONE else 'mismatch',
          f"Chart clock {snapshot['timezone']}; GTOP clock {rules.TIMEZONE}.")
    check('feed_identity', 'unverified',
          f"Chart declares {snapshot['symbol']} on TradingView; GBOP reads {broker.get('symbol', 'unavailable')} from {broker.get('source', 'an unavailable broker feed')}. Feed equivalence is not established by an asset alias or matching prices.")
    capture_age = now - snapshot['captured_at']
    check('snapshot_freshness', 'match' if 0 <= capture_age <= 180 else 'stale',
          f'Context captured {capture_age} seconds ago; it is a closed-bar snapshot, not a live connection.')
    check('broker_freshness', 'match' if broker.get('status') == 'fresh' else 'stale',
          f"Broker snapshot: {broker.get('status', 'unavailable')}; historical candle evidence is assessed separately.")
    check('chart_coverage', 'match' if snapshot['coverage'] == 'closed_source' else 'unverified',
          'Chart coverage is declared by the snapshot and is not independently ingested.')
    if snapshot['range_tf'] == '240':
        opening = stamp(snapshot['range_open'])
        hour = int(opening[11:13])
        aligned = hour in (1,5,9,13,17,21) and opening[14:19] == '00:00'
        check('H4_definition', 'match' if snapshot['range_definition'] == 'gtop_ny_1_5_9' and aligned else 'unverified',
              'GTOP H4 requires its explicit New York window. Native chart H4 is compared as a declared custom interval; it is not automatically GTOP H4.')
    if not broker.get('ok') or not anchor.get('complete'):
        check('range', 'unverified', 'Complete independent broker range evidence is unavailable.')
    else:
        times_match = (parse_time(anchor['start_ny']) == snapshot['range_open'] and
                       parse_time(anchor['end_ny']) == snapshot['range_end'] and
                       review.get('anchor_timeframe') == TIMEFRAMES[snapshot['range_tf']])
        check('range_identity', 'match' if times_match else 'mismatch',
              f"Broker range {anchor['start_ny']} to {anchor['end_ny']} ({review.get('anchor_timeframe')}).")
        equal = all(Decimal(str(anchor[k])) == Decimal(str(snapshot[k])) for k in ('high','low'))
        check('range_prices', 'match' if equal else 'mismatch',
              f"Chart {snapshot['low']}–{snapshot['high']}; broker {anchor['low']}–{anchor['high']}.")
        covered = review.get('range_observation_coverage', {})
        actual = review.get('validity_evidence_through_ny')
        check('review_cutoff', 'match' if covered.get('complete') and (not actual or parse_time(actual) >= snapshot['cutoff']) else 'unverified',
              f"Requested the same cutoff {stamp(snapshot['cutoff'])}; gaps or earlier invalidation limit subsequent evidence.")
    mismatch = any(c['status'] == 'mismatch' for c in checks)
    status = 'mismatched' if mismatch else 'unverified_feed_equivalence'
    summary = ('The chart and broker context disagree. ' if mismatch else 'The declared chart context has been compared, but feed equivalence remains unverified. ')
    summary += 'GBOP is not receiving your live TradingView chart. '
    summary += ' '.join(c['detail'] for c in checks if c['status'] in ('mismatch','stale'))
    return dict(ok=True, status=status, same_context_verified=False, live_chart_connected=False,
                asset=broker.get('asset'), checks=checks, spoken_summary=summary.strip(),
                requested_context=dict(symbol=snapshot['symbol'], range_open_ny=stamp(snapshot['range_open']),
                    range_timeframe=TIMEFRAMES[snapshot['range_tf']], cutoff_ny=stamp(snapshot['cutoff'])),
                broker_directional_outcome=review.get('directional_outcome', {}).get('spoken_summary'),
                ruleset=dict(rules.RULESET))


def compare_chart_context(db, args, now):
    from gbop_voice_web.market_data import market_tool
    snapshot = parse_snapshot(args.get('snapshot_json'), now)
    broker = market_tool(db, 'review_market_crt', dict(asset=args.get('asset'),
        anchor_start_ny=stamp(snapshot['range_open']), through_ny=stamp(snapshot['cutoff']),
        anchor_timeframe=TIMEFRAMES[snapshot['range_tf']]), now=now)
    return compare_snapshot(snapshot, broker, now)
