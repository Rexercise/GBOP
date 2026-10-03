"""Owner-defined nested CRT evidence; structure and outcome are independent.

Closed candles only. No broker orders, member fills, stop placement or alerts.
A Model 1's immutable identity is kept separately from this evolving lifecycle.
"""
from gbop_voice_web.candle_evidence import (
    interval, next_boundary, parse_time, stamp, summarize,
)

from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web import cisd_rule

VERSION = 'super-soup-full-extreme-2026-10-03'


def relation(row, reference, bearish):
    """Describe actual OHLC, including bodies returning inward from outside."""
    high, low = reference['high'], reference['low']
    above, below = row['high'] > high, row['low'] < low
    inside = low <= row['close'] <= high
    form = ('close_above' if row['close'] > high else
            'close_below' if row['close'] < low else
            'both_sides_wicked' if above and below else
            'wick_above' if above else 'wick_below' if below else 'inside_bar')
    level = high if bearish else low
    outward = (row['open'] <= level < row['close'] if bearish else
               row['close'] < level <= row['open'])
    wick_only = (max(row['open'], row['close']) <= level < row['high'] if bearish else
                 row['low'] < level <= min(row['open'], row['close']))
    swept = above if bearish else below
    return dict(relationship=form, swept_buy_side=above, swept_sell_side=below,
                close_inside=inside, initiating_side_swept=swept,
                purge_form=('body_purge' if outward else 'wick_only' if wick_only else
                            'body_return_or_body_outside' if swept else 'no_purge'))


def fact(row):
    out = {k: row[k] for k in ('open', 'high', 'low', 'close') if k in row}
    out.update(bar_open_ny=row['start_ny'], bar_close_ny=row['end_ny'],
               complete=row['complete'])
    return out


def assigned_rows(bars, start, end, tf, step):
    """Linear aggregation; retain missing/forming buckets instead of skipping them."""
    source = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end),
                    key=lambda b: b['time'])
    rows, cursor, index = [], start, 0
    while cursor < end:
        stop = next_boundary(cursor, tf)
        left = index
        while index < len(source) and source[index]['time'] < min(stop, end):
            index += 1
        subset = source[left:index]
        row = summarize(subset, cursor, min(stop, end), step)
        row.update(_start=cursor, _end=stop, _bars=subset, forming=stop > end)
        if stop > end:
            row['complete'] = False
        rows.append(row)
        cursor = stop
    return rows


def objective(bars, after, level, step, valid_until, complete):
    """Do not invent intrabar sweep/touch ordering, including at invalidation."""
    same = next((b for b in bars if b['time'] == after and b['low'] <= level <= b['high']
                 and (valid_until is None or b['time'] + step <= valid_until)), None)
    later = next((b for b in bars if b['time'] > after and b['low'] <= level <= b['high']
                  and (valid_until is None or b['time'] + step <= valid_until)), None)
    result = dict(level=level, same_purge_bar_order_unknown=bool(same), evidence=None)
    if later:
        result['evidence'] = interval(later, step)
        result['status'] = ('touch_in_invalidating_bar_order_unresolved'
                            if valid_until == later['time'] + step else 'observed_after_purge')
    elif same:
        result['status'] = 'same_purge_bar_order_unresolved'
        result['evidence'] = interval(same, step)
    elif not complete:
        result['status'] = 'unverified_incomplete_coverage'
    else:
        result['status'] = 'not_observed_before_invalidation' if valid_until else 'not_observed_by_cutoff'
    return result


def objectives(bars, after, reference, bearish, step, invalid_at, complete):
    return {name: {**objective(bars, after, level, step, invalid_at, complete),
                   **objective_identity(name, 'bearish' if bearish else 'bullish', reference,
                                        model1='bar_open_ny' in reference)}
            for name, level in (
                ('midpoint', (reference['high'] + reference['low']) / 2),
                ('opposing_liquidity', reference['low'] if bearish else reference['high']))}


