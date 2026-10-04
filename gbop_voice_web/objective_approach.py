"""Bounded, directional distances from retained source OHLC, never tick paths."""
from decimal import Decimal

from gbop_voice_web.candle_evidence import interval, parse_time, stamp, summarize
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.target_approach import target_approach_measurement


def objective_approach(review, bars, end, step):
    """Measure the closest approach after the initiating purge while valid.

    The purge source bar cannot establish post-purge order. Similarly, an
    extremum in the source bar ending at an invalidating close cannot prove
    delivery *before* that close. Keep these observations separately rather
    than letting them create a verified zero-distance or nearest-price claim.
    A cutoff without invalidation includes its last fully closed source bar.
    """
    anchor = review.get('anchor', {})
    direction = (review.get('observed_direction') or review.get('direction_observed')
                 or review.get('directional_outcome', {}).get('direction'))
    out = {
        'status': 'unverified_direction', 'direction': direction,
        'range_start_ny': anchor.get('start_ny'),
        'range_timeframe': anchor.get('timeframe', review.get('anchor_timeframe')),
        'source_resolution_seconds': step, 'exact_tick_time_known': False,
        'distance_unit': 'provider_price_points', 'objectives': {},
        'response_contract': 'Distance is measured from retained source OHLC in the stated '
            'directional post-purge window, not ticks, pips, fills or trade performance. '
            'A positive observed distance with missing bars is not a verified closest approach. '
            'Same-source-bar purge/invalidation order remains unknown. Earlier verified '
            'target delivery is not erased by later invalidation or missing later bars.'}
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        out['status'] = 'unverified_source_resolution'
        return out
    if not anchor.get('complete'):
        out['status'] = 'unverified_incomplete_anchor'
        return out
    if anchor['high'] <= anchor['low']:
        out['status'] = 'unverified_nonpositive_range'
        return out
    if direction not in ('bearish', 'bullish') or review.get('sweep_order') == 'unknown_within_same_bar':
        return out
    side = 'buy' if direction == 'bearish' else 'sell'
    purges = [event for event in review.get('events', [])
              if event.get('kind') == side + '_side_purge']
    if not purges:
        out['status'] = 'unverified_initiating_purge'
        return out
    purge = min(purges, key=lambda event: parse_time(event['bar_open_ny']))
    purge_start = parse_time(purge['bar_open_ny'])
    purge_close = parse_time(purge['bar_close_ny'])
    if purge_close - purge_start != step:
        out['status'] = 'unverified_source_resolution'
        return out
    invalid_ny = review.get('invalidated_at_ny')
    if not invalid_ny:
        invalid_ny = next((e.get('confirmed_at_ny') for e in review.get('events', [])
                           if e.get('kind') == 'range_invalidated'), None)
    invalid = parse_time(invalid_ny) if invalid_ny else None
    cutoff = min(end, invalid) if invalid is not None else end
    invalid_is_boundary = invalid is not None and invalid <= end
    start = max(parse_time(anchor['end_ny']), purge_close)
    # This excludes only the final source bar, not the whole invalidating H1.
    safe_end = cutoff - step if invalid_is_boundary else cutoff
    safe_end = max(start, safe_end)
    rows = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= safe_end),
                  key=lambda b: b['time'])
    coverage = summarize(rows, start, safe_end, step)
    # Complete post-purge prices alone cannot prove this range survived an
    # earlier missing anchor-timeframe close. The supplied initiating purge
    # interval is authoritative; verify the history before that interval too.
    pre_start = parse_time(anchor['end_ny'])
    pre_coverage = summarize(bars, pre_start, purge_start, step)
    pre_complete = purge_start == pre_start or pre_coverage['complete']
    out.update(status='evaluated', initiating_purge={
        'bar_open_ny': stamp(purge_start), 'bar_close_ny': stamp(purge_close),
        'precision_seconds': step, 'exact_tick_time_known': False},
        window_start_ny=stamp(start), review_cutoff_ny=stamp(end),
        validity_cutoff_ny=stamp(cutoff), range_invalidated_at_ny=invalid_ny,
        coverage={key: coverage[key] for key in ('start_ny', 'end_ny', 'complete',
            'bar_count', 'missing_bar_count', 'source_resolution_seconds')},
        window_rule='Strictly after the initiating source purge bar; fully closed bars '
            'before the invalidating close, or through the review cutoff if still valid.',
        invalidating_source_bar_excluded=invalid_is_boundary)
    out['pre_purge_coverage_complete'] = pre_complete
    out['pre_purge_missing_bar_count'] = pre_coverage['missing_bar_count']
    boundary_rows = []
    for b in bars:
        if b['time'] == purge_start and b['time'] + step <= cutoff:
            boundary_rows.append(('same_initiating_purge_source_bar', b))
        elif invalid_is_boundary and b['time'] + step == cutoff and b['time'] >= purge_close:
            boundary_rows.append(('same_invalidating_close_source_bar', b))

    def measure(bar, level):
        price = bar['low'] if direction == 'bearish' else bar['high']
        gap = (Decimal(str(price)) - Decimal(str(level)) if direction == 'bearish'
               else Decimal(str(level)) - Decimal(str(price)))
        return price, float(max(Decimal(0), gap))

    for name, level in [('midpoint', anchor['midpoint']), ('opposing_liquidity',
                         anchor['low'] if direction == 'bearish' else anchor['high'])]:
        fact = {'objective': name, 'level': level,
                **objective_identity(name, direction, anchor),
                'status': 'unverified_no_post_purge_bars', 'distance_price_points': None,
                'observed_distance_price_points': None, 'closest_observed_price': None,
                'closest_source_interval': None, 'first_touch_order_verified': False,
                'boundary_observations': []}
        for reason, bar in boundary_rows:
            price, gap = measure(bar, level)
            fact['boundary_observations'].append({'reason': reason, 'observed_price': price,
                'distance_price_points': gap, 'source_interval': interval(bar, step),
                'post_purge_before_invalidation_order_known': False})
        if rows:
            closest = min(rows, key=lambda b: (measure(b, level)[1], b['time']))
            price, distance = measure(closest, level)
            fact.update(observed_distance_price_points=distance, closest_observed_price=price,
                        closest_source_interval=interval(closest, step))
            boundary_might_be_closer = any(x['distance_price_points'] < distance
                                          for x in fact['boundary_observations'])
            if distance == 0:
                # A missing earlier source bar could hide an invalidating
                # close. A physical touch after that gap is not proven valid
                # delivery. Conversely, gaps AFTER an already observed touch
                # cannot revoke the complete prefix that established it.
                prefix = summarize(rows, start, closest['time'] + step, step)
                fact['coverage_through_touch'] = {key: prefix[key] for key in (
                    'start_ny', 'end_ny', 'complete', 'bar_count', 'missing_bar_count')}
                if prefix['complete'] and pre_complete:
                    fact.update(status='target_reached_in_verified_post_purge_bar',
                                distance_price_points=0.0,
                                first_touch_order_verified=not any(
                                    x['distance_price_points'] == 0 and
                                    x['reason'] == 'same_initiating_purge_source_bar'
                                    for x in fact['boundary_observations']))
                else:
                    fact['status'] = 'observed_target_touch_validity_unverified'
            elif not coverage['complete'] or not pre_complete:
                fact['status'] = 'unverified_incomplete_window'
            elif boundary_might_be_closer:
                fact['status'] = 'unverified_boundary_bar_order'
            else:
                fact.update(status='closest_approach_verified', distance_price_points=distance)
        if fact['closest_observed_price'] is not None:
            fact['target_approach'] = target_approach_measurement(
                {'kind': name, 'level': level, 'anchor_start_ny': anchor['start_ny'],
                 'timeframe': anchor.get('timeframe', review.get('anchor_timeframe'))},
                fact['closest_observed_price'], direction,
                reference_low=anchor['low'], reference_high=anchor['high'],
                reference_boundary=anchor['high'] if direction == 'bearish' else anchor['low'])
            fact['target_approach']['evidence_status'] = fact['status']
        out['objectives'][name] = fact
    return out
