"""Same-selected-range double-purge evidence, never a trade or inducement verdict.

A later opposite Model 1 is an identity, not proof of this sequence. Both
boundaries must be purged in source-bar order while the selected range remains
valid, followed by an observed close back inside that SAME range. Source and
assigned closes have separate known-at times. The reversal's full objective is
the originally purged boundary; the selected-range midpoint is halfway only.
"""
from copy import deepcopy

from gbop_voice_web.candle_evidence import (
    assigned_purge_evidence, interval, next_boundary, parse_time, stamp, summarize,
)
from gbop_voice_web.candle_naming import candle_label, objective_identity, source_timeframe
from gbop_voice_web.target_approach import target_approach_measurement

VERSION = 'same-selected-range-double-purge-2026-10-03'


def _coverage(bars, start, end, step):
    facts = summarize(bars, start, end, step)
    return {key: facts[key] for key in ('start_ny', 'end_ny', 'complete',
        'bar_count', 'missing_bar_count', 'source_resolution_seconds')}


def _source(bar, step):
    return {**interval(bar, step), 'timeframe': source_timeframe(step),
            'known_at_ny': stamp(bar['time'] + step), 'close': bar['close']}


def _bounded_original(review, bars, end, step):
    """Rebuild the reviewed original outcome without leaking a later cutoff."""
    from gbop_voice_web.shift_narrative import directional_outcome, range_objectives
    bounded = deepcopy(review)
    bounded.pop('objectives', None)
    bounded.pop('directional_outcome', None)
    events = []
    for event in bounded.get('events', []):
        known = event.get('bar_close_ny', event.get('confirmed_at_ny'))
        if not known or parse_time(known) > end:
            continue
        assigned = event.get('assigned_purge', {})
        candle = assigned.get('assigned_candle', {})
        if candle and parse_time(candle['end_ny']) > end:
            event['assigned_purge'] = {'model1_qualification': 'unverified_incomplete_assigned_candle'}
        events.append(event)
    bounded['events'] = events
    invalid = bounded.get('invalidated_at_ny')
    if invalid and parse_time(invalid) > end:
        invalid = bounded['invalidated_at_ny'] = None
    cutoff = min(end, parse_time(invalid)) if invalid else end
    bounded['range_observation_coverage'] = _coverage(
        bars, parse_time(bounded['anchor']['end_ny']), cutoff, step)
    direction = (review.get('directional_outcome', {}).get('direction') or
                 review.get('direction_observed') or review.get('observed_direction'))
    purges = [e for e in events if e.get('kind', '').endswith('_side_purge')]
    if not purges or review.get('sweep_order') == 'unknown_within_same_bar':
        direction = None
    bounded['observed_direction'] = bounded['direction_observed'] = direction
    objectives = range_objectives(bounded)
    for objective in objectives:
        hit = objective.get('evidence')
        if objective.get('status') == 'observed_after_purge' and hit:
            prefix = _coverage(bars, parse_time(bounded['anchor']['end_ny']),
                               parse_time(hit['bar_close_ny']), step)
            objective['coverage_through_touch'] = prefix
            if not prefix['complete']:
                objective['status'] = 'unresolved_touch_validity_incomplete_coverage'
    bounded['objectives'] = objectives
    return directional_outcome(bounded)


def _assigned_return(bars, anchor, mapped, purge, side, end, step):
    assigned = assigned_purge_evidence(bars, anchor, mapped, purge, side, end, step)
    candle = assigned.get('assigned_candle')
    if not candle:
        return None
    cursor = parse_time(candle['start_ny'])
    while next_boundary(cursor, mapped) <= end:
        stop = next_boundary(cursor, mapped)
        row = summarize(bars, cursor, stop, step)
        if row['complete'] and anchor['low'] <= row['close'] <= anchor['high']:
            return {'bar_open_ny': stamp(cursor), 'bar_close_ny': stamp(stop),
                    'known_at_ny': stamp(stop), 'timeframe': mapped,
                    'precision_seconds': stop - cursor, 'source_resolution_seconds': step,
                    'close': row['close'], 'exact_tick_time_known': False}
        cursor = stop
    return None


