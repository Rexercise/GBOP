"""Ordered GTOP candle facts, independent of a trader's orders or P/L.

CSD uses the opposite edge of the ORIGINAL purge candle's real body (its open).
Retests and later closes are named by the exact reference tested. They never
silently become a member's stop or the parent range's invalidation rule.
"""
from datetime import datetime
from gbop_voice_web.candle_evidence import summarize, stamp, parse_time, next_boundary, timeframe

VERSION = 'candle-lifecycle-local-function-2026-10-03'
MAX_IDENTITIES = 32


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _fact(candle):
    return {**{k: candle[k] for k in ('open', 'high', 'low', 'close')},
            'bar_open_ny': candle['start_ny'], 'bar_close_ny': candle['end_ny'],
            'exact_tick_time_known': False}


def _assigned(bars, start, end, tf, step):
    """Single pass over source bars; missing buckets stay explicitly incomplete."""
    rows, cursor, index = [], start, 0
    bars = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end),
                  key=lambda b: b['time'])
    while cursor < end:
        stop = next_boundary(cursor, tf)
        if stop > end:
            rows.append({'start_ny': stamp(cursor), 'end_ny': stamp(stop),
                         'complete': False, 'forming': True})
            break
        left = index
        while index < len(bars) and bars[index]['time'] < stop:
            index += 1
        rows.append(summarize(bars[left:index], cursor, stop, step))
        cursor = stop
    return rows


def _objectives(bars, start, end, anchor, bearish, step, invalid_at, origin=None):
    """First source-bar touches after a closed event, not filled trade targets."""
    result, cursor, gap = {}, start, False
    later = []
    for bar in bars:
        if bar['time'] < start or bar['time'] + step > end:
            continue
        if bar['time'] != cursor:
            gap = True
            break
        later.append(bar)
        cursor += step
    gap = gap or cursor < end
    for name, level in (('midpoint', anchor['midpoint']),
                        ('opposing_liquidity', anchor['low'] if bearish else anchor['high'])):
        hit = next((b for b in later if b['low'] <= level <= b['high']), None)
        same_origin = bool(origin and origin['low'] <= level <= origin['high'])
        status = ('observed_after_event' if hit else
                  'unverified_incomplete_coverage' if gap else
                  'not_observed_before_range_invalidation' if invalid_at else
                  'not_observed_by_review_cutoff')
        # A final-bar touch and invalidating close do not reveal their tick order.
        if hit and invalid_at and hit['time'] + step == invalid_at:
            status = 'touch_in_invalidating_bar_order_unresolved'
        result[name] = {'level': level, 'status': status,
                        'same_formation_bar_touch_order_unknown': same_origin,
                        'first_touch': ({'bar_open_ny': stamp(hit['time']),
                                         'bar_close_ny': stamp(hit['time'] + step),
                                         'precision_seconds': step,
                                         'exact_tick_time_known': False} if hit else None)}
    return result


