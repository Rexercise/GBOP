"""Continue proven same-parent delivery without defining another named play.

Only a strict later boundary purge followed by this parent's own-timeframe
return opens another observed leg. A target touch alone is not a new reversal.
This is bounded by the review cutoff/invalidation and closed source coverage.
"""
from gbop_voice_web.candle_evidence import interval, next_boundary, parse_time, stamp, summarize
from gbop_voice_web.double_purge import _objectives, _selected_return


def _resoup_manner(anchor, bars, confirmation, target, direction, step):
    """Existing V6 condition, frozen at this leg's objective milestone."""
    if target.get('status') != 'observed_after_confirmation' or not target.get('evidence'):
        return None
    stop = parse_time(target['evidence']['bar_open_ny'])
    start = parse_time(confirmation['bar_open_ny'])
    end = parse_time(confirmation['known_at_ny'])
    first = summarize(bars, start, end, step)
    if not first['complete']:
        return None
    bearish = direction == 'bearish'
    cursor = end
    while next_boundary(cursor, anchor['timeframe']) <= stop:
        end = next_boundary(cursor, anchor['timeframe'])
        candle = summarize(bars, cursor, end, step)
        if not candle['complete']:
            return None
        if ((candle['high'] > first['high'] if bearish else candle['low'] < first['low'])
                and anchor['low'] <= candle['close'] <= anchor['high']):
            return {'code': 'V6', 'name': 're-soup', 'acting_candle_start_ny': stamp(cursor),
                    'first_manipulation_start_ny': confirmation['bar_open_ny'],
                    'known_at_ny': stamp(end)}
        cursor = end
    return None


def continuation_legs(review, bars, end, step):
    double = review.get('double_purge', {})
    previous = double.get('reversal_thesis', {})
    if not double.get('observed') or previous.get('status') != 'original_side_delivered':
        return {'legs': []}
    anchor = review['anchor']
    invalid = review.get('invalidated_at_ny')
    cutoff = min(end, parse_time(invalid)) if invalid else end
    rows = sorted((b for b in bars if b['time'] + step <= cutoff), key=lambda b: b['time'])
    previous_confirmation = parse_time(double['confirmed_at_ny'])
    target_time = parse_time(previous['objectives']['original_side']['evidence']['bar_open_ny'])
    direction = previous['direction']
    result = {'legs': []}
    # Each confirmation advances at least one parent candle; no repeated/tied
    # source event can be promoted to another leg of the same range.
    while True:
        side = 'buy' if direction == 'bullish' else 'sell'
        purge = next((b for b in rows if b['time'] >= target_time and
                      (b['high'] > anchor['high'] if side == 'buy' else b['low'] < anchor['low'])), None)
        if purge is None:
            break
        confirmation, status = _selected_return(rows, anchor, purge, cutoff, step)
        candidate = {'purged_side': side, 'purge': interval(purge, step), 'confirmation_status': status}
        result['next_boundary'] = candidate
        if not confirmation:
            break
        known = parse_time(confirmation['known_at_ny'])
        if known <= previous_confirmation or invalid and known >= parse_time(invalid):
            break
        coverage = summarize(rows, previous_confirmation, known, step)
        if not coverage['complete']:
            candidate['confirmation_status'] = 'unverified_incomplete_sequence_coverage'
            break
        direction = 'bearish' if side == 'buy' else 'bullish'
        objectives, coverage = _objectives(rows, anchor, direction, {'time': known-step},
                                          cutoff, bool(invalid), step, confirmed=True)
        full, mid = objectives['original_side'], objectives['midpoint']
        full_hit = full['status'] == 'observed_after_confirmation'
        mid_hit = mid['status'] == 'observed_after_confirmation'
        unresolved = any('unverified' in o['status'] for o in objectives.values())
        state = ('opposing_liquidity_delivered' if full_hit else 'midpoint_only' if mid_hit else
                 'unverified' if unresolved else 'failed_before_objectives' if invalid else 'pending_at_review_cutoff')
        leg = {'leg_index': len(result['legs']) + 3, 'direction': direction,
               'first_purge': interval(purge, step), 'confirmation': confirmation, 'status': state,
               'objectives': {}, 'coverage_complete': coverage['complete']}
        for key, target in (('midpoint', mid), ('opposing_liquidity', full)):
            leg['objectives'][key] = {'status': target['status'], 'level': target['level'],
                'evidence': {k: target['evidence'][k] for k in
                    ('bar_open_ny', 'bar_close_ny', 'precision_seconds') if k in target['evidence']}
                    if target.get('evidence') else None}
            manner = _resoup_manner(anchor, rows, confirmation, target, direction, step)
            leg['objectives'][key]['delivery_manner'] = ({'primary_code': 'V6', 'known_at_ny': manner['known_at_ny']}
                if manner else {'status': 'no_confirmed_resoup_at_milestone'})
            if key == 'opposing_liquidity' and manner:
                leg['variant'] = manner
        returned = next((b for b in rows if b['time'] >= purge['time'] and
                         b['time'] + step <= known and anchor['low'] <= b['close'] <= anchor['high']), None)
        if returned:
            development, _ = _objectives(rows, anchor, direction, returned, known, False, step)
            if any(t['status'] in ('observed_after_return', 'observed_touch_validity_unverified',
                                  'unverified_boundary_bar_order') for t in development.values()):
                leg['pre_confirmation_development'] = {
                    'status': 'pre_confirmation_only', 'direction': direction,
                    'known_at_ny': stamp(known), 'objectives': {
                        k: {'status': t['status'], 'evidence': t.get('evidence')}
                        for k, t in development.items()}}
        result['legs'].append(leg)
        result.pop('next_boundary')
        if not full_hit:
            break
        previous_confirmation = known
        target_time = parse_time(full['evidence']['bar_open_ny'])
    return result


