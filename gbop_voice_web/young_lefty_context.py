"""Conditional 7–8–9 structure checks, never an automatic narrative selector.

An intrahour first sweep does not choose the named Young Lefty thesis when
the manipulation H1 swept both sides. Each direction can be checked against
real source bars; HTF context/confluence and the user's narrative remain
separate. These checks neither grant early execution nor create another play.
"""
from datetime import datetime

from gbop_voice_web.candle_evidence import interval, parse_time, summarize
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.shift_narrative import classify_structure


def young_lefty_context(review, bars, end, step):
    bars = [b for b in bars if b['time'] + step <= end]
    anchor = review.get('anchor', {})
    if not anchor.get('complete') or anchor.get('timeframe') != 'H1':
        return None
    opening = datetime.fromisoformat(anchor['start_ny'])
    if opening.hour not in (7, 19) or opening.minute or opening.second:
        return None
    start = parse_time(anchor['start_ny'])
    manipulation = summarize(bars, start+3600, start+7200, step)
    if (start+7200 > end or not manipulation['complete']
            or not (manipulation['high'] > anchor['high'] and manipulation['low'] < anchor['low'])
            or not anchor['low'] <= manipulation['close'] <= anchor['high']):
        return None
    invalid = review.get('invalidated_at_ny')
    stop = min(end, parse_time(invalid)) if invalid else end
    rows = [b for b in bars if start+3600 <= b['time'] and b['time']+step <= stop]
    checks = []
    for direction, side in (('bullish', 'sell'), ('bearish', 'buy')):
        first = next((b for b in rows if b['time'] < start+7200 and
                      (b['low'] < anchor['low'] if side == 'sell' else b['high'] > anchor['high'])), None)
        if first is None:
            continue
        objectives = []
        for name, level in (('midpoint', anchor['midpoint']),
                            ('opposing_liquidity', anchor['high'] if direction == 'bullish' else anchor['low'])):
            hit = next((b for b in rows if b['time'] >= first['time'] and
                        (b['high'] >= level if direction == 'bullish' else b['low'] <= level)), None)
            status = 'not_observed_by_review_cutoff'
            evidence = None
            if hit:
                evidence = interval(hit, step)
                status = ('same_bar_order_unknown' if hit['time'] == first['time'] else
                          'touch_in_invalidating_bar_order_unresolved' if invalid and hit['time']+step >= stop else
                          'observed_after_purge' if summarize(rows, start+3600, hit['time']+step, step)['complete'] else
                          'unresolved_incomplete_coverage')
            elif not review.get('range_observation_coverage', {}).get('complete'):
                status = 'unresolved_incomplete_coverage'
            objectives.append(dict(objective=name, level=level, status=status, evidence=evidence,
                                   **objective_identity(name, direction, anchor)))
        row = {'anchor': anchor, 'events': [dict(kind=side+'_side_purge', **interval(first, step))],
               'direction_observed': direction, 'invalidated_at_ny': invalid, 'objectives': objectives}
        variant = classify_structure(row, bars, end, step)
        checks.append({'direction': direction, 'scope': 'conditional_on_selected_thesis_direction',
            'first_source_purge': interval(first, step), 'objectives': objectives,
            'variant_evidence': variant, 'execution_status': 'not_assessed'})
    distribution = summarize(bars, start+7200, min(start+10800, end), step)
    distribution['complete'] = distribution['complete'] and start+10800 <= end
    return {'status': 'two_sided_manipulation_context_dependent', 'selected_direction': None,
        'anchor_start_ny': anchor['start_ny'], 'anchor_timeframe': 'H1',
        'manipulation': {k: manipulation[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close', 'complete')},
        'distribution': {k: distribution[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close', 'complete') if k in distribution},
        'directional_checks': checks,
        'physical_first_purge_direction': review.get('directional_outcome', {}).get('direction'),
        'physical_first_purge_variant': review.get('variant_evidence', {}),
        'response_contract': 'These are conditional mechanical structure checks, not a selected trade thesis. '
            'Use an explicitly stated user direction/HTF narrative to discuss its matching check; otherwise '
            'keep direction context-dependent. Neither the first intrahour wick, M5 Model 1/CSD nor the later '
            'profitable direction automatically selects Young Lefty. Preserve raw price-path audit separately; '
            'do not label an opposite intrahour path another Young Lefty or a Young Lefty double purge. '
            'Nine can deliver then close outside the named Young Lefty range; that later invalidation does not erase earlier delivery. '
            'Name the closing candle by its opening time and the exact play/range; do not substitute ambiguous anchor wording or redundant closing-clock times. '
            'HTF permission, confluence, entry and execution are not assessed here.'}


def compact_young_context(value):
    if not value:
        return None
    out = {k: value[k] for k in ('status', 'selected_direction', 'anchor_start_ny',
                                'anchor_timeframe', 'manipulation', 'distribution')}
    out['directional_checks'] = []
    for check in value['directional_checks']:
        out['directional_checks'].append({
            'direction': check['direction'], 'scope': check['scope'],
            'variant_status': check['variant_evidence']['status'],
            'variant_labels': [{k: v[k] for k in ('code', 'name')} for v in check['variant_evidence']['labels']],
            'objectives': [{k: o[k] for k in ('objective', 'level', 'status', 'evidence')}
                           for o in check['objectives']]})
    out['response_contract'] = value['response_contract']
    return out


def young_context_sentence(value):
    clock = lambda s: datetime.fromisoformat(s).strftime('%I:%M %p').lstrip('0')
    manipulation = value['manipulation']
    name = f"the Young Lefty {clock(value['anchor_start_ny'])} H1 range"
    text = (f"Young Lefty: the {clock(manipulation['start_ny'])} H1 candle swept both sides of {name} "
            'and closed inside it.')
    distribution = value['distribution']
    if distribution.get('complete'):
        bounds = {c['direction']: next(o['level'] for o in c['objectives'] if o['objective'] == 'opposing_liquidity')
                  for c in value['directional_checks']}
        relation = ('above' if distribution['close'] > bounds['bullish'] else
                    'below' if distribution['close'] < bounds['bearish'] else 'inside')
        text += (f" The {clock(distribution['start_ny'])} H1 candle closed inside {name}." if relation == 'inside' else
                 f" The {clock(distribution['start_ny'])} H1 candle closed outside of {name}, {relation} its {'buy' if relation == 'above' else 'sell'}-side.")
    text += ' Direction remains HTF/narrative-dependent; conditional direction checks do not select a trade thesis.'
    return text


def neutral_thesis_fact(fact):
    """Named-play fields stay unselected; physical path facts remain audit-only."""
    keys = ('direction', 'verdict', 'outcome', 'variant', 'midpoint', 'opposing_liquidity',
            'first_purge_interval', 'first_purge', 'setup_status')
    fact['physical_path_audit'] = {k: fact[k] for k in keys if k in fact}
    fact['physical_path_audit']['scope'] = 'physical_first_purge_not_selected_named_thesis'
    midpoint = fact.get('midpoint', {})
    fact.update(direction=None, outcome='context_dependent',
                direction_scope='context_dependent_no_selected_thesis',
                variant={'status': 'conditional_direction_checks', 'labels': []},
                midpoint={**{k: midpoint[k] for k in ('level', 'spoken_label') if k in midpoint},
                          'status': 'not_assessed_without_selected_direction'},
                opposing_liquidity={'status': 'not_assessed_without_selected_direction'})
    if 'verdict' in fact:
        fact['verdict'] = 'context_dependent'
    if 'setup_status' in fact:
        fact['setup_status'] = 'context_dependent_thesis_unselected'
    for key in ('first_purge_interval', 'first_purge'):
        if key in fact:
            fact[key] = None
    return fact