def _follow(candle, side, later, bars, anchor, end, step, invalid_at, tf):
    bearish = side == 'buy'
    reference = candle['open']
    extreme = candle['high'] if bearish else candle['low']
    body_far_edge = candle['close']
    origin_end = parse_time(candle['end_ny'])
    csd, soup, retest, disrespect, breach = None, None, None, None, None
    first_soup_purge, gap_at = None, None
    ambiguous_soup = None
    for row in later:
        if not row['complete']:
            gap_at = row['start_ny']
            break
        confirms = row['close'] < reference if bearish else row['close'] > reference
        swept = row['high'] > extreme if bearish else row['low'] < extreme
        if csd is None:
            if swept and first_soup_purge is None:
                first_soup_purge = _fact(row)
            returned = candle['low'] <= row['close'] <= candle['high']
            if first_soup_purge and returned and soup is None and ambiguous_soup is None:
                event = {'purge': first_soup_purge, 'return_candle': _fact(row),
                         'level': extreme, 'timeframe': tf}
                if confirms:
                    ambiguous_soup = event
                else:
                    soup = event
            if confirms:
                csd = {**_fact(row), 'reference_level': reference, 'timeframe': tf,
                       'confirmed_at_ny': row['end_ny'], 'rule': 'close_through_original_body_open'}
        else:
            # A touch on the confirmation candle itself is not a later retest.
            if retest is None and row['low'] <= reference <= row['high']:
                retest = {**_fact(row), 'level': reference, 'timeframe': tf}
            if disrespect is None and (row['close'] > body_far_edge if bearish else row['close'] < body_far_edge):
                disrespect = {**_fact(row), 'level': body_far_edge, 'timeframe': tf,
                              'rule': 'assigned_close_back_through_original_body_far_edge',
                              'not_a_member_stop_or_parent_range_invalidation': True}
            if breach is None and swept:
                breach = {**_fact(row), 'level': extreme, 'timeframe': tf,
                          'not_automatically_thesis_invalidation': True}
    unavailable = 'unverified_incomplete_coverage' if gap_at else 'not_observed_by_review_cutoff'
    csd_status = 'confirmed' if csd else unavailable
    if not csd and invalid_at and not gap_at:
        csd_status = 'not_observed_before_range_invalidation'
    if csd and invalid_at and parse_time(csd['confirmed_at_ny']) == invalid_at:
        csd_status = 'body_close_observed_at_range_invalidation'
    return {'csd': {'status': csd_status, 'reference_level': reference, 'evidence': csd},
            'super_soup': {'status': ('observed_before_csd' if soup else
                                      'same_candle_as_csd_order_unresolved' if ambiguous_soup else
                                      'not_observed_before_csd' if csd else unavailable),
                           'evidence': soup or ambiguous_soup, 'reference_level': extreme},
            'body_reference_retest': {'status': 'observed_after_csd' if retest else
                                      unavailable if csd else 'not_applicable_without_observed_csd',
                                      'evidence': retest},
            'body_disrespect_close': {'status': 'observed_after_csd' if disrespect else
                                      unavailable if csd else 'not_applicable_without_observed_csd',
                                      'evidence': disrespect},
            'post_csd_extreme_breach': breach,
            'sequence_gap_at_ny': gap_at,
            'objectives_after_formation': _objectives(bars, origin_end, end, anchor, bearish, step, invalid_at, candle),
            'objectives_after_csd': (_objectives(bars, parse_time(csd['confirmed_at_ny']), end,
                                               anchor, bearish, step, invalid_at) if csd else None)}


