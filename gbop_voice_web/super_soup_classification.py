"""GTOP Super Soup: the Model 1 is a nested CRT; cleanliness is not outcome.

Facts use closed candles and explicit source-bar ordering. No inferred trades,
stopouts or profits. Parent-range function and nested-CRT delivery are separate.
"""
from datetime import datetime

VERSION = 'super-soup-structure-2026-10-03'


def epoch(value):
    return int(datetime.fromisoformat(value).timestamp())


def fact(row):
    return {k: row[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close')}


def relationship(row, model, bearish):
    inside = row['high'] <= model['high'] and row['low'] >= model['low']
    sweep = row['high'] > model['high'] if bearish else row['low'] < model['low']
    returned = model['low'] <= row['close'] <= model['high']
    body_outside = row['close'] > model['high'] or row['close'] < model['low']
    body_cross = (row['open'] <= model['high'] < row['close']) if bearish else (row['close'] < model['low'] <= row['open'])
    wick = sweep and (max(row['open'], row['close']) <= model['high'] if bearish else min(row['open'], row['close']) >= model['low'])
    return {'candle': fact(row), 'inside_bar': inside, 'purges_model1_extreme': sweep,
            'body_cross_and_close': body_cross, 'wick_purge': wick,
            'closes_inside_model1': returned, 'closes_outside_model1': body_outside,
            'both_model1_boundaries_pierced': row['high'] > model['high'] and row['low'] < model['low']}


def touch(bars, after, end, step, level, invalid_at=None, ambiguous_source=None):
    cursor, gap = after, False
    for row in bars:
        t = row['time']
        if t < after or t + step > end:
            continue
        if t != cursor:
            gap = True
        cursor = t + step
        if row['low'] <= level <= row['high']:
            status = 'delivered' if not gap else 'touch_observed_order_unverified'
            if invalid_at and t + step >= invalid_at:
                status = 'touch_at_invalidation_order_unresolved' if t < invalid_at else 'touch_after_invalidation'
            return {'level': level, 'status': status, 'source_open_epoch': t,
                    'source_close_epoch': t + step, 'precision_seconds': step}
    status = ('same_source_candle_order_unresolved' if ambiguous_source and ambiguous_source['low'] <= level <= ambiguous_source['high'] else
              'unverified_incomplete_coverage' if gap or cursor < end else
              'not_reached_before_invalidation' if invalid_at else 'not_reached_by_cutoff')
    return {'level': level, 'status': status, 'source_open_epoch': None, 'precision_seconds': step}


def classify_super_soup(model, side, rows, bars, anchor, end, step, invalid_at=None, timeframe='M5'):
    bearish = side == 'buy'
    extreme = model['high'] if bearish else model['low']
    opposite = model['low'] if bearish else model['high']
    bars = sorted(bars, key=lambda b: b['time'])
    formed = epoch(model['end_ny'])
    limit = min(end, invalid_at) if invalid_at else end
    sequence, gap, csd_index = [], None, None
    expected = formed
    for row in rows:
        if epoch(row['start_ny']) >= limit:
            break
        if not row.get('complete') or epoch(row['start_ny']) != expected or epoch(row['end_ny']) > limit:
            gap = row['start_ny']
            break
        expected = epoch(row['end_ny'])
        sequence.append(relationship(row, model, bearish))
        if csd_index is None and (row['close'] < model['open'] if bearish else row['close'] > model['open']):
            csd_index = len(sequence) - 1
    if expected < limit and gap is None:
        gap = 'incomplete_tail'
    pre = sequence if csd_index is None else sequence[:csd_index + 1]
    index = next((i for i, x in enumerate(pre) if x['purges_model1_extreme']), None)
    base = {'version': VERSION, 'model1_candle': fact(model), 'timeframe': timeframe,
            'following_candles': sequence[:32], 'following_candles_truncated': len(sequence) > 32,
            'sequence_gap_at': gap, 'formation': 'unverified' if gap else 'none_observed',
            'variant': None, 'variant_status': 'not_established', 'outcome': 'not_applicable',
            'execution_status': 'not_assessed', 'risk_tier_unchanged': True,
            'cleanliness_is_separate_from_outcome': True,
            'parent_function_is_separate_from_nested_crt_outcome': True}
    if index is None:
        base['reason'] = 'No qualifying pre-CSD sweep of the Model 1 extreme was established; later directional delivery alone is not Super Soup.'
        base['parent_direction_without_soup'] = touch(bars, formed, limit, step,
            anchor['low'] if bearish else anchor['high'], invalid_at)
        return base
    sweep = pre[index]
    returned = next((x for x in pre[index:] if x['closes_inside_model1']), None)
    clean = sweep['closes_inside_model1'] and not any(x['closes_outside_model1'] for x in pre[:index])
    base.update(formation='clean' if clean else 'unclean', sweep=sweep,
                return_candle=returned['candle'] if returned else None,
                pre_csd_order=('same_assigned_candle_as_csd' if csd_index == index or
                    (returned and csd_index is not None and returned is pre[csd_index]) else 'before_csd_close'),
                pre_sweep_inside_bars=sum(x['inside_bar'] for x in pre[:index]),
                reason='Model 1 treated as a CRT: sweep and inside close.' if clean else
                       'The extreme was swept but the closed-candle sequence did not preserve clean Model 1 CRT treatment.')
    source = next((b for b in bars if epoch(sweep['candle']['start_ny']) <= b['time'] < epoch(sweep['candle']['end_ny']) and
                   (b['high'] > extreme if bearish else b['low'] < extreme)), None)
    if source is None:
        base.update(formation='unverified', outcome='unverified', reason='Assigned sweep lacks matching source evidence.')
        return base
    after = source['time'] + step
    nested_invalid = next((epoch(x['candle']['end_ny']) for x in sequence if x['closes_outside_model1']), None)
    nested_end = min(limit, nested_invalid) if nested_invalid else limit
    nested = {name: touch(bars, after, nested_end, step, level, nested_invalid or invalid_at, source)
              for name, level in [('midpoint', (model['high'] + model['low']) / 2), ('opposing_liquidity', opposite)]}
    parent = {name: touch(bars, after, limit, step, level, invalid_at, source)
              for name, level in [('midpoint', anchor['midpoint']), ('opposing_liquidity', anchor['low'] if bearish else anchor['high'])]}
    base.update(nested_crt_objectives=nested, parent_range_function=parent,
                nested_range_invalidated_at_epoch=nested_invalid,
                parent_range_invalidated_at_epoch=invalid_at)
    target = nested['opposing_liquidity']
    if target['status'] == 'delivered':
        base['outcome'] = 'delivered'
    elif target['status'] in ('unverified_incomplete_coverage', 'same_source_candle_order_unresolved',
                              'touch_observed_order_unverified', 'touch_at_invalidation_order_unresolved'):
        base['outcome'] = 'unverified'
    elif nested_invalid or invalid_at:
        base['outcome'] = 'failed'
    elif nested['midpoint']['status'] == 'delivered':
        base['outcome'] = 'midpoint_only'
    else:
        base['outcome'] = 'pending'
    # A structural variant does not imply successful distribution.
    inside_count = 0
    for x in pre[:index]:
        if x['inside_bar']:
            inside_count += 1
        else:
            inside_count = 0
    base['consecutive_inside_bars_before_sweep'] = inside_count
    target_at = target.get('source_open_epoch') if target['status'] == 'delivered' else None
    first_extreme = sweep['candle']['high'] if bearish else sweep['candle']['low']
    resoup = next((x for x in sequence[index + 1:] if
                  (target_at is None or epoch(x['candle']['start_ny']) <= target_at) and
                  (nested_invalid is None or epoch(x['candle']['end_ny']) <= nested_invalid) and
                  (x['candle']['high'] > first_extreme if bearish else x['candle']['low'] < first_extreme) and
                  x['closes_inside_model1']), None)
    if clean and resoup:
        base.update(variant='V6', variant_status='structural', resoup_candle=resoup['candle'])
    elif clean and inside_count:
        base.update(variant='V4' if inside_count == 1 else 'V5', variant_status='structural')
    elif clean and target_at is not None:
        distribution_index = next((i + 1 for i, x in enumerate(sequence) if
                                   epoch(x['candle']['start_ny']) <= target_at < epoch(x['candle']['end_ny'])), None)
        base['variant'] = 'V2' if distribution_index == 1 else 'V1' if distribution_index == 2 and index == 0 else 'V3'
        base['variant_status'] = 'ordered_distribution'
    elif clean:
        base['variant_status'] = 'distribution_unresolved'
    else:
        base['variant_status'] = 'no_clean_crt_variant'
    return base
