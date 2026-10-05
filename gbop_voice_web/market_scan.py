"""On-request multi-instrument Young Lefty evidence, with one frozen window."""
from datetime import date, datetime, timedelta
import re

from gbop_voice_web.candle_evidence import NY, parse_time, stamp, crt_review
from gbop_voice_web.current_market import _anchor_state, _current_precision, _range_fact


def scan_intent(text, previous=False):
    text = (text or '').lower()
    if re.search(r'\b(?:define|definition|meaning|journal|log|save|record|watch|notify|alert)\b', text):
        return False
    wide = re.search(r'\b(?:anywhere|any|other|all|across|scan|whichever|whatever|whatevers)\b', text)
    play = re.search(r'\byoung\s+left(?:y|ys|ies)\b', text)
    from gbop_voice_web.market_data import ASSETS, ALIASES
    named = any(re.search(r'(?<!\w)' + re.escape(name.lower()) + r'(?!\w)', text)
                for name in ASSETS | set(ALIASES))
    if named and not re.search(r'\b(?:anywhere|all|across|other (?:pairs?|markets?|instruments?))\b', text):
        return False
    follow = previous and re.fullmatch(r'\s*(?:whatever(?:\s+is|s|\x27s)? applicable|any others?\??|what else\??|check (?:them )?all)\s*[.!?]?\s*', text)
    return bool(wide and play or follow)


def scan_args(text, fields, selected, now, previous=None):
    text = (text or '').lower()
    local = datetime.fromtimestamp(now, NY)
    selected = selected or {}
    previous = previous or {}
    explicit_date = fields.get('date_ny')
    explicit_shift = fields.get('shift')
    day = explicit_date or previous.get('date_ny') or selected.get('date_ny') or local.date().isoformat()
    shift = explicit_shift or previous.get('shift') or selected.get('shift') or ('day' if local.hour < 19 else 'night')
    changed = (explicit_date and explicit_date != selected.get('date_ny') or
               explicit_shift and explicit_shift != selected.get('shift'))
    refresh = bool(re.search(r'\b(?:now|currently|refresh|today|tonight)\b', text))
    through = None if changed or refresh else previous.get('through_ny') or selected.get('through_ny')
    exclude = selected.get('asset') if re.search(r'\bother\b', text) else previous.get('exclude_asset')
    return dict(date_ny=day, shift=shift, through_ny=through, exclude_asset=exclude)


def scan_young_lefty(db, args, now):
    from gbop_voice_web.market_data import ASSETS, asset_name, read_feed, _history_sets, attach_lifecycle
    local = datetime.fromtimestamp(now, NY)
    day = date.fromisoformat(args.get('date_ny') or local.date().isoformat())
    shift = args.get('shift') or ('day' if local.hour < 19 else 'night')
    if shift not in ('day', 'night'):
        raise ValueError('Use day or night shift.')
    anchor = datetime.combine(day, datetime.min.time(), NY).replace(hour=7 if shift == 'day' else 19)
    start = int(anchor.timestamp())
    end = int((anchor + timedelta(hours=5)).timestamp())
    requested = parse_time(args['through_ny']) if args.get('through_ny') else now
    cutoff = min(now, requested, end) // 60 * 60
    if cutoff - start > 90 * 86400 or start < now - 90 * 86400:
        raise ValueError('Scan window is outside retained history.')
    exclude = asset_name(args['exclude_asset']) if args.get('exclude_asset') else None
    rows = []
    for asset in sorted(ASSETS - ({exclude} if exclude else set())):
        try:
            feed = read_feed(db, asset, now=now)
            if not feed.get('ok'):
                rows.append(dict(asset=asset, status='unavailable', reason='No retained feed.'))
                continue
            capture = int(datetime.fromisoformat(feed['captured_at_utc']).timestamp())
            received = int(datetime.fromisoformat(feed['received_at_utc']).timestamp())
            observed = min(cutoff, capture // 60 * 60, received // 60 * 60)
            if observed <= start:
                rows.append(dict(asset=asset, status='unavailable', reason='No source evidence in the requested window.'))
                continue
            bars, step = _current_precision(_history_sets(db, feed, start, cutoff), start, observed)
            state = _anchor_state(bars, start, 'H1', observed, step)
            if not state['complete']:
                rows.append(dict(asset=asset, status='forming' if state['forming'] else 'unavailable',
                                 reason='Reference is not yet complete in source evidence.', observed_through_ny=state['observed_through_ny']))
                continue
            review = attach_lifecycle(crt_review(bars, start, observed, 'H1', step, 'M5'), bars, observed, step)
            review['anchor'].update(state)
            fact = _range_fact(review, 'scan_result', 'Young Lefty', asset, observed, 'H1', 'M5')
            assigned = review.get('candle_lifecycle', {}).get('purge_candles', [])
            fact['first_assigned_purge'] = ({k: assigned[0][k] for k in
                ('bar_open_ny', 'bar_close_ny', 'timeframe', 'purge_type', 'direction') if k in assigned[0]}
                if assigned else None)
            status = ('invalidated' if fact['invalidated_at_ny'] else 'observed_setup' if fact['setup_status'] == 'initiated'
                      else 'not_observed' if fact['setup_status'] == 'not_observed_in_complete_window' else 'unverified')
            rows.append(dict(asset=asset, status=status, evidence=fact,
                             source_cutoff_ny=stamp(observed), source_resolution_seconds=step,
                             feed_status=feed.get('status'), is_live=feed.get('is_live')))
        except Exception:
            # No query strings, account details or provider exceptions in responses.
            rows.append(dict(asset=asset, status='unavailable', reason='Source evidence could not be read.'))
    return dict(ok=True, play='Young Lefty', date_ny=day.isoformat(), shift=shift,
                anchor_start_ny=stamp(start), through_ny=stamp(cutoff), excluded_asset=exclude,
                checked_assets=[r['asset'] for r in rows], results=rows,
                response_contract='Lead with observed Young Lefty initiation candidates and their own objectives. '
                'observed_setup means a purge was observed, not a confirmed completed variant or entry signal. '
                'All checked instruments are listed: not_observed means no purge in complete evidence; '
                'unavailable/unverified/forming is not absence. Keep invalidation and prior delivery separate. '
                'Disclose source cutoffs/stale feeds. A scan is not permission for pre-9 execution, '
                'a trade, or a watch. Do not ask the member to choose a pair before scanning. '
                'For details use that row\'s exact detail_request; the scan does not select a member trade.')
