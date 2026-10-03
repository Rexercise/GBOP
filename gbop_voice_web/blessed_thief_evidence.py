"""Bounded opening-price evidence for the GTOP fifth entry, never trade fills.

A selected candle's open remains queryable in later candles. Its opening tick is
not a revisit. Directional evidence requires a source interval AFTER the parent
purge and an observed departure/return at that price. OHLC cannot order a sweep,
return and objective inside one source bar. Model 1 mappings are unrelated.
"""
from bisect import bisect_left
from gbop_voice_web.candle_evidence import interval, next_boundary, parse_time, stamp, summarize, timeframe

VERSION = 'blessed-thief-sequence-2026-10-03'
PAGE_SIZE = 12


def _event(bar, step):
    return {**interval(bar, step), **{k: bar[k] for k in ('open', 'high', 'low', 'close')}}


def _level_review(row, sources, purge, bearish, end, step, gap_at, objective, invalid_at):
    """Evaluate one immutable open across its remaining eligible CRT window."""
    start = parse_time(row['bar_open_ny'])
    level = row['opening_price']
    result = dict(status='opening_price_unverified' if level is None else 'no_directional_revisit_observed',
                  first_revisit=None, directional_trigger=None, opening_bar_order_unresolved=False,
                  gap_through_open=None, manipulation_extreme_as_of_trigger=None,
                  manipulation_extreme_at_cutoff=None, adverse_open_excursion=None, exact_fill_known=False)
    if level is None or purge is None or bearish is None:
        if level is not None:
            result['status'] = 'manipulation_or_direction_unverified'
        return result
    local = [b for b in sources if start <= b['time'] and b['time'] + step <= end]
    extreme_key = 'high' if bearish else 'low'
    local_adverse = [b for b in local if (b['high'] > level if bearish else b['low'] < level)]
    if local_adverse:
        extreme = (max if bearish else min)(local_adverse, key=lambda b: b[extreme_key])
        result['adverse_open_excursion'] = dict(first_observed=_event(local_adverse[0], step),
            extreme_price_at_cutoff=extreme[extreme_key], extreme_evidence=_event(extreme, step),
            scope='selected candle open; not an asserted new selected-range purge')
    parent_leg = [b for b in sources if purge['time'] <= b['time'] and b['time'] + step <= end]
    if parent_leg:
        extreme = (max if bearish else min)(parent_leg, key=lambda b: b[extreme_key])
        result['manipulation_extreme_at_cutoff'] = dict(price=extreme[extreme_key], evidence=_event(extreme, step))
    previous, departed = None, False
    for b in local:
        touches = b['low'] <= level <= b['high']
        distributed = b['close'] < level if bearish else b['close'] > level
        adverse = b['high'] > level if bearish else b['low'] < level
        # The selected opening price is necessarily present at its first tick;
        # that alone cannot prove a return. A first-bar round trip is ambiguous.
        if b['time'] == start or b['time'] <= purge['time']:
            if touches and adverse and distributed and b['time'] >= purge['time']:
                result['opening_bar_order_unresolved'] = True
        elif previous is not None and departed:
            if touches:
                fact = _event(b, step)
                if result['first_revisit'] is None:
                    result['first_revisit'] = fact
                if distributed:
                    prior_distribution = previous['close'] < level if bearish else previous['close'] > level
                    mode = 'return_and_reject_toward_distribution' if prior_distribution else 'cross_or_reclaim_toward_distribution'
                    result['directional_trigger'] = dict(mode=mode, evidence=fact,
                        known_at_ny=stamp(b['time'] + step), analytical_condition_only=True)
                    before = [x for x in parent_leg if x['time'] <= b['time']]
                    extreme = (max if bearish else min)(before, key=lambda x: x[extreme_key])
                    result['manipulation_extreme_as_of_trigger'] = dict(price=extreme[extreme_key],
                        evidence=_event(extreme, step), same_trigger_bar_order_unresolved=extreme['time'] == b['time'],
                        not_a_member_stop=True)
                    result['status'] = ('trigger_and_objective_same_source_bar_order_unresolved'
                        if objective and objective['time'] == b['time'] else
                        'trigger_in_invalidating_bar_order_unresolved'
                        if invalid_at == b['time'] + step else 'directional_revisit_observed')
                    break
            elif (previous['close'] - level) * (b['open'] - level) < 0:
                result['gap_through_open'] = _event(b, step)
        departed = departed or b['close'] != level
        previous = b
    if result['directional_trigger'] is None:
        result['status'] = ('unverified_after_source_gap' if gap_at is not None and gap_at < end else
                            'revisited_without_directional_close' if result['first_revisit'] else
                            'same_source_bar_order_unresolved' if result['opening_bar_order_unresolved'] else
                            'gap_through_open_without_observed_price_touch' if result['gap_through_open'] else
                            'no_directional_revisit_observed')
    return result