def outcome(targets, invalid_at):
    if targets['opposing_liquidity']['status'] == 'observed_after_purge':
        return 'opposing_liquidity_delivered'
    if targets['midpoint']['status'] == 'observed_after_purge':
        return 'midpoint_delivered_then_invalidated' if invalid_at else 'midpoint_only_at_cutoff'
    if any('unresolved' in x['status'] or 'unverified' in x['status'] for x in targets.values()):
        return 'unverified'
    return 'failed_before_objectives' if invalid_at else 'pending_at_cutoff'


def functional_objectives(bars, after, model, bearish, step, local_invalid_at,
                          parent_invalid_at, complete):
    """Observe the nested function even after its CRT invalidates.

    Local invalidation still governs local_crt_objectives and variant evidence.
    These independent physical touches never restore that validity. Keep the
    existing parent-range observation boundary and source-bar uncertainty.
    """
    targets = objectives(bars, after, model, bearish, step, parent_invalid_at, complete)
    for target in targets.values():
        evidence = target['evidence']
        if evidence is None:
            timing = 'not_observed'
        elif local_invalid_at is None:
            timing = 'no_model1_invalidation_observed'
        elif parse_time(evidence['bar_open_ny']) >= local_invalid_at:
            timing = 'after_model1_invalidation'
        elif parse_time(evidence['bar_close_ny']) < local_invalid_at:
            timing = 'before_model1_invalidation'
        else:
            timing = 'same_model1_invalidating_bar_order_unresolved'
        target['relative_to_model1_invalidation'] = timing
    return targets