def _identities(review, anchor, direction, cutoff):
    """Only identities belonging to this anchor, known by this review cutoff."""
    identities = []
    seen = set()
    candidates = (review.get('candle_lifecycle', {}).get('purge_candles', []) +
                  review.get('model1', {}).get('candles', []))
    for fact in candidates:
        start = fact.get('range_start_ny', fact.get('purged_range_start_ny'))
        end = fact.get('range_end_ny', fact.get('purged_range_end_ny'))
        close = fact.get('identified_at_ny', fact.get('bar_close_ny'))
        if (start != anchor['start_ny'] or end != anchor['end_ny'] or
                fact.get('direction') != direction or not close or parse_time(close) > cutoff):
            continue
        key = (fact.get('bar_open_ny'), fact.get('timeframe'), fact.get('purged_side'))
        if key in seen:
            continue
        seen.add(key)
        identities.append({key: deepcopy(fact[key]) for key in (
            'identity', 'purge_type', 'bar_open_ny', 'bar_close_ny', 'timeframe',
            'direction', 'purged_side', 'purged_level', 'purge_source_interval') if key in fact})
        identities[-1]['known_at_ny'] = close
    return sorted(identities, key=lambda fact: parse_time(fact['bar_close_ny']))


def _objectives(bars, anchor, direction, returned, cutoff, invalid, step):
    start = returned['time'] + step
    safe_end = max(start, cutoff - step if invalid else cutoff)
    rows = [b for b in bars if start <= b['time'] and b['time'] + step <= safe_end]
    coverage = _coverage(rows, start, safe_end, step)
    boundary_rows = [('same_source_return_bar', returned)]
    if invalid:
        boundary_rows += [('same_invalidating_close_source_bar', b) for b in bars
                          if b['time'] >= start and b['time'] + step == cutoff]
    bullish = direction == 'bullish'
    boundary = anchor['low'] if bullish else anchor['high']
    targets = (('midpoint', anchor['midpoint']),
               ('original_side', anchor['high'] if bullish else anchor['low']))
    result = {}
    for name, level in targets:
        identity = objective_identity('midpoint' if name == 'midpoint' else 'opposing_liquidity',
                                      direction, anchor)
        target = {'kind': name, 'level': level, **identity}
        fact = {'objective': name, 'level': level, **identity,
                'status': 'no_closed_post_return_bars', 'evidence': None,
                'distance_price_points': None, 'observed_distance_price_points': None,
                'closest_observed_price': None, 'closest_source_interval': None,
                'approach': None, 'boundary_observations': []}
        def measure(bar):
            return target_approach_measurement(target, bar['high'] if bullish else bar['low'],
                direction, reference_low=anchor['low'], reference_high=anchor['high'],
                reference_boundary=boundary)
        for reason, bar in boundary_rows:
            measured = measure(bar)
            fact['boundary_observations'].append({'reason': reason,
                'source_interval': _source(bar, step), 'gap_price_points': measured['gap_price_points'],
                'post_return_before_invalidation_order_known': False})
        if rows:
            closest = min(rows, key=lambda b: (measure(b)['gap_price_points'], b['time']))
            approach = measure(closest)
            gap = approach['gap_price_points']
            fact.update(closest_observed_price=closest['high'] if bullish else closest['low'],
                closest_source_interval=_source(closest, step), approach=approach,
                observed_distance_price_points=gap)
            if gap == 0:
                prefix = _coverage(rows, start, closest['time'] + step, step)
                fact['coverage_through_touch'] = prefix
                fact['evidence'] = _source(closest, step)
                if prefix['complete']:
                    fact.update(status='observed_after_return', distance_price_points=0)
                else:
                    fact['status'] = 'observed_touch_validity_unverified'
            elif not coverage['complete']:
                fact['status'] = 'unverified_incomplete_coverage'
            elif any(item['gap_price_points'] < gap for item in fact['boundary_observations']):
                fact['status'] = 'unverified_boundary_bar_order'
            else:
                fact.update(status=('not_observed_before_range_invalidation' if invalid else
                                    'not_observed_by_review_cutoff'), distance_price_points=gap)
        elif any(item['gap_price_points'] == 0 for item in fact['boundary_observations']):
            fact['status'] = 'unverified_boundary_bar_order'
        result[name] = fact
    return result, coverage