def pre_confirmation_sentence(leg):
    from gbop_voice_web.candle_naming import candle_label, source_timeframe
    development = leg.get('pre_confirmation_development', {})
    hits = []
    for key, label in (('midpoint', 'midpoint'), ('original_side', 'full opposing liquidity')):
        target = development.get('objectives', {}).get(key, {})
        if target.get('status') == 'observed_after_return' and target.get('evidence'):
            event = target['evidence']
            hits.append(label + ' in ' + candle_label(event['bar_open_ny'], source_timeframe(event.get('precision_seconds'))))
    return (' Before own-timeframe confirmation, physical delivery reached ' + ', '.join(hits)
            + '; this is separate from post-confirmation delivery.') if hits else ''


def continuation_sentence(sequence, anchor_start, timeframe):
    """Factual repeated legs; neither a second play nor execution permission."""
    from gbop_voice_web.candle_naming import candle_label, source_timeframe
    clauses = []
    for leg in sequence.get('legs', []):
        label = 'triple purge' if leg['leg_index'] == 3 else f"purge {leg['leg_index']}"
        parent = candle_label(anchor_start, timeframe).replace(' candle', ' parent')
        confirmation = leg['confirmation']
        text = (f"Same {parent.removeprefix('the ')} {label}: {leg['direction']}, confirmed on "
                f"{candle_label(confirmation['bar_open_ny'], timeframe)}'s closure")
        for key, label in (('midpoint', 'midpoint'), ('opposing_liquidity', 'full opposing liquidity')):
            target = leg['objectives'][key]
            if target['status'] == 'observed_after_confirmation' and target.get('evidence'):
                event = target['evidence']
                text += f"; {label} in {candle_label(event['bar_open_ny'], source_timeframe(event.get('precision_seconds')))}"
        if leg.get('variant'):
            text += '; final V6 re-soup manner (earlier midpoint manner stays separate)'
        if leg['status'] != 'opposing_liquidity_delivered':
            text += '; full outcome ' + leg['status'].replace('_', ' ')
        clauses.append(text + '.' + pre_confirmation_sentence(leg))
    return ' '.join(clauses)
