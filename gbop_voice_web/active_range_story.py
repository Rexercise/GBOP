"""Compact selected-range continuity from the authoritative shift ledger.

An hour's candle body is distinct from its own independent CRT attempt. Delivery
concludes an objective; only a recorded transition replaces the selected range.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.candle_naming import candle_label, source_timeframe
from gbop_voice_web.chronological_context import transition_sentence
from gbop_voice_web.smt_reference import closing_candle
from gbop_voice_web.variant_explanation import variant_clause
from gbop_voice_web.target_approach import owner_inducement_example, inducement_clause


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _interval(event):
    return {k: event[k] for k in ('bar_open_ny', 'bar_close_ny', 'precision_seconds') if k in event}


def _event_label(event):
    return candle_label(event['bar_open_ny'], source_timeframe(event.get('precision_seconds')))


def _double_context(row, asset=None, story=None):
    evidence = row.get('double_purge', {})
    if not evidence.get('observed') and not evidence.get('developing'):
        return None
    reversal = evidence['reversal_thesis']
    direction = evidence['reverse_direction']
    side = evidence['original_first_purged_side']
    value = {'original_direction': evidence['original_outcome']['direction'],
        'range_start_ny': row['anchor']['start_ny'],
        'original_outcome': evidence['original_outcome']['status'],
        'reversal_direction': direction, 'reversal_outcome': reversal['status'],
        'full_objective_side': side, 'full_objective_level': row['anchor']['high' if side == 'buy' else 'low'],
        'confirmed_at_ny': evidence.get('confirmed_at_ny'), 'developing': evidence.get('developing', False),
        'confirmation_timeframe': evidence.get('range_timeframe'),
        'source_return_inside': deepcopy(evidence['sequence']['source_return_inside']),
        'assigned_return_inside': deepcopy(evidence['sequence']['assigned_return_inside']),
        'selected_timeframe_return_inside': deepcopy(evidence['sequence'].get('selected_timeframe_return_inside')),
        'objectives': {}, 'pre_confirmation_objectives': {}}
    # The engine's cutoff-relative status is unchanged. Only a fully observed
    # completed shift can turn a still-valid pending reversal into a historical
    # non-delivery verdict; missing/live evidence must stay pending/unverified.
    shift_complete = bool(story and story.get('coverage', {}).get('complete')
                          and story.get('progression_complete'))
    value['invalidated_at_ny'] = row.get('invalidated_at_ny')
    value['presentation_outcome'] = ('failed_to_deliver_objectives_by_shift_end'
        if shift_complete and reversal['status'] == 'pending_at_review_cutoff'
        and reversal['coverage'].get('complete')
        and all(t['status'] == 'not_observed_by_review_cutoff'
                for t in reversal['objectives'].values())
        else reversal['status'])
    if evidence.get('observed') and evidence.get('confirmed_at_ny') == evidence.get('review_cutoff_ny'):
        value['presentation_outcome'] = 'no_post_confirmation_evidence'
    for key, source in (('objectives', reversal),
                        ('pre_confirmation_objectives', evidence.get('reversal_development') or {})):
        for name, target in source.get('objectives', {}).items():
            result = {k: deepcopy(target[k]) for k in ('status', 'level', 'distance_price_points',
                'observed_distance_price_points', 'closest_observed_price', 'closest_source_interval') if k in target}
            if result.get('closest_source_interval'):
                result['closest_source_interval'] = _interval(result['closest_source_interval'])
            approach = target.get('approach') or {}
            result['full_range_reference'] = deepcopy(approach.get('full_range_reference'))
            result['boundary_to_target_reference'] = deepcopy(approach.get('boundary_to_target_reference'))
            annotation = owner_inducement_example(asset, row['anchor'], direction, target)
            if annotation:
                result['gtop_context'] = annotation
            result = {k: v for k, v in result.items() if v is not None}
            if not annotation:
                # These geometric ratios are only spoken for supported inducement context.
                # The exact target, distance and source interval remain; full audit is on the range.
                result.pop('boundary_to_target_reference', None)
                result.pop('full_range_reference', None)
            if key == 'pre_confirmation_objectives':
                # Development is a separate phase, not another full audit tree.
                result = {k: v for k, v in result.items() if k in (
                    'status', 'level', 'distance_price_points', 'closest_source_interval',
                    'gtop_context', 'boundary_to_target_reference', 'full_range_reference') and v is not None}
                if not annotation:
                    result.pop('boundary_to_target_reference', None)
                    result.pop('full_range_reference', None)
            value[key][name] = result
    for key in ('source_return_inside', 'assigned_return_inside', 'selected_timeframe_return_inside'):
        event = value.get(key)
        if event:
            value[key] = {k: v for k, v in event.items() if k in ('bar_open_ny', 'known_at_ny',
                'timeframe', 'close', 'confirmation_basis', 'ohlc_basis', 'source_coverage_complete',
                'native_ohlc_provenance')}
    if evidence.get('continuation'):
        value['continuation'] = deepcopy(evidence['continuation'])
    return value


def _double_sentence(value, short=False):
    if not value:
        return ''
    side, direction = value['full_objective_side'], value['reversal_direction']
    if value.get('developing'):
        return ('Potential same-range ' + direction + ' double purge; awaiting the '
                + str(value['confirmation_timeframe']) + ' candle\'s close inside.')
    outcome = value.get('presentation_outcome', value['reversal_outcome'])
    state = {'original_side_delivered': 'delivered the original ' + side + '-side',
        'midpoint_only': 'delivered 50% only', 'unverified': 'has an unverified outcome',
        'failed_before_objectives': 'failed before its objectives on structural invalidation',
        'failed_to_deliver_objectives_by_shift_end': 'failed to deliver objectives by shift end',
        'no_post_confirmation_evidence': 'has no post-confirmation evidence by the cutoff',
        'pending_at_review_cutoff': 'remained pending at the cutoff'}[outcome]
    text = f'The same-range double-purge {direction} reversal {state}'
    if not short and value.get('range_start_ny'):
        text = f"The {_clock(value['range_start_ny'])} double-purge range's {direction} reversal {state}"
    confirmation = value.get('selected_timeframe_return_inside')
    if confirmation:
        text += '; confirmed on the closure of ' + candle_label(
            confirmation['bar_open_ny'], confirmation['timeframe'])
    midpoint = value['objectives'].get('midpoint', {})
    induced = inducement_clause(midpoint, direction)
    if induced:
        text += '; ' + induced
    prior_induced = inducement_clause(value.get('pre_confirmation_objectives', {}).get('midpoint', {}), direction)
    if prior_induced:
        text += '; before official confirmation, ' + prior_induced
    text += '.'
    if not short:
        returned = value['source_return_inside']
        text += (' Its return inside was in ' + candle_label(returned['bar_open_ny'], returned['timeframe'])
                 + f'; its full objective is the original {side}-side, with 50% only halfway.')
        if outcome == 'failed_to_deliver_objectives_by_shift_end':
            text += ' Neither 50% nor the original boundary was reached; the range was not structurally invalidated.'
    if value.get('continuation'):
        from gbop_voice_web.range_delivery_sequence import continuation_sentence
        text += ' ' + continuation_sentence(value['continuation'], value['range_start_ny'], value['confirmation_timeframe'])
    return text


def selected_range_story(story, row, fact, asset=None):
    """Keep one selected anchor through its real hourly development and outcome."""
    anchor = row['anchor_start_ny']
    if row.get('role') != 'selected_range':
        return None
    next_range = next((t for t in story.get('range_transitions', [])
                       if t['from_anchor_ny'] == anchor), None)
    next_range = ({k: next_range[k] for k in ('from_anchor_ny', 'to_anchor_ny', 'confirmed_at_ny', 'reason', 'next_status', 'crt_established_by_handoff')}
                  if next_range else None)
    if next_range:
        next_range['at_review_cutoff'] = next_range['confirmed_at_ny'] == story['end_ny']
    by_hour = {r['anchor_start_ny']: r['anchor'] for r in story.get('ranges', [])}
    purges = [e for e in row.get('events', []) if e['kind'].endswith('_side_purge')]
    first = min(purges, key=lambda e: e['bar_open_ny']) if purges else None
    side = 'buy' if fact.get('direction') == 'bearish' else 'sell' if fact.get('direction') == 'bullish' else None
    # Paired direction may differ from local purge direction. Do not reassign a
    # local manipulation to the opposite boneless thesis.
    if first and (side is None or first['kind'] != side + '_side_purge'):
        first = None
    returned = next((s for s in row.get('sweep_detail', [])
                     if s['side'] == side and s.get('first_source_close_back_inside_ny')), None) if first else None
    return_interval = None
    if returned:
        end, precision = returned['first_source_close_back_inside_ny'], returned['precision_seconds']
        return_interval = {'bar_open_ny': stamp(parse_time(end) - precision),
                           'bar_close_ny': end, 'precision_seconds': precision}
    development, clauses = [], []
    name = f"{_clock(anchor)} H1 range"
    for hour in story.get('hourly_progression', []):
        if hour['anchor_start_ny'] != anchor:
            continue
        item = {k: deepcopy(hour[k]) for k in ('candle_start_ny', 'candle_end_ny', 'complete',
                'status', 'candle_science', 'close') if k in hour}
        # This independent-range status is evaluated at the shared review
        # cutoff, never implied to hold at this acting candle's own closure.
        own_range = next((r for r in story.get('ranges', [])
                          if r['anchor_start_ny'] == hour['candle_start_ny']), {})
        item['own_range_crt_status_at_cutoff'] = own_range.get('context_qualification', {}).get('crt_status', 'unverified')
        candle = by_hour.get(hour['candle_start_ny'], {})
        body = None
        if hour.get('complete') and candle.get('open') is not None and candle.get('close') is not None:
            body = ('bullish' if candle['close'] > candle['open'] else
                    'bearish' if candle['close'] < candle['open'] else 'doji')
        item['candle_body_direction'] = body
        lo, hi = parse_time(hour['candle_start_ny']), parse_time(hour['candle_end_ny'])
        actions = []
        if first and lo <= parse_time(first['bar_open_ny']) < hi:
            item['purge'] = {'side': side, **_interval(first)}
            actions.append(f"swept the {name}'s {side}-side in {_event_label(first)}")
        if return_interval and lo < parse_time(return_interval['bar_close_ny']) <= hi:
            item['return_inside'] = deepcopy(return_interval)
            actions.append(f"returned inside in {_event_label(return_interval)}")
        for key, label in (('midpoint', '50%'), ('opposing_liquidity',
                'sell-side' if fact.get('direction') == 'bearish' else 'buy-side')):
            objective = fact.get(key, {})
            event = objective.get('source_interval') or {}
            if (objective.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
                    and event.get('bar_open_ny') and lo <= parse_time(event['bar_open_ny']) < hi):
                item[key] = deepcopy(event)
                actions.append(f"delivered the {name}'s {label} in {_event_label(event)}")
        if not hour.get('complete') or not hour.get('candle_science'):
            actions.append('has incomplete evidence; its close and progression are unverified')
        elif not any(k in item for k in ('midpoint', 'opposing_liquidity')):
            state = {'wick_above': 'back inside', 'wick_below': 'back inside',
                     'both_sides_wicked': 'inside', 'inside_range': 'inside',
                     'close_above': 'above', 'close_below': 'below'}[hour['candle_science']]
            actions.append(f"closed {body + ' ' if body else ''}{state} the {name}")
        if actions:
            clauses.append(f"The {_clock(hour['candle_start_ny'])} H1 candle " + ', then '.join(actions) + '.')
        development.append(item)
    full = fact.get('opposing_liquidity', {})
    delivered = full.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
    invalid = fact.get('invalidated_at_ny')
    conclusion = {'status': 'opposing_liquidity_delivered' if delivered else
                  'invalidated' if invalid else 'unverified' if fact.get('outcome') == 'unverified' else
                  'pending_at_review_cutoff',
                  'source_interval': deepcopy(full.get('source_interval')) if delivered else None,
                  'known_at_ny': (full.get('source_interval') or {}).get('bar_close_ny') if delivered else invalid}
    if row.get('selected_at_ny') and parse_time(row['selected_at_ny']) >= parse_time(story['end_ny']):
        conclusion = {'status': 'pending_at_review_cutoff', 'source_interval': None, 'known_at_ny': None}
    variant = row.get('variant_evidence', {})
    codes = [v['code'] for v in fact.get('variant', {}).get('labels', [])]
    distribution = variant.get('distribution_hour_ny')
    variant_known = fact.get('variant', {}).get('explanation', {}).get('known_at_ny')
    if variant_known is None and distribution and codes:
        variant_known = stamp(parse_time(distribution) + 3600)
    selection_start = row.get('selected_at_ny')
    if selection_start and parse_time(selection_start) >= parse_time(story['end_ny']):
        intro = f"The {name} became the range under review at the {_clock(story['end_ny'])} end of the GTOP shift; no later setup or delivery evidence is available."
    else:
        through = ', '.join(_clock(h['candle_start_ny']) for h in development)
        verified = all(h.get('candle_science') for h in development)
        intro = f"The {name} remained under review" + (f" through the {through} H1 candles." if through else '.')
        if not verified:
            intro = f"The {name} was under review; later progression has missing or unfinished evidence."
    text = ' '.join([intro] + clauses)
    text += ' ' + variant_clause(fact.get('variant', {}), include_known=True) + '.'
    if conclusion['status'] == 'pending_at_review_cutoff':
        text += ' Its directional outcome remains pending at the cutoff.'
    elif conclusion['status'] == 'unverified':
        text += ' Missing evidence leaves its outcome unverified.'
    if invalid:
        text += f" It was invalidated on {closing_candle(invalid)['spoken_label']}" + (' after recorded delivery.' if delivered else '.')
    if next_range:
        text += ' ' + transition_sentence(next_range)
    elif story.get('progression_complete'):
        text += ' No later under-review handoff occurred before the end of the GTOP shift.'
    else:
        text += ' A later under-review handoff is unverified.'
    double = _double_context(row, asset, story)
    if double:
        text += ' ' + _double_sentence(double)
    verified_through = next((h['candle_end_ny'] for h in reversed(development) if h.get('candle_science')), selection_start)
    incoming = next((t for t in story.get('range_transitions', []) if t['to_anchor_ny'] == anchor), None)
    return {'review_handoff': {k: incoming[k] for k in ('from_anchor_ny', 'to_anchor_ny', 'confirmed_at_ny', 'reason')} if incoming else None, 'anchor_start_ny': anchor, 'selection_status': 'range_under_review',
            'crt_status': row.get('context_qualification', {}).get('crt_status', 'unverified'), 'selected_at_ny': selection_start,
            'selected_through_ny': next_range['confirmed_at_ny'] if next_range else verified_through,
            'still_selected_at_cutoff': (story.get('active_anchor_ny') == anchor
                                        if story.get('progression_complete') else None),
            'direction': fact.get('direction'), 'hourly_development': development,
            'invalidated_at_ny': invalid,
            'conclusion': conclusion, 'variant': deepcopy(fact.get('variant', {})),
            'variant_known_at_ny': variant_known,
            'next_selected_range': next_range, 'double_purge': double, 'spoken_summary': text}


def shift_end_state(story):
    """Actual final close plus the newly closed range's independent evidence limit."""
    hours = story.get('hourly_progression', [])
    final = hours[-1] if hours else {}
    selected = next((r for r in story.get('ranges', [])
                     if r['anchor_start_ny'] == story.get('active_anchor_ny')), {})
    return {'through_ny': story['end_ny'], 'active_anchor_ny': story.get('active_anchor_ny'),
            'selection_status': selected.get('selection_status', 'unverified'),
            'selected_range_crt_status': selected.get('context_qualification', {}).get('crt_status', 'unverified'),
            'progression_complete': story.get('progression_complete', False),
            'final_hour': {k: deepcopy(final[k]) for k in ('candle_start_ny', 'candle_end_ny',
                'anchor_start_ny', 'complete', 'close', 'candle_science', 'status') if k in final},
            'newly_closed_range_observation': ('no_post_close_evidence_at_cutoff'
                if final.get('complete') and final.get('candle_end_ny') == story['end_ny']
                else 'final_range_unfinished_or_unverified'),
            'spoken_summary': story.get('recap', {}).get('closing',
                f"Review ends at {_clock(story['end_ny'])} New York.")}