def lifecycle_review(bars, anchor, mapped, end, step, invalid_at=None):
    result = {'version': VERSION, 'assigned_timeframe': mapped, 'purge_candles': [],
              'status': 'unverified_incomplete_anchor', 'execution_status': 'not_assessed',
              'range_invalidated_at_ny': stamp(invalid_at) if invalid_at else None,
              'next_identity_open_ny': None,
              'response_contract': 'Name the candle and its wick/body type first. Then give CSD, Super Soup, '
                  'reference retests, body closes, own objectives and parent-range invalidation separately. '
                  'Unconfirmed is not nonexistent. Body-disrespect evidence uses its stated level, '
                  'not an assumed stop. Missing data means unverified. Times are candle intervals, not ticks.'}
    if not anchor.get('complete'):
        return result
    if not mapped:
        result['status'] = 'assigned_timeframe_required'
        return result
    mapped = timeframe(mapped)
    result['assigned_timeframe'] = mapped
    start = parse_time(anchor['end_ny'])
    end = min(end, invalid_at) if invalid_at else end
    if end <= start:
        result['status'] = 'no_observation_window'
        return result
    duration = next_boundary(start, mapped) - start
    if duration < step or duration % step:
        result['status'] = 'resolution_unavailable'
        return result
    bars = sorted(bars, key=lambda b: b['time'])
    rows = _assigned(bars, start, end, mapped, step)
    total = 0
    for i, row in enumerate(rows):
        if not row['complete']:
            continue
        for side, level in (('buy', anchor['high']), ('sell', anchor['low'])):
            body = row['open'] <= level < row['close'] if side == 'buy' else row['close'] < level <= row['open']
            wick = (row['high'] > level and max(row['open'], row['close']) <= level) if side == 'buy' else (
                    row['low'] < level and min(row['open'], row['close']) >= level)
            if not body and not wick:
                continue
            total += 1
            if len(result['purge_candles']) >= MAX_IDENTITIES:
                result['next_identity_open_ny'] = result['next_identity_open_ny'] or row['start_ny']
                continue
            fact = {**_fact(row), 'identity': 'Model 1 candle' if body else 'Turtle Wick Soup',
                    'purge_type': 'body_soup' if body else 'wick_soup',
                    'timeframe': mapped, 'purged_side': side, 'purged_level': level,
                    'direction': 'bearish' if side == 'buy' else 'bullish',
                    'identified_at_ny': row['end_ny'], 'source_resolution_seconds': step,
                    'range_start_ny': anchor['start_ny'], 'range_end_ny': anchor['end_ny'],
                    'both_boundaries_pierced_in_same_candle': row['high'] > anchor['high'] and row['low'] < anchor['low']}
            if body:
                fact.update(_follow(row, side, rows[i+1:], bars, anchor, end, step, invalid_at, mapped))
            else:
                fact.update(csd={'status': 'not_a_model1_body_candle'},
                            objectives_after_formation=_objectives(bars, parse_time(row['end_ny']), end,
                                                                 anchor, side == 'buy', step, invalid_at, row))
            result['purge_candles'].append(fact)
    # Add the owner's nested-CRT cleanliness/outcome axes to the SAME view.
    # Do not replace the existing CSD or pre-CSD ordering statuses.
    from gbop_voice_web.super_soup_evidence import enrich_model1
    models = {'assigned_timeframe': mapped, 'candles': [
        f for f in result['purge_candles'] if f['purge_type'] == 'body_soup']}
    enrich_model1(bars, anchor, models, end, step, invalid_at)
    structural = {r['model1_bar_open_ny']: r for r in models.get('lifecycle', [])}
    for f in result['purge_candles']:
        r = structural.get(f['bar_open_ny']) if f['purge_type'] == 'body_soup' else None
        if r is None:
            continue
        structure = dict(r['super_soup'])
        structure.pop('pre_csd', None)
        structure['pre_csd_status_ref'] = 'super_soup.status'
        f['super_soup_structure'] = structure
        f['following_candle_relations'] = r['following_candles']
        f['next_relation_detail_start_ny'] = r['next_detail_start_ny']
        f['model1_crt_invalidating_close'] = r['model1_crt_invalidating_close']
    result['response_contract'] += (' Use super_soup_structure for nested CRT cleanliness, '
        'supported variants, local_crt_outcome, local_function_outcome and parent_function_outcome independently. '
        'Clean formation can fail; an invalidated or unclean Model 1 can still deliver its own '
        'opposing liquidity. local_function_objectives records that delivery and its timing '
        'relative to Model 1 invalidation, without restoring CRT validity or clean structure. '
        'The existing parent-range observation boundary still applies. '
        'super_soup.status separately preserves pre-CSD ordering uncertainty. '
        'The next candle may be an inside bar rather than an immediate soup.')
    complete = all(row['complete'] for row in rows)
    result.update(identified_count=total, observation_complete=complete,
                  window_start_ny=stamp(start), window_end_ny=stamp(end))
    result['status'] = 'identified' if total else ('not_observed_in_complete_window' if complete else 'unverified_incomplete_observation')
    result['spoken_summary'] = lifecycle_summary(result)
    return result


