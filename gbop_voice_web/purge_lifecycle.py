"""Closed-candle GTOP lifecycle evidence; no fills, stops, or profit inference.

Use the owner's full-extreme, strict assigned-close CSD convention explicitly. A second body purge is
reported as an observation awaiting rejection, not silently treated as a
successful Turtle Soup. Coverage and chronology travel with every conclusion.
"""
from __future__ import annotations
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web import cisd_rule


def _bar(candle):
    return {k: candle[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close')}


def _form(candle, level, side):
    if side == 'buy':
        if candle['high'] <= level:
            return None
        if candle['open'] <= level < candle['close']:
            return 'body_cross_and_close'
        if max(candle['open'], candle['close']) <= level:
            return 'wick_only'
    else:
        if candle['low'] >= level:
            return None
        if candle['close'] < level <= candle['open']:
            return 'body_cross_and_close'
        if min(candle['open'], candle['close']) >= level:
            return 'wick_only'
    return 'outside_or_returning_body'


def _after_event(bars, source, event_end, through, step, anchor, side, invalid_at):
    """Objective order starts at the CLOSED event; same-bar touches stay distinct."""
    from gbop_voice_web.candle_evidence import stamp, parse_time
    begins = parse_time(source['start_ny'])
    rows = [b for b in bars if event_end <= b['time'] and b['time'] + step <= through]
    full = [b['time'] for b in rows] == list(range(event_end, through - step + 1, step)) and (through-event_end) % step == 0
    outcomes = {}
    for name, level in (('midpoint', anchor['midpoint']),
                        ('opposing_liquidity', anchor['low'] if side == 'buy' else anchor['high'])):
        touch = lambda b: b['low'] <= level <= b['high']
        same = source['low'] <= level <= source['high']
        earlier_in_event = [b for b in bars if begins <= b['time'] and b['time'] + step <= event_end and touch(b)]
        hit = next((b for b in rows if touch(b) and (invalid_at is None or b['time'] + step < invalid_at)), None)
        after_invalid = next((b for b in rows if touch(b) and invalid_at is not None and b['time'] >= invalid_at), None)
        boundary_hit = next((b for b in rows if touch(b) and invalid_at is not None and b['time'] < invalid_at <= b['time'] + step), None)
        prefix_start = parse_time(anchor['end_ny'])
        prefix_complete = bool(hit) and [b['time'] for b in bars if prefix_start <= b['time'] <= hit['time']] == list(range(prefix_start, hit['time'] + step, step))
        if hit:
            status = 'delivered_before_range_invalidation' if prefix_complete else 'touch_observed_range_validity_unverified'
        elif boundary_hit:
            status = 'touch_in_invalidating_source_bar_order_unresolved'
        elif same:
            status = 'event_candle_touch_order_unresolved'
        elif not full:
            status = 'unverified_incomplete_coverage'
        elif invalid_at is not None:
            status = 'range_invalidated_before_verified_delivery'
        else:
            status = 'not_observed_by_review_cutoff'
        describe = lambda b: ({'bar_open_ny': stamp(b['time']), 'bar_close_ny': stamp(b['time'] + step)} if b else None)
        outcomes[name] = {'level': level, 'status': status,
                          **objective_identity(name, 'bearish' if side == 'buy' else 'bullish', anchor),
                          'first_verified_later_touch': describe(hit),
                          'touch_in_event_candle': same,
                          'event_source_touch': describe(earlier_in_event[0]) if earlier_in_event else None,
                          'touch_in_invalidating_source_bar': describe(boundary_hit),
                          'later_touch_after_range_invalidation': describe(after_invalid),
                          'source_precision_seconds': step}
    return outcomes


def _life(bars, model, candles, anchor, through, step, invalid_at):
    from gbop_voice_web.candle_evidence import parse_time, stamp
    formed = parse_time(model['bar_close_ny'])
    following = [c for c in candles if parse_time(c['start_ny']) >= formed]
    side = model['purged_side']
    bearish = side == 'buy'
    extreme = model['high'] if bearish else model['low']
    body_open = model['open']
    confirmation = cisd_rule.reference(model, bearish)
    csd, first_sweep, return_inside, wick_soup = None, None, None, None
    continuous, wick_verified = True, False
    for c in following:
        if not c['complete']:
            continuous = False
            continue
        t, stop = parse_time(c['start_ny']), parse_time(c['end_ny'])
        if invalid_at is not None and stop >= invalid_at:
            break  # No new valid-thesis confirmation at/after the invalidating close.
        form = _form(c, extreme, side)
        if form and first_sweep is None:
            first_sweep = {'candle': _bar(c), 'purged_level': extreme, 'form': form,
                           'continuous_since_model1': continuous,
                           'immediate_next_assigned_candle': t == formed,
                           'before_csd_close_verified': continuous,
                           'close_back_inside_model1': model['low'] <= c['close'] <= model['high'],
                           'close_back_through_swept_extreme': c['close'] <= extreme if bearish else c['close'] >= extreme}
        if first_sweep and return_inside is None and (c['close'] <= extreme if bearish else c['close'] >= extreme):
            return_inside = _bar(c)
        if wick_soup is None and cisd_rule.wick_soup(c, model, bearish):
            wick_soup = _bar(c)
            wick_verified = continuous
        cross = cisd_rule.confirms(c, model, bearish)
        if cross:
            csd = {'candle': _bar(c), 'confirmed_at_ny': c['end_ny'],
                   **confirmation,
                   'first_in_continuous_window': continuous}
            if first_sweep and first_sweep['candle']['end_ny'] == c['end_ny']:
                first_sweep['before_csd_close_verified'] = False
            break
    observation_end = parse_time(csd['confirmed_at_ny']) if csd else min(through, invalid_at or through)
    expected_cursor = formed
    pre_complete = True
    for c in following:
        t, stop = parse_time(c['start_ny']), parse_time(c['end_ny'])
        if t >= observation_end:
            break
        if t != expected_cursor or not c['complete'] or stop > observation_end:
            pre_complete = False
        expected_cursor = stop
    if expected_cursor != observation_end:
        pre_complete = False
    same_csd_sweep = bool(first_sweep and csd and
                          first_sweep['candle']['end_ny'] == csd['confirmed_at_ny'])
    if wick_soup and wick_verified:
        soup_status = 'super_soup_observed'
    elif same_csd_sweep:
        soup_status = 'same_candle_as_csd_order_unresolved'
    elif first_sweep:
        if not first_sweep['before_csd_close_verified']:
            soup_status = 'sweep_observed_pre_csd_order_unverified'
        elif return_inside:
            soup_status = 'body_purge_then_return_observed'
        else:
            soup_status = 'body_purge_observed_rejection_unverified'
    else:
        soup_status = ('no_subsequent_closed_candle' if not any(c['complete'] for c in following) else
                       'not_observed_in_complete_pre_csd_window' if pre_complete
                       else 'unverified_incomplete_pre_csd_window')
    ss = {'status': soup_status, 'sweep': first_sweep, 'pre_csd_wick': wick_soup, 'first_close_back_through_swept_extreme': return_inside,
          'formation_is_separate_from_csd_and_execution': True,
          'pre_csd_window_complete': pre_complete,
          'body_purge_is_not_automatically_a_successful_reversal': True}
    if first_sweep:
        source = first_sweep['candle']
        stop = parse_time(source['end_ny'])
        ss['objectives_after_sweep_candle_close'] = _after_event(bars, source, stop, through, step, anchor, side, invalid_at)
        later = [c for c in following if c['complete'] and parse_time(c['start_ny']) >= stop]
        adverse = next((c for c in later if (c['close'] > source['high'] if bearish else c['close'] < source['low'])), None)
        ss['later_adverse_close_beyond_soup_candle_extreme'] = _bar(adverse) if adverse else None
        ss['adverse_close_is_not_a_recorded_stopout'] = True
    next_c = following[0] if following else None
    life = {'assessed_through_ny': stamp(through), 'source_resolution_seconds': step,
            'next_assigned_candle': (_bar(next_c) if next_c and next_c['complete'] else None),
            'next_assigned_candle_status': ('closed' if next_c and next_c['complete'] else
                                           'incomplete' if next_c else 'not_available_yet'),
            'csd': {'status': 'observed' if csd else 'not_assessed_no_subsequent_closed_candle' if not any(c['complete'] for c in following) else
                    'not_observed_in_complete_window' if pre_complete else 'unverified_incomplete_window',
                    **confirmation, 'evidence': csd}, 'super_soup': ss,
            'outer_range_invalidated_at_ny': stamp(invalid_at) if invalid_at else None,
            'execution_status': 'not_assessed', 'stopout_status': 'requires_member_stop_rule'}
    source = {'start_ny': model['bar_open_ny'], 'end_ny': model['bar_close_ny'],
              **{k: model[k] for k in ('open', 'high', 'low', 'close')}}
    life['objectives_after_model1_close'] = _after_event(bars, source, formed, through, step, anchor, side, invalid_at)
    if csd:
        stop = parse_time(csd['confirmed_at_ny'])
        after = [c for c in following if c['complete'] and parse_time(c['start_ny']) >= stop]
        retest = next((c for c in after if c['low'] <= body_open <= c['high']), None)
        adverse = next((c for c in after if (c['close'] > extreme if bearish else c['close'] < extreme)), None)
        life['first_body_open_retest_after_csd'] = _bar(retest) if retest else None
        life['later_adverse_close_beyond_model1_extreme'] = _bar(adverse) if adverse else None
        life['objectives_after_csd_close'] = _after_event(bars, csd['candle'], stop, through, step, anchor, side, invalid_at)
    return life


def attach_lifecycles(bars, anchor, mapped, through, step, model1, invalid_at=None):
    """Enrich existing Model 1 facts and add strict assigned-timeframe wick soups."""
    from gbop_voice_web.candle_evidence import next_boundary, parse_time, summarize
    if not anchor.get('complete') or not mapped:
        return
    start = parse_time(anchor['end_ny'])
    duration = next_boundary(start, mapped) - start
    if duration < step or duration % step:
        return
    bars = sorted(bars, key=lambda b: b['time'])
    selected = [b for b in bars if start <= b['time'] and b['time'] + step <= through]
    candles, cursor, index = [], start, 0
    while cursor < through:
        stop = next_boundary(cursor, mapped)
        left = index
        while index < len(selected) and selected[index]['time'] < stop:
            index += 1
        c = summarize(selected[left:index], cursor, min(stop, through), step)
        if stop > through:
            c['complete'] = False
        candles.append(c)
        cursor = stop
    wick, count = [], 0
    for c in candles:
        if not c['complete'] or (invalid_at is not None and parse_time(c['end_ny']) > invalid_at):
            continue
        for side, boundary in (('buy', anchor['high']), ('sell', anchor['low'])):
            if _form(c, boundary, side) == 'wick_only':
                count += 1
                if len(wick) < 32:
                    wick.append({'identity': 'Turtle Wick Soup', 'timeframe': mapped,
                                 'purged_side': side, 'purged_level': boundary, 'candle': _bar(c),
                                 'is_model1_body_candle': False,
                                 'objectives_after_wick_close': _after_event(
                                     bars, _bar(c), parse_time(c['end_ny']), through, step, anchor, side, invalid_at)})
    model1['wick_soups'] = {'identified_count': count, 'candles': wick,
                           'truncated': count > len(wick),
                           'status': ('identified' if count else 'not_observed_in_complete_window'
                                      if candles and all(c['complete'] for c in candles) else 'unverified_incomplete_window')}
    model1['lifecycles'] = [
        {'model1_candle_open_ny': model['bar_open_ny'],
         **_life(bars, model, candles, anchor, through, step, invalid_at)}
        for model in model1.get('candles', [])]
    # Candle identity records remain immutable as new follow-up bars arrive.
    model1['lifecycle_contract'] = ('Report the Model 1 candle first, then its same-timeframe sequel. '
        'A wick-only soup is not a Model 1 body candle. Super Soup sweep, return, CSD, retest, '
        'adverse closes, outer-range invalidation and objectives are distinct observations. '
        'Body-purge-only is not automatically a completed reversal. Delivery after invalidation '
        'is later price movement, not retroactive valid-thesis success. No inferred entry or profit. '
        'A missing or forming candle is unknown, not no Super Soup. CSD requires a strict close below Model 1 full low bearish, above full high bullish; a wick or equality is not confirmation.')
    assessed = any(x['csd']['status'] != 'not_assessed_no_subsequent_closed_candle' for x in model1['lifecycles'])
    if assessed:
        model1['csd_status'] = 'assessed_per_candle'
        model1['super_soup_status'] = 'assessed_per_candle'
