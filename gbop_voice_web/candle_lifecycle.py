"""Ordered GTOP candle facts, independent of a trader's orders or P/L.

CSD uses the opposite edge of the ORIGINAL purge candle's real body (its open).
Retests and later closes are named by the exact reference tested. They never
silently become a member's stop or the parent range's invalidation rule.
"""
from datetime import datetime
from gbop_voice_web.super_soup_classification import classify_super_soup
from gbop_voice_web.candle_evidence import summarize, stamp, parse_time, next_boundary, timeframe

VERSION = 'candle-lifecycle-2026-10-03'
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
                  'not an assumed stop. Missing data means unverified. Times are candle intervals, not ticks. '
                  'Use super_soup.classification for cleanliness, nested CRT variant, outcome and separate parent function.'}
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
                fact['super_soup']['classification'] = classify_super_soup(
                    row, side, rows[i+1:], bars, anchor, end, step, invalid_at, mapped)
            else:
                fact.update(csd={'status': 'not_a_model1_body_candle'},
                            objectives_after_formation=_objectives(bars, parse_time(row['end_ny']), end,
                                                                 anchor, side == 'buy', step, invalid_at, row))
            result['purge_candles'].append(fact)
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
        if body['super_soup']['status'] == 'observed_before_csd':
            parts.append(f"A Super Soup returned inside that candle at {_clock(body['super_soup']['evidence']['return_candle']['bar_close_ny'])}, before CSD.")
        classification = body['super_soup'].get('classification', {})
        if classification.get('formation') in ('clean', 'unclean'):
            parts.append(f"Super Soup formation was {classification['formation']}; its Model 1-range CRT outcome was {classification['outcome'].replace('_', ' ')}. Parent-range objectives are assessed separately.")
        if body['body_reference_retest']['evidence']:
            r = body['body_reference_retest']['evidence']
            parts.append(f"Its body reference was retested in the {_clock(r['bar_open_ny'])}–{_clock(r['bar_close_ny'])} candle.")
        if body['body_disrespect_close']['evidence']:
            r = body['body_disrespect_close']['evidence']
            parts.append(f"A later close crossed back through the original body's far edge, {r['level']}, at {_clock(r['bar_close_ny'])}; that is separate from range invalidation.")
    return ' '.join(parts)