def model_lifecycle(model, anchor, rows, end, step, parent_invalid_at=None):
    start = parse_time(model['bar_close_ny'])
    bearish = model['direction'] == 'bearish'
    later = [r for r in rows if r['_start'] >= start]
    prefix, sources, missing = [], [], False
    for row in later:
        # Source coverage is checked independently from an assigned candle still forming.
        expected = row['_start']
        for bar in row['_bars']:
            if bar['time'] != expected:
                missing = True
                break
            sources.append(bar)
            expected += step
        if missing or expected != row['_start'] + (min(row['_end'], end) - row['_start']) // step * step:
            missing = True
            break
        if row['complete']:
            prefix.append(row)
        elif not row['forming']:
            missing = True
            break
    forming = bool(later and later[-1]['forming'])
    parent_validity = all(r['complete'] for r in rows if r['_start'] < start)
    local_invalid = next((r for r in prefix if not model['low'] <= r['close'] <= model['high']), None)
    local_invalid_at = local_invalid['_end'] if local_invalid else None
    confirmation = cisd_rule.reference(model, bearish)
    csd = next((r for r in prefix if cisd_rule.confirms(r, model, bearish)), None)
    csd_at = csd['_end'] if csd else None
    result = dict(model1_bar_open_ny=model['bar_open_ny'], timeframe=model['timeframe'],
                  window_end_ny=stamp(end), source_coverage_complete=not missing,
                  parent_validity_verified_at_model1=parent_validity,
                  assigned_candle_forming=forming, execution_status='not_assessed',
                  csd=dict(status='confirmed' if csd else 'unverified_missing_candles' if missing else
                           'pending_assigned_close' if forming else 'not_observed_by_cutoff',
                           **confirmation,
                           evidence=fact(csd) if csd else None),
                  model1_crt_invalidating_close=fact(local_invalid) if local_invalid else None,
                  parent_invalidated_at_ny=stamp(parent_invalid_at) if parent_invalid_at else None,
                  next_candle=(fact(later[0]) if later else None), following_candles=[],
                  following_candle_count=len(later), next_detail_start_ny=None)
    for row in later[:12]:
        item = fact(row)
        if row['complete']:
            item.update(relation(row, model, bearish))
        else:
            item['relationship'] = 'forming_unclassified' if row['forming'] else 'missing_unclassified'
        result['following_candles'].append(item)
    if later and later[0]['complete']:
        result['next_candle'].update(relation(later[0], model, bearish))
    if len(later) > 12:
        result['next_detail_start_ny'] = later[12]['start_ny']
    result['model1_parent_objectives'] = objectives(
        sources, start - step, anchor, bearish, step, parent_invalid_at, not missing)
    # Retest is a literal touch of the named body reference, not an inferred entry.
    retest = next((b for b in sources if csd_at and b['time'] >= csd_at and
                   b['low'] <= model['open'] <= b['high']), None)
    result['body_reference_retest_after_csd'] = interval(retest, step) if retest else None
    adverse = next((r for r in prefix if csd_at and r['_start'] >= csd_at and
                    (r['close'] > model['high'] if bearish else r['close'] < model['low'])), None)
    result['adverse_extreme_close_after_csd'] = fact(adverse) if adverse else None

    soup = dict(structure_status='not_observed_before_csd' if csd else
                'unverified_missing_candles' if missing else
                'pending_assigned_close' if forming else 'not_observed_by_cutoff',
                structural_quality='not_established', variants=[], event=None,
                local_crt_outcome='not_applicable', local_function_outcome='not_applicable',
                parent_function_outcome='not_applicable')
    result['super_soup'] = soup
    prior, event = [], None
    for row in prefix:
        if csd_at and row['_start'] >= csd_at:
            break  # A new sweep after already-confirmed CSD is not pre-CSD Super Soup.
        if relation(row, model, bearish)['initiating_side_swept']:
            event = row
            break
        prior.append(row)
    if event is None:
        return result

    rel = relation(event, model, bearish)
    previously_invalid = any(not model['low'] <= r['close'] <= model['high'] for r in prior)
    wrong_side_first = any(r['low'] < model['low'] if bearish else r['high'] > model['high'] for r in prior)
    clean = cisd_rule.wick_soup(event, model, bearish) and not previously_invalid and not wrong_side_first
    purge_bar = next(b for b in event['_bars'] if (b['high'] > model['high'] if bearish else b['low'] < model['low']))
    purge_at = purge_bar['time']
    soup.update(structure_status='observed', structural_quality='clean' if clean else 'not_clean',
                event={**fact(event), **rel}, purge_source_interval=interval(purge_bar, step),
                structure_known_at_ny=event['end_ny'],
                pre_csd=csd_at != event['_end'], csd_same_assigned_close=csd_at == event['_end'],
                occurrence_type=('pre_csd_wick_super_soup' if cisd_rule.wick_soup(event, model, bearish)
                                 else 'same_candle_csd_order_unresolved' if csd_at == event['_end']
                                 else 'model1_range_body_purge'),
                prior_local_invalidation=previously_invalid, opposite_side_swept_first=wrong_side_first,
                inside_bars_before_purge=sum(r['high'] <= model['high'] and r['low'] >= model['low'] for r in prior))
    cutoff_values = [t for t in (local_invalid_at, parent_invalid_at) if t is not None]
    local_cutoff = min(cutoff_values) if cutoff_values else None
    local_targets = objectives(sources, purge_at, model, bearish, step, local_cutoff, not missing)
    local_function_targets = functional_objectives(
        sources, purge_at, model, bearish, step, local_invalid_at, parent_invalid_at, not missing)
    parent_targets = objectives(sources, purge_at, anchor, bearish, step, parent_invalid_at, not missing)
    soup.update(local_crt_objectives=local_targets, parent_range_objectives=parent_targets,
                local_crt_outcome=outcome(local_targets, local_cutoff),
                local_function_objectives=local_function_targets,
                local_function_outcome=outcome(local_function_targets, parent_invalid_at),
                local_crt_invalidated_at_ny=stamp(local_invalid_at) if local_invalid_at else None,
                local_function_window_end_ny=stamp(end),
                parent_function_outcome=(outcome(parent_targets, parent_invalid_at) if parent_validity
                                         else 'unverified_parent_validity'))
    # Classify the Model 1 as the nested CRT anchor, never substitute the outer H1.
    if clean:
        all_inside = all(r['high'] <= model['high'] and r['low'] >= model['low'] for r in prior)
        n = len(prior)
        if all_inside and n:
            soup['variants'].append({'code': 'V4' if n == 1 else 'V5',
                                     'basis': f'{n} complete inside bars before the purge',
                                     'scope': 'Model 1 own candle range'})
        target = local_targets['opposing_liquidity']
        if n == 0 and target['status'] == 'observed_after_purge':
            t = parse_time(target['evidence']['bar_open_ny'])
            index = next((i for i, r in enumerate(prefix) if r['_start'] <= t < r['_end']), None)
            if index is not None:
                count = index + 2  # Model 1 is candle one.
                soup['variants'].append({'code': 'V2' if count == 2 else 'V1' if count == 3 else 'V3',
                                         'basis': f'Opposing Model 1 liquidity reached in candle {count}',
                                         'scope': 'Model 1 own candle range'})
        # V6 requires re-soup of the manipulation extreme BEFORE distribution,
        # not another touch of the original Model 1 boundary.
        midpoint = local_targets['midpoint']
        first_distribution = (parse_time(midpoint['evidence']['bar_open_ny'])
                              if midpoint['evidence'] else end)
        for row in prefix:
            if row['_start'] < event['_end'] or row['_end'] > first_distribution:
                continue
            if local_cutoff is not None and row['_end'] >= local_cutoff:
                continue
            re_swept = row['high'] > event['high'] if bearish else row['low'] < event['low']
            if re_swept and model['low'] <= row['close'] <= model['high']:
                soup['variants'].append({'code': 'V6', 'basis': 'Later manipulation extreme re-souped before midpoint delivery',
                                         'scope': 'Model 1 own candle range', 'evidence': fact(row)})
                break
    return result


