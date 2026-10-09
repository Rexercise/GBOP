"""On-demand, time-bounded market observations, never a completed-shift fallback.

Only closed source bars are available from the collector. An aggregate H1 (or
custom anchor) can still be forming. Quotes cannot manufacture intrabar events.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import re

from gbop_voice_web.candle_evidence import (
    ASSIGNED, NY, crt_review, next_boundary, parse_time, stamp, summarize, timeframe,
)


CURRENT_CONTRACT = (
    'Answer as of as_of_ny, using observed_through_ny and freshness. This is an '
    'on-demand snapshot, not tick streaming. Never say purging as we speak. '
    'Forming ranges are provisional, never fixed references or closed candles. '
    'Purges require a complete reference; variants/CISD require their own closes. '
    'Name supplied variants/candidates with candle-specific why and missing conditions. '
    'Keep observed delivery separate from pending classification. Preserve this '
    'scope/cutoff for detail and journals; refresh only on a new current request. '
    'No completed-shift fallback, automatic fractal scan, member fill or result inference.')


def is_current_request(text):
    """Conservative known-text routing; audio must explicitly call the tool."""
    if text is None:
        return False
    text = text.lower()
    if re.search(r'\b(?:recap|historical|yesterday|last night|last week|previous|completed shift|'
                 r'what did|how did|definition|define|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', text):
        return False
    # A dated review stays historical. A dated chart anchor is a reference for
    # an explicitly current custom/HTF request, not a request to change its day.
    if (re.search(r'\b20\d{2}-\d{2}-\d{2}\b', text)
            and not re.search(r'\b(?:anchor|range)\b', text)):
        return False
    return bool(re.search(
        r'\b(?:what (?:do you see|are you seeing)|what(?:\'s| is) happening|'
        r'current (?:market|price|analysis|view)|(?:analyze|review|check|look at) .+ (?:now|currently)|'
        r'(?:right now|currently)|how (?:is|does) .+ look(?:ing)?|refresh (?:this|that|the|my|current) (?:\w+ )?(?:range|view|market|analysis))\b', text))


def current_request_args(text, fields, selected, now=None):
    """Only verified textual/retained scope can choose a current custom anchor."""
    from gbop_voice_web.market_data import ALIASES, ASSETS
    text = (text or '').lower()
    aliases = {**{x.lower(): x for x in ASSETS}, **{k.lower(): v for k, v in ALIASES.items()},
               'bitcoin': 'BTCUSD', 'ethereum': 'ETHUSD', 'crude': 'WTI'}
    assets = {value for key, value in aliases.items()
              if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', text)}
    if len(assets) > 1:
        raise ValueError('Which one market should I inspect first?')
    asset = fields.get('asset') or (selected or {}).get('asset')
    if not asset:
        raise ValueError('Which market should I inspect now?')
    args = {'asset': asset, 'anchor_start_ny': None, 'anchor_timeframe': None,
            'confirmation_timeframe': None}
    same = bool(re.search(r'\b(?:same|this|that|selected) (?:\w+ )?(?:range|anchor)|\brefresh\b', text))
    frame = re.search(r'\b(?:h(?:[1-9]|1\d|2[0-4])|m(?:[1-9]|[1-5]\d|60)|d1|w1|mn1|daily|weekly|monthly)\b', text)
    tf = timeframe(frame[0]) if frame else None
    iso = re.search(r'\b20\d{2}-\d{2}-\d{2}t\d{2}:\d{2}(?::00)?(?:[+-]\d{2}:\d{2}|z)?', text)
    anchor = iso[0].upper() if iso else fields.get('anchor_start_ny')
    wall = re.search(r'\b(1[0-2]|0?[1-9])(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\s*(?:candle|range|anchor)\b', text)
    if wall and not iso and now is not None:
        day = datetime.fromtimestamp(now, NY)
        hour = int(wall[1]) % 12 + (12 if wall[3].startswith('p') else 0)
        anchor = day.replace(hour=hour, minute=int(wall[2] or 0), second=0, microsecond=0).isoformat()
    if anchor:
        args.update(anchor_start_ny=anchor, anchor_timeframe=tf or 'H1')
    elif same or tf:
        if (not selected or not selected.get('anchor_start_ny')
                or (tf and timeframe(selected.get('anchor_timeframe')) != tf)
                or selected.get('asset') != asset):
            raise ValueError('Which chart opening and timeframe should I use for that current range?')
        args.update(anchor_start_ny=selected['anchor_start_ny'],
                    anchor_timeframe=selected['anchor_timeframe'],
                    confirmation_timeframe=selected.get('assigned_timeframe'))
    confirmation = re.search(r'\b(?:assigned|confirmation)(?: timeframe)?\s*(m\d{1,2}|h\d{1,2}|d1)\b|'
                             r'\b(m\d{1,2}|h\d{1,2}|d1)\s*(?:assigned|confirmation)\b', text)
    if confirmation:
        args['confirmation_timeframe'] = timeframe(confirmation[1] or confirmation[2])
    return args


def current_session(now):
    local = datetime.fromtimestamp(now, NY)
    day = local.date().isoformat()
    hour = local.hour
    shift = 'day' if 7 <= hour < 12 else 'night' if 19 <= hour else None
    phase = ('in_shift' if 9 <= hour < 12 or hour >= 21 else
             'pre_shift' if shift else 'off_shift')
    opening = (local.replace(hour=9 if shift == 'day' else 21, minute=0,
                             second=0, microsecond=0) if shift else None)
    upcoming = local.replace(hour=9 if hour < 7 else 21, minute=0, second=0, microsecond=0)
    return {'date_ny': day, 'shift': shift, 'phase': phase,
            'shift_start_ny': opening.isoformat() if opening else None,
            'shift_end_ny': (opening + timedelta(hours=3)).isoformat() if opening else None,
            'next_shift_start_ny': upcoming.isoformat() if not shift else None,
            'broker_closure_inferred': False}


def _anchor_state(bars, start, tf, cutoff, step, native_h1=None):
    end = next_boundary(start, tf)
    observed = min(end, cutoff)
    value = summarize(bars, start, max(start, observed), step)
    if tf == 'H1' and end <= cutoff and native_h1:
        from gbop_voice_web.candle_evidence import h1_anchor
        value = h1_anchor(bars, start, step, native_h1, cutoff)
    complete = value['complete'] and end <= cutoff
    last = max((b['time'] + step for b in bars
                if start <= b['time'] and b['time'] + step <= observed), default=None)
    value.update(end_ny=stamp(end), timeframe=tf, complete=complete,
                 forming=start <= cutoff < end,
                 observed_through_ny=stamp(last) if last is not None else None,
                 status=('not_started' if start > cutoff else 'forming' if end > cutoff else
                         'closed' if complete else 'closed_time_incomplete_evidence'))
    return value


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _objective(value):
    out = _pick(value, ('status', 'level', 'spoken_label', 'liquidity_side'))
    if out.get('status') == 'not_observed_by_shift_end':
        out['status'] = 'not_observed_by_review_cutoff'
    if value.get('evidence'):
        out['source_interval'] = _pick(value['evidence'],
            ('bar_open_ny', 'bar_close_ny', 'precision_seconds'))
    return out


def _range_fact(evidence, role, play, asset, cutoff, tf, assigned):
    anchor = evidence['anchor']
    events = evidence.get('events', [])
    purges = [e for e in events if e['kind'].endswith('_side_purge')]
    outcome = evidence.get('directional_outcome') or {}
    model = evidence.get('model1') or {}
    lifecycle = evidence.get('candle_lifecycle') or {}
    variants = evidence.get('variant_evidence') or {}
    labels = [_pick(v, ('code', 'name')) for v in variants.get('labels', [])]
    coverage = evidence.get('range_observation_coverage', evidence.get('observation_coverage', {}))
    state = ('pending_reference_close' if anchor['forming'] else
             'pending_reference_start' if anchor['status'] == 'not_started' else
             'reference_unverified' if not anchor['complete'] else
             'initiated' if purges else 'pending_observation' if parse_time(anchor['end_ny']) >= cutoff else
             'not_observed_in_complete_window' if coverage.get('complete') else 'unverified_incomplete_window')
    fact = {'anchor_start_ny': anchor['start_ny'], 'anchor_timeframe': tf,
            'assigned_timeframe': assigned, 'role': role, 'play': play,
            'anchor': _pick(anchor, ('start_ny', 'end_ny', 'timeframe', 'status', 'complete', 'forming',
                                   'open', 'high', 'low', 'close', 'midpoint', 'observed_through_ny',
                                   'bar_count', 'missing_bar_count', 'source_resolution_seconds',
                                   'source_coverage_complete', 'ohlc_complete', 'ohlc_basis',
                                   'native_h1_status', 'native_ohlc_provenance')),
            'setup_status': state, 'range_status': evidence.get('status'),
            'direction': outcome.get('direction'), 'outcome': outcome.get('status'),
            'invalidated_at_ny': evidence.get('invalidated_at_ny'),
            'variant': {'status': 'not_assessed' if tf != 'H1' else 'established' if labels else
                        'not_established' if evidence.get('invalidated_at_ny') else
                        variants.get('explanation', {}).get('status', 'pending') if purges else 'pending', 'labels': labels,
                        **({'explanation': deepcopy(variants['explanation'])} if variants.get('explanation') else {})},
            'midpoint': _objective(outcome.get('midpoint', {})),
            'opposing_liquidity': _objective(outcome.get('opposing_liquidity', {})),
            'first_purge': _pick(purges[0], ('kind', 'bar_open_ny', 'bar_close_ny', 'precision_seconds')) if purges else None,
            'model1_status': model.get('status', 'pending_reference_close'),
            'model1_count': model.get('identified_count', 0),
            'forming_assigned_candle': model.get('forming_assigned_candle', False),
            'confirmed_cisd_count': sum(x.get('csd', {}).get('status') == 'confirmed'
                                        for x in lifecycle.get('purge_candles', [])),
            'spoken_summary': outcome.get('spoken_summary'),
            'coverage_complete': coverage.get('complete', False)}
    from gbop_voice_web.shift_synopsis import _delivery_manner
    _delivery_manner(fact, variants)
    # A future/incomplete reference is inspectable as OHLC, not a valid CRT.
    if anchor['complete']:
        fact['detail_request'] = {'tool': 'review_market_crt', 'args': {
            'asset': asset, 'anchor_start_ny': anchor['start_ny'], 'anchor_timeframe': tf,
            'through_ny': stamp(cutoff), 'confirmation_timeframe': assigned,
            'blessed_thief_timeframe': None, 'blessed_thief_from_ny': None,
            'detail_candle_start_ny': None, 'detail_from_ny': None}}
    if evidence.get('young_lefty_context'):
        from gbop_voice_web.young_lefty_context import compact_young_context, young_context_sentence, neutral_thesis_fact
        fact['young_lefty_context'] = compact_young_context(evidence['young_lefty_context'])
        neutral_thesis_fact(fact)
        fact['spoken_summary'] = young_context_sentence(fact['young_lefty_context'], fact)
    elif evidence.get('double_purge', {}).get('continuation', {}).get('legs'):
        from gbop_voice_web.range_delivery_sequence import continuation_sentence
        fact['delivery_continuation'] = deepcopy(evidence['double_purge']['continuation'])
        fact['spoken_summary'] += ' ' + continuation_sentence(fact['delivery_continuation'], anchor['start_ny'], tf)
    return fact


def _current_precision(sets, start, cutoff, native_h1=None, anchor_starts=None):
    """Prefer consistent closed references, then freshness and real coverage."""
    anchors = [t for t in (anchor_starts if anchor_starts is not None else [start])
               if t + 3600 <= cutoff]
    native_times = {b['time'] for b in (native_h1 or [])}
    use_native = any(t in native_times for t in anchors)
    candidates = []
    for step, source in sets.items():
        bars = [b for b in source if start <= b['time'] and b['time'] + step <= cutoff]
        observed = max((b['time'] + step for b in bars), default=0)
        score = (observed, len(bars) * step, -step)
        if use_native:
            resolved = [_anchor_state(bars, t, 'H1', cutoff, step, native_h1) for t in anchors]
            post_start = min(anchors) + 3600
            post_end = cutoff // step * step
            post = summarize(bars, post_start, max(post_start, post_end), step)
            score = (not any(c.get('native_h1_status') == 'conflicting_ohlc' for c in resolved),
                     sum(c['complete'] for c in resolved), observed, post['complete'],
                     post['bar_count'] * step, -step)
        candidates.append((score, bars, step))
    _, bars, step = max(candidates, key=lambda item: item[0])
    return bars, step


def review_current_market(db, feed, args, now):
    # Local imports avoid a market_data -> current_market -> market_data cycle.
    from gbop_voice_web.market_data import _history_sets, attach_lifecycle, read_feed, history_native_h1
    from gbop_voice_web.market_context import PAIRINGS, enrich_smt
    from gbop_voice_web.smt_evidence import compare_ranges

    cutoff = now // 60 * 60
    session = current_session(now)
    local = datetime.fromtimestamp(now, NY)
    hour_start = int(local.replace(minute=0, second=0, microsecond=0).timestamp())
    explicit = bool(args.get('anchor_start_ny') or args.get('anchor_timeframe'))
    if explicit and not (args.get('anchor_start_ny') and args.get('anchor_timeframe')):
        raise ValueError('A current custom or higher-timeframe review needs both an explicit chart anchor and timeframe.')
    tf = timeframe(args['anchor_timeframe']) if explicit else 'H1'
    assigned = timeframe(args['confirmation_timeframe']) if args.get('confirmation_timeframe') else ASSIGNED.get(tf)
    if explicit:
        starts = [parse_time(args['anchor_start_ny'])]
        if starts[0] > cutoff:
            raise ValueError('A current anchor cannot start in the future.')
        selected = starts[0]
    elif session['shift']:
        opening = parse_time(session['shift_start_ny'])
        starts = list(range(opening - 7200, min(opening + 7200, hour_start) + 1, 3600))
        if opening - 3600 not in starts:
            starts.append(opening - 3600)  # Explicitly not started before eight.
        starts.sort()
        selected = opening - (7200 if cutoff < opening else 3600)
    else:
        # Off-shift means the actual current hour and its preceding reference,
        # never yesterday's or the latest available completed shift.
        starts = [hour_start - 3600, hour_start]
        selected = starts[0]
    start = min(starts)
    if not 0 <= cutoff - start <= 90 * 86400 + 3600:
        raise ValueError('Current review requires an anchor within retained 90-day history.')
    capture = int(datetime.fromisoformat(feed['captured_at_utc']).timestamp())
    received = int(datetime.fromisoformat(feed['received_at_utc']).timestamp())
    source_cutoff = min(cutoff, capture // 60 * 60, received // 60 * 60)
    sets = _history_sets(db, feed, start, cutoff)
    native_h1 = history_native_h1(db, feed, start, source_cutoff) if tf == 'H1' else []
    bars, step = _current_precision(sets, start, source_cutoff, native_h1, starts if tf == 'H1' else [])
    states = {t: _anchor_state(bars, t, tf, cutoff, step, native_h1) for t in starts}
    progression = 'explicit_anchor' if explicit else 'off_shift_hourly_reference'
    if not explicit and session['phase'] == 'in_shift':
        progression = 'verified_through_closed_hours'
        if not states[selected]['complete']:
            progression = 'unverified_missing_reference'
        else:
            for t in starts:
                if t <= selected or t + 3600 > cutoff:
                    continue
                row = states[t]
                if not row['complete']:
                    progression = 'unverified_missing_hour'
                    break
                ref = states[selected]
                if row['close'] > ref['high'] or row['close'] < ref['low']:
                    selected = t
    elif not explicit and session['phase'] == 'pre_shift':
        progression = 'pre_shift_reference'
    evidence, facts = {}, []
    for t in starts:
        anchor = states[t]
        if anchor['complete']:
            row = attach_lifecycle(crt_review(bars, t, source_cutoff, tf, step,
                                             args.get('confirmation_timeframe'), native_h1=native_h1), bars, source_cutoff, step)
            row['anchor'].update({k: anchor[k] for k in ('status', 'forming', 'observed_through_ny')})
        else:
            row = {'anchor': anchor, 'assigned_timeframe': assigned,
                   'status': 'pending_reference_close' if anchor['forming'] else 'unverified_reference',
                   'events': [], 'model1': {'status': 'unverified_incomplete_anchor', 'candles': []},
                   'candle_lifecycle': {'status': 'unverified_incomplete_anchor', 'purge_candles': []}}
        evidence[t] = row
        hour = datetime.fromtimestamp(t, NY).hour
        play = ('Young Lefty' if hour in (7, 19) else '9ate8' if hour in (8, 20) else None
                ) if not explicit and session['shift'] else None
        facts.append(_range_fact(row, 'selected_range' if t == selected else 'independent_range_context',
                                 play, feed['asset'], source_cutoff, tf, assigned))
    newest_bar = max((b['time'] + step for b in bars), default=None)
    freshness = {**_pick(feed, ('status', 'is_live', 'tick_age_seconds', 'capture_age_seconds',
                               'received_age_seconds', 'feed_health', 'tick_time_utc',
                               'captured_at_utc', 'received_at_utc')),
                 'source_resolution_seconds': step,
                 'observed_through_ny': stamp(newest_bar) if newest_bar else None,
                 'bar_age_seconds': now - newest_bar if newest_bar else None,
                 'source_snapshot_cutoff_ny': stamp(source_cutoff),
                 'collector_mode': 'periodic_closed_bar_snapshot',
                 'collector_interval_seconds_approx': 31,
                 'instantaneous_tick_observation': False}
    scope = {'asset': feed['asset'], 'date_ny': datetime.fromtimestamp(selected, NY).date().isoformat(),
             'shift': session['shift'],
             'anchor_start_ny': stamp(selected), 'anchor_timeframe': tf,
             'assigned_timeframe': assigned,
             'review_mode': 'current_market', 'as_of_ny': stamp(now),
             'through_ny': stamp(source_cutoff) if selected < source_cutoff else None,
             'evidence_status': 'observed_source_bars' if any(b['time'] >= selected for b in bars)
                                else 'no_current_range_evidence'}
    current = _anchor_state(bars, hour_start, 'H1', cutoff, step) if not explicit else states[selected]
    review = {**evidence[selected], 'mode': 'current_market', 'as_of_ny': stamp(now),
              'analysis_cutoff_ny': stamp(source_cutoff), 'observed_through_ny': freshness['observed_through_ny'],
              'current_scope': scope, 'session_clock': session, 'freshness': freshness,
              'selection_status': progression, 'current_candle': current, 'ranges': facts,
              'response_contract': CURRENT_CONTRACT}
    peer = PAIRINGS.get(feed['asset'])
    paired = {'status': 'no_configured_comparison_pair'}
    if peer and states[selected]['complete'] and next_boundary(selected, tf) < source_cutoff:
        other = read_feed(db, peer, now=now)
        paired = {'status': 'insufficient_paired_evidence', 'comparison_asset': peer}
        if other.get('ok'):
            peer_capture = int(datetime.fromisoformat(other['captured_at_utc']).timestamp())
            peer_received = int(datetime.fromisoformat(other['received_at_utc']).timestamp())
            peer_bars, peer_step = _current_precision(_history_sets(db, other, selected, cutoff), selected,
                min(source_cutoff, peer_capture // 60 * 60, peer_received // 60 * 60))
            full = enrich_smt(compare_ranges(
                dict(asset=feed['asset'], symbol=feed['symbol'], bars=bars, step=step),
                dict(asset=peer, symbol=other['symbol'], bars=peer_bars, step=peer_step),
                selected, next_boundary(selected, tf), source_cutoff, tf))
            paired = _pick(full, ('ok', 'status', 'paired_coverage_complete', 'paired_through_ny'))
            qualified = [e for e in full.get('events', [])
                         if e.get('setup_interval', {}).get('qualified_smt') or e.get('setup_interval', {}).get('potential_smt')]
            paired.update(comparison_asset=peer, event_count=len(qualified), events=[{
                **_pick(e, ('direction', 'side', 'bar_open_ny', 'bar_close_ny', 'boneless_asset',
                            'potential_boneless_asset', 'boneless_status', 'setup_interval')),
                'own_objectives': deepcopy(e.get('objective_status', {}).get(feed['asset'], {}))}
                for e in qualified[:2]], more_events_available=len(qualified) > 2,
                detail_request={'tool': 'review_market_smt', 'args': {
                    'asset': feed['asset'], 'comparison_asset': peer, 'anchor_start_ny': stamp(selected),
                    'anchor_timeframe': tf, 'through_ny': stamp(source_cutoff)}})
    elif peer:
        paired = {'status': 'pending_complete_reference_or_observation', 'comparison_asset': peer}
    review['paired_context'] = paired
    result = {k: deepcopy(v) for k, v in feed.items() if k not in (
        'bars', 'bars_m1', 'bars_h1', 'native_h1_source', 'bid', 'ask')}
    result.update(review=review, available_precision_seconds=step,
                  available_from_ny=stamp(bars[0]['time']) if bars else None,
                  available_through_ny=freshness['observed_through_ny'])
    return result