def double_purge_evidence(review, bars, end, step):
    """Pure evidence for one selected CRT and two separately scoped outcomes.

    `review` is the existing CRT/shift row with original directional events and
    optional assigned candle lifecycle. `end` is the review cutoff as Unix
    seconds; only fully closed source bars are used. This never mutates inputs,
    promotes a later range, adds variant labels or classifies inducement.
    """
    anchor = review.get('anchor', {})
    out = {'version': VERSION, 'status': 'unverified_direction', 'observed': False,
        'scope': 'same_selected_range', 'range_start_ny': anchor.get('start_ny'),
        'range_timeframe': anchor.get('timeframe', review.get('anchor_timeframe')),
        'review_cutoff_ny': stamp(end), 'source_resolution_seconds': step,
        'exact_tick_time_known': False, 'original_outcome': None,
        'original_first_purged_side': None, 'reverse_direction': None,
        'sequence': {}, 'opposite_identities': [],
        'reversal_thesis': {'status': 'not_assessed_no_valid_double_purge'},
        'response_contract': 'Double purge requires an ordered purge of both sides of the SAME '
            'selected range and an observed close back inside. An opposite Model 1 alone is '
            'insufficient. Keep source return, assigned return and their known-at closes distinct. '
            'The reverse full objective is the ORIGINAL FIRST PURGED SIDE; midpoint is halfway '
            'progress only. Preserve earlier original delivery separately. No variant, inducement, '
            'entry, fill, stop, profit or exact tick time is inferred.'}
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        out['status'] = 'unverified_source_resolution'
        return out
    if not anchor.get('complete') or parse_time(anchor['end_ny']) > end:
        out['status'] = 'unverified_incomplete_anchor'
        return out
    if anchor['high'] <= anchor['low']:
        out['status'] = 'unverified_nonpositive_range'
        return out
    rows = sorted((b for b in bars if parse_time(anchor['end_ny']) <= b['time']
                   and b['time'] + step <= end), key=lambda b: b['time'])
    original = _bounded_original(review, rows, end, step)
    out['original_outcome'] = original
    out['original_completion_preserved'] = original['status'] == 'opposing_liquidity_delivered'
    direction = original.get('direction')
    if direction not in ('bullish', 'bearish'):
        return out
    side = 'buy' if direction == 'bearish' else 'sell'
    opposite = 'sell' if side == 'buy' else 'buy'
    reverse = 'bullish' if side == 'buy' else 'bearish'
    invalid_ny = original.get('range_invalidated_at_ny')
    invalid = parse_time(invalid_ny) if invalid_ny else None
    cutoff = min(end, invalid) if invalid else end
    rows = [b for b in rows if b['time'] + step <= cutoff]
    out.update(original_first_purged_side=side, reverse_direction=reverse,
               validity_cutoff_ny=stamp(cutoff), range_invalidated_at_ny=invalid_ny)
    out['opposite_identities'] = _identities(review, anchor, reverse, cutoff)
    event = next((e for e in review.get('events', []) if e.get('kind') == side + '_side_purge'
                  and parse_time(e['bar_close_ny']) <= cutoff), None)
    if not event:
        out['status'] = 'unverified_initiating_purge'
        return out
    first_time = parse_time(event['bar_open_ny'])
    first_rows = [b for b in rows if b['time'] == first_time]
    crossed = lambda b, which: b['high'] > anchor['high'] if which == 'buy' else b['low'] < anchor['low']
    if (len(first_rows) != 1 or not crossed(first_rows[0], side) or
            parse_time(event['bar_close_ny']) - first_time != step):
        out['status'] = 'unverified_initiating_source_evidence'
        return out
    first = first_rows[0]
    opposing = next((b for b in rows if b['time'] >= first_time and crossed(b, opposite)), None)
    sequence = out['sequence']
    sequence.update(first_purge=_source(first, step), opposing_purge=None,
        opposing_delivery=deepcopy(original['opposing_liquidity'].get('evidence')),
        source_return_inside=None, assigned_return_inside=None, known_at_ny=None,
        coverage=_coverage(rows, parse_time(anchor['end_ny']), cutoff, step))
    if not opposing:
        out['status'] = ('not_observed_no_opposing_purge' if sequence['coverage']['complete'] else
                         'unverified_incomplete_coverage')
        return out
    sequence['opposing_purge'] = _source(opposing, step)
    if opposing['time'] == first_time:
        out['status'] = 'unverified_same_source_bar_order'
        return out
    returned = next((b for b in rows if b['time'] >= opposing['time'] and
                     anchor['low'] <= b['close'] <= anchor['high']), None)
    assigned = review.get('assigned_timeframe') or review.get('candle_lifecycle', {}).get('assigned_timeframe')
    sequence['assigned_return_inside'] = _assigned_return(
        rows, anchor, assigned, opposing, opposite, cutoff, step) if assigned else None
    if not returned:
        out['status'] = ('not_established_before_range_invalidation' if invalid else
                         'opposing_purge_observed_return_unverified')
        return out
    sequence['source_return_inside'] = _source(returned, step)
    known = returned['time'] + step
    prefix = _coverage(rows, parse_time(anchor['end_ny']), known, step)
    sequence.update(coverage_through_return=prefix, known_at_ny=stamp(known))
    if not prefix['complete']:
        out['status'] = 'unverified_incomplete_sequence_coverage'
        return out
    if invalid and known >= invalid:
        out['status'] = 'unverified_return_at_range_invalidation'
        return out
    if original['status'] != 'opposing_liquidity_delivered':
        out['status'] = 'unverified_original_delivery'
        return out
    out.update(status='observed', observed=True)
    objectives, coverage = _objectives(rows, anchor, reverse, returned, cutoff, invalid, step)
    full, mid = objectives['original_side'], objectives['midpoint']
    full_hit, mid_hit = (full['status'] == 'observed_after_return', mid['status'] == 'observed_after_return')
    unresolved = any('unverified' in item['status'] for item in objectives.values())
    status = ('original_side_delivered' if full_hit else 'midpoint_only' if mid_hit else
              'unverified' if unresolved else 'failed_before_objectives' if invalid else
              'pending_at_review_cutoff')
    out['reversal_thesis'] = {'direction': reverse, 'status': status,
        'objective_side': side, 'objective_level': anchor['high'] if side == 'buy' else anchor['low'],
        'objective_basis': 'original_first_purged_side_of_same_selected_range',
        'midpoint_level': anchor['midpoint'], 'midpoint_role': 'halfway_progress_not_full_objective',
        'window_start_ny': stamp(known), 'validity_cutoff_ny': stamp(cutoff),
        'coverage': coverage, 'objectives': objectives,
        'window_rule': 'Fully closed source bars after the source return close; exclude the '
            'source bar ending at selected-range invalidation. Boundary-bar extrema stay qualified.',
        'earlier_delivery_preserved_after_invalidation': bool(invalid and (full_hit or mid_hit))}
    out['spoken_summary'] = (
        f"The same selected range has an observed double purge: the {side}-side was purged first, "
        f"then its {opposite}-side, with a source close back inside in "
        f"{candle_label(stamp(returned['time']), source_timeframe(step))}. "
        f"The separate {reverse} reversal's full objective is the original {side}-side; "
        f"the midpoint is halfway progress. Its outcome is {status.replace('_', ' ')}. "
        'Earlier original-direction delivery remains recorded separately.')
    return out