def blessed_thief_review(bars, anchor, anchor_tf, end, step, invalid_at=None,
                        candle_tf=None, page_from=None):
    tf = timeframe(candle_tf or anchor_tf)
    start = parse_time(anchor['end_ny'])
    result = dict(version=VERSION, entry_model='Blessed Thief', tier=3, timeframe=tf,
                  anchor_timeframe=anchor_tf, anchor_start_ny=anchor['start_ny'],
                  source_resolution_seconds=step, status='unverified_incomplete_anchor',
                  candles=[], total_candle_count=0, next_candle_start_ny=None,
                  execution_status='not_assessed', direction=None, manipulation=None,
                  objective=None, source_gap_at_ny=None, summary='',
                  response_contract='Opening PRICE, never automatic execution at opening TIME. '
                    'Read each candle opening_price and open_revisit chronology. Analytical evidence is not a member '
                    'fill, stop or realized R. The manipulation candle is included when its open can be revisited; '
                    'all subsequent selected-timeframe candles are paged. Preserve the original CRT anchor and '
                    'through_ny; pass next_candle_start_ny as blessed_thief_from_ny with the same '
                    'blessed_thief_timeframe to continue. Do not substitute Model 1 mapping or raw OHLC paging. '
                    'An objective ends eligibility; a midpoint alone does not. Missing data or same-bar order is unverified.')
    if not anchor.get('complete'):
        return result
    if end <= start:
        result['status'] = 'no_observation_window'
        return result
    if next_boundary(start, tf) - start < step or (next_boundary(start, tf) - start) % step:
        result['status'] = 'resolution_unavailable'
        return result
    invalid_at = invalid_at if invalid_at and invalid_at <= end else None
    end = min(end, invalid_at) if invalid_at else end
    sources = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end), key=lambda b: b['time'])
    prefix, expected, gap_at = [], start, None
    for b in sources:
        if b['time'] != expected:
            gap_at = expected
            break
        prefix.append(b)
        expected += step
    if gap_at is None and expected < end - (end - start) % step:
        gap_at = expected
    result['source_gap_at_ny'] = stamp(gap_at) if gap_at is not None else None
    purge = next((b for b in prefix if b['high'] > anchor['high'] or b['low'] < anchor['low']), None)
    bearish, objective = None, None
    if purge:
        both = purge['high'] > anchor['high'] and purge['low'] < anchor['low']
        bearish = None if both else purge['high'] > anchor['high']
        result['direction'] = None if both else 'bearish' if bearish else 'bullish'
        result['manipulation'] = dict(evidence=_event(purge, step),
            status='both_sides_same_source_bar_order_unresolved' if both else 'parent_boundary_purge_observed',
            purged_level=None if both else anchor['high'] if bearish else anchor['low'],
            observed_extreme=None if both else purge['high'] if bearish else purge['low'])
        if not both:
            level = anchor['low'] if bearish else anchor['high']
            objective = next((b for b in prefix if b['time'] > purge['time'] and
                              (b['low'] <= level if bearish else b['high'] >= level)), None)
            result['objective'] = dict(level=level, status='observed_after_manipulation' if objective else
                                       'unverified_after_source_gap' if gap_at is not None else
                                       'not_observed_before_invalidation' if invalid_at else 'not_observed_by_cutoff',
                                       evidence=_event(objective, step) if objective else None)
            if objective:
                end = min(end, objective['time'] + step)
                if invalid_at == end:
                    result['objective']['status'] = 'objective_in_invalidating_bar_order_unresolved'
    if purge is None and gap_at is None:
        result['status'] = 'no_manipulation_observed'
        result['summary'] = 'No selected-range manipulation was observed in the reviewed closed candles; Blessed Thief conditions are not established.'
        return result
    result.update(status=('objective_reached' if objective else 'range_invalidated' if invalid_at else
                          'unverified_after_source_gap' if gap_at is not None else
                          'direction_unresolved' if bearish is None else 'review_cutoff'),
                  window_end_ny=stamp(end), range_invalidated_at_ny=stamp(invalid_at) if invalid_at else None)
    # Build only bounded detail pages, but count every selected candle and expose
    # a continuation cursor. Later pages keep all earlier manipulation evidence.
    requested = parse_time(page_from) if page_from else start
    if requested < start or requested >= end:
        raise ValueError('Blessed Thief page start must be inside the post-anchor review window.')
    times = [b['time'] for b in sources]
    cursor, matched_page_boundary = start, False
    while cursor < end:
        stop = next_boundary(cursor, tf)
        if cursor == requested:
            matched_page_boundary = True
        include = purge is None or stop > purge['time']
        if include:
            result['total_candle_count'] += 1
            if cursor >= requested:
                if len(result['candles']) == PAGE_SIZE:
                    result['next_candle_start_ny'] = result['next_candle_start_ny'] or stamp(cursor)
                else:
                    subset = sources[bisect_left(times, cursor):bisect_left(times, min(stop, end))]
                    candle = summarize(subset, cursor, min(stop, end), step)
                    opening = subset[0]['open'] if subset and subset[0]['time'] == cursor else None
                    row = dict(timeframe=tf, bar_open_ny=stamp(cursor), bar_close_ny=stamp(stop),
                               opening_price=opening, candle_complete=candle['complete'] and stop <= end,
                               forming_at_cutoff=stop > end, source_bar_count=candle['bar_count'],
                               observed_ohlc={'open':opening, **{k:candle[k] for k in ('high','low','close') if k in candle}},
                               manipulation_ref='blessed_thief.manipulation',
                               role='manipulation_candle' if purge and cursor <= purge['time'] < stop else 'subsequent_candle',
                               thesis_validity_at_open='unverified_after_source_gap' if gap_at is not None and cursor >= gap_at else
                                                       'valid_in_observed_closes',
                               thesis_status_at_cutoff=result['status'], objective_status=result['objective'],
                               execution_status='not_assessed', automatic_entry_at_open=False)
                    row['open_revisit'] = _level_review(row, prefix, purge, bearish, end, step, gap_at, objective, invalid_at)
                    result['candles'].append(row)
        cursor = stop
    if not matched_page_boundary:
        raise ValueError('Blessed Thief page start must match a selected-timeframe candle boundary.')
    result['summary'] = (f"Blessed Thief: {result['total_candle_count']} {tf} opening-price levels in the reviewed "
                         f"post-manipulation sequence; {len(result['candles'])} detailed on this page. "
                         f"Sequence status: {result['status'].replace('_', ' ')}. "
                         'Each level requires a post-manipulation directional revisit; no member execution is inferred.')
    return result