def lifecycle_summary(result):
    facts = result.get('purge_candles', [])
    if not facts:
        return ''
    first = facts[0]
    parts = [f"The first identified assigned-timeframe purge was a {first['timeframe']} {first['purge_type'].replace('_', ' ')} in the {_clock(first['bar_open_ny'])}–{_clock(first['bar_close_ny'])} candle."]
    body = next((f for f in facts if f['purge_type'] == 'body_soup' and f['purged_side'] == first['purged_side']), None)
    if body:
        parts.append(f"The Model 1 candle opened at {_clock(body['bar_open_ny'])} and its body close was identified at {_clock(body['bar_close_ny'])}.")
        csd = body['csd']
        if csd['status'] == 'confirmed':
            parts.append(f"CSD confirmed at {_clock(csd['evidence']['confirmed_at_ny'])} through its body reference, {csd['reference_level']}.")
        elif csd['status'].startswith('not_observed'):
            parts.append('CSD was not observed in the reviewed window; the Model 1 candle still exists.')
        else:
            parts.append('CSD or its timing remains unresolved in the available evidence.')
        structure = body.get('super_soup_structure')
        if structure and structure['structure_status'] == 'observed':
            quality = 'clean' if structure['structural_quality'] == 'clean' else 'not clean'
            variants = ', '.join(v['code'] for v in structure['variants'])
            when = _clock(structure['event']['bar_close_ny'])
            parts.append(f"The Model 1 nested CRT purge closed at {when}; its structure was {quality}" +
                         (f", supporting {variants}." if variants else '.'))
            outcomes = {
                'opposing_liquidity_delivered': 'opposing liquidity delivered',
                'midpoint_delivered_then_invalidated': 'midpoint delivered, then invalidated',
                'midpoint_only_at_cutoff': 'midpoint delivered; full objective not established',
                'failed_before_objectives': 'invalidated before either objective was verified',
                'pending_at_cutoff': 'not delivered by the review cutoff',
                'unverified': 'outcome unverified',
                'unverified_parent_validity': 'parent validity unverified',
            }
            parts.append('For the nested CRT: ' + outcomes.get(structure['local_crt_outcome'], 'unverified') +
                         '; for the parent range: ' + outcomes.get(structure['parent_function_outcome'], 'unverified') + '.')
            function = structure.get('local_function_outcome')
            invalidated_at = structure.get('local_crt_invalidated_at_ny')
            if function and (invalidated_at or function != structure['local_crt_outcome']):
                objectives = structure['local_function_objectives']
                target_name = ('opposing_liquidity' if function == 'opposing_liquidity_delivered' else
                               'midpoint' if function.startswith('midpoint_') else None)
                if target_name:
                    target = objectives[target_name]
                    evidence = target['evidence']
                    timing = target['relative_to_model1_invalidation']
                    sentence = (f"Independently, the Model 1 function delivered its own {target_name.replace('_', ' ')} "
                                f"at {target['level']} in the {_clock(evidence['bar_open_ny'])}–{_clock(evidence['bar_close_ny'])} source candle")
                    if timing == 'after_model1_invalidation':
                        sentence += f", after its {_clock(invalidated_at)} invalidating close"
                    elif timing == 'before_model1_invalidation':
                        sentence += f", before its later {_clock(invalidated_at)} invalidating close"
                    elif timing == 'same_model1_invalidating_bar_order_unresolved':
                        sentence += '; its order relative to the Model 1 invalidating close is unresolved'
                    parts.append(sentence + '. This does not restore CRT validity or change its structural quality.')
                else:
                    description = ('neither objective verified before the parent-range observation window ended'
                                   if function == 'failed_before_objectives' else outcomes.get(function, 'unverified'))
                    parts.append('Independent Model 1 function: ' + description + '.')
        if body['super_soup']['status'] == 'observed_before_csd':
            parts.append(f"A Super Soup returned inside that candle at {_clock(body['super_soup']['evidence']['return_candle']['bar_close_ny'])}, before CSD.")
        if body['body_reference_retest']['evidence']:
            r = body['body_reference_retest']['evidence']
            parts.append(f"Its body reference was retested in the {_clock(r['bar_open_ny'])}–{_clock(r['bar_close_ny'])} candle.")
        if body['body_disrespect_close']['evidence']:
            r = body['body_disrespect_close']['evidence']
            parts.append(f"A later close crossed back through the original body's far edge, {r['level']}, at {_clock(r['bar_close_ny'])}; that is separate from range invalidation.")
    return ' '.join(parts)