def enrich_model1(bars, anchor, model1, end, step, parent_invalid_at=None):
    """Add separate lifecycle records; never mutate model1.candles identities."""
    mapped = model1.get('assigned_timeframe')
    start = parse_time(anchor['end_ny'])
    cutoff = min(end, parent_invalid_at) if parent_invalid_at else end
    if not mapped or not anchor['complete'] or cutoff <= start:
        return
    duration = next_boundary(start, mapped) - start
    if duration < step or duration % step:
        return
    rows = assigned_rows(bars, start, cutoff, mapped, step)
    purges = []
    for row in rows:
        if not row['complete']:
            continue
        for bearish in (True, False):
            rel = relation(row, anchor, bearish)
            if rel['initiating_side_swept']:
                purges.append({**fact(row), **rel, 'timeframe': mapped,
                               'purged_side': 'buy' if bearish else 'sell',
                               'purged_level': anchor['high'] if bearish else anchor['low']})
    model1.update(lifecycle_version=VERSION, assigned_range_purges=purges[:32],
                  assigned_range_purge_count=len(purges),
                  next_purge_detail_start_ny=purges[32]['bar_open_ny'] if len(purges) > 32 else None,
                  lifecycle=[model_lifecycle(m, anchor, rows, cutoff, step, parent_invalid_at)
                             for m in model1.get('candles', [])],
                  lifecycle_contract='Use lifecycle keyed by model1_bar_open_ny for CSD, following candles, '
                  'Super Soup structure and outcomes. CSD requires a strict assigned close below the full Model 1 low bearish, '
                  'above its full high bullish. A wick or equality is not confirmation. Identity stays in candles. assigned_range_purges '
                  'distinguishes wick-only purges from body-purging Model 1 candles. '
                  'Cleanliness is independent of success. Local Model 1 CRT validity, local_function '
                  'delivery and parent objectives are separate. Local function observations continue '
                  'after Model 1 invalidation within the selected-range review window; they never '
                  'restore CRT validity or clean structure. Preserve same-bar ordering uncertainty. '
                  'No trade execution or live alert is inferred. '
                  'Legacy top-level not_assessed flags do not override the detailed lifecycle.')
