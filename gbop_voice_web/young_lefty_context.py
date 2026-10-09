"""Conditional 7–8–9 structure checks, never an automatic narrative selector.

An intrahour first sweep does not choose the named Young Lefty thesis when
the manipulation H1 swept both sides. Each direction can be checked against
real source bars; HTF context/confluence and the user's narrative remain
separate. These checks neither grant early execution nor create another play.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import interval, parse_time, summarize
from gbop_voice_web.candle_naming import candle_label, objective_identity, source_timeframe
from gbop_voice_web.smt_reference import closing_candle
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
    context = {'status': 'two_sided_manipulation_context_dependent', 'selected_direction': None,
        'anchor_start_ny': anchor['start_ny'], 'anchor_timeframe': 'H1',
        'anchor_bounds': {'high': anchor['high'], 'low': anchor['low']},
        'manipulation': {k: manipulation[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close', 'complete')},
        'distribution': {k: distribution[k] for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close', 'complete') if k in distribution},
        'directional_checks': checks,
        'physical_first_purge_direction': review.get('directional_outcome', {}).get('direction'),
        'physical_first_purge_variant': review.get('variant_evidence', {}),
        'response_contract': 'Keep observed price-path delivery separate from HTF/narrative selection. '
            'An ordered same-parent double purge confirmed on its own H1 close may be recapped with '
            'original and post-confirmation reversal outcomes. Source/M1 or assigned/M5 returns alone '
            'are developing evidence. Conditional variants do not choose a thesis or another play. '
            'Later invalidation never erases earlier delivery. Name acting candles by opening time '
            'and the affected range. HTF permission, entry, execution and profit remain unassessed.'}
    double = review.get('double_purge', {})
    if double.get('observed') and double.get('confirmation_status') == 'confirmed':
        # Reuse the detector's validity/coverage/order gates, never infer a DP
        # from two conditional directions or the manipulation candle alone.
        from gbop_voice_web.shift_synopsis import compact_double_purge
        original = double['original_outcome']
        first = original.get('initiating_identity', {}).get('source_purge')
        context['delivery_recap'] = {
            'scope': 'observed_price_path_not_selected_thesis',
            'original': {'direction': original['direction'], 'status': original['status'],
                         'first_purge': deepcopy(first),
                         'variant': {'labels': [{k: v[k] for k in ('code', 'name')}
                             for v in review.get('variant_evidence', {}).get('labels', [])]}},
            'double_purge': compact_double_purge(review),
            'invalidated_at_ny': original.get('range_invalidated_at_ny'),
            'continuation': deepcopy(double.get('continuation', {'legs': []}))}
        for name in ('midpoint', 'opposing_liquidity'):
            target = original[name]
            context['delivery_recap']['original'][name] = {
                'status': target['status'],
                'evidence': {k: target['evidence'][k] for k in
                    ('bar_open_ny', 'bar_close_ny', 'precision_seconds') if k in target['evidence']}
                    if target.get('evidence') else None}
    return context


def compact_young_context(value, *, brief=False):
    if not value:
        return None
    out = {k: value[k] for k in ('status', 'selected_direction', 'anchor_start_ny',
                                'anchor_timeframe', 'anchor_bounds', 'manipulation', 'distribution')}
    out['directional_checks'] = []
    for check in value['directional_checks'] if not value.get('delivery_recap') else []:
        out['directional_checks'].append({
            'direction': check['direction'], 'scope': check['scope'],
            'variant_status': check['variant_evidence']['status'],
            'variant_labels': [{k: v[k] for k in ('code', 'name')} for v in check['variant_evidence']['labels']],
            'objectives': [{k: o[k] for k in ('objective', 'level', 'status', 'evidence')}
                           for o in check['objectives']]})
    if value.get('delivery_recap'):
        for key in ('directional_checks', 'manipulation', 'distribution', 'anchor_bounds'):
            out.pop(key)
        out['status'] = 'confirmed_range_delivery'
        out['conditional_checks_omitted'] = True
        out['delivery_recap'] = _compact_delivery(value['delivery_recap'])
        if brief:
            delivery = out['delivery_recap']
            for key in ('source_return_inside', 'assigned_return_inside'):
                delivery['double_purge']['sequence'].pop(key, None)
            continuation = delivery.get('continuation', {})
            if continuation.get('next_boundary'):
                continuation['next_boundary'] = {'confirmation_status': continuation['next_boundary']['confirmation_status']}
            out['secondary_return_details_omitted'] = True
    out['response_contract'] = ('Price-path facts do not choose an HTF thesis, entry or profit. Own-H1 confirmations gate reversals; later invalidation preserves delivery. Conditional variants remain in exact detail.' if value.get('delivery_recap') else value['response_contract'])
    return out


def _compact_delivery(delivery):
    out = deepcopy(delivery)
    double = out['double_purge']
    double.pop('original_outcome', None)
    double.pop('continuation', None)
    double.pop('original_completion_preserved', None)
    for key in ('source_return_inside', 'assigned_return_inside'):
        value = double['sequence'].get(key)
        double['sequence'][key] = {k: value[k] for k in ('known_at_ny', 'timeframe') if k in value} if value else None
    reverse = double['reversal_thesis']
    for key in ('objective_level', 'midpoint_level', 'midpoint_role', 'window_start_ny', 'objective_side'):
        reverse.pop(key, None)
    for target in reverse['objectives'].values():
        target.pop('distance_price_points', None)
    for leg in out.get('continuation', {}).get('legs', []):
        leg['confirmation'] = {k: leg['confirmation'][k] for k in
            ('bar_open_ny', 'timeframe', 'known_at_ny', 'close') if k in leg['confirmation']}
    return out


def _delivery_sentence(value, name):
    delivery = value['delivery_recap']
    original = delivery['original']
    double = delivery['double_purge']
    side = double['original_first_purged_side']
    opposite = 'sell' if side == 'buy' else 'buy'
    def source(event):
        seconds = parse_time(event['bar_close_ny']) - parse_time(event['bar_open_ny'])
        return candle_label(event['bar_open_ny'], source_timeframe(seconds)).removeprefix('the ').removesuffix(' candle')
    def targets(objectives, full_key, full_label, status, evidence_key):
        return ', '.join(f"{label} in {source(target[evidence_key])}" for key, label in
            (('midpoint', 'midpoint'), (full_key, full_label))
            if (target := objectives[key]).get('status') == status and target.get(evidence_key))
    variant = original.get('variant', {}).get('labels', [])
    label = ' ' + '/'.join(v['code'] for v in variant) if variant else ''
    text = (f"Young Lefty {name.removeprefix('the Young Lefty ')}: {side}-side purge in "
            f"{source(original['first_purge'])}; initial {original['direction']}{label} delivered "
            + targets(original, 'opposing_liquidity', f'full {opposite}-side', 'observed_after_purge', 'evidence') + '.')
    confirmation = double['sequence']['selected_timeframe_return_inside']
    opposing = double['sequence'].get('opposing_purge') or {}
    simultaneous = opposing.get('bar_open_ny') == (original['opposing_liquidity'].get('evidence') or {}).get('bar_open_ny')
    text += (" Double purge developed simultaneously; confirmed on " if simultaneous else
             " Double purge confirmed on ") + f"{candle_label(confirmation['bar_open_ny'], confirmation['timeframe'])}'s closure."
    reversal = double['reversal_thesis']
    delivered = targets(reversal['objectives'], 'original_side', f'full {side}-side',
                        'observed_after_confirmation', 'source_interval')
    text += f" Then {reversal['direction']} " + (
        'delivered ' + delivered + '.' if delivered else reversal['status'].replace('_', ' ') + '.')
    if reversal['status'] == 'midpoint_only':
        text += ' Full reversal delivery remains unestablished.'
    for leg in delivery.get('continuation', {}).get('legs', []):
        side = 'buy' if leg['direction'] == 'bearish' else 'sell'
        opposite = 'sell' if side == 'buy' else 'buy'
        confirmation = leg['confirmation']
        label = 'triple purge' if leg['leg_index'] == 3 else f"purge {leg['leg_index']}"
        text += (f" {label.capitalize()}: {side}-side in {source(leg['first_purge'])}, "
                 f"confirmed on {candle_label(confirmation['bar_open_ny'], confirmation['timeframe'])}'s closure; "
                 f"{leg['direction']} ")
        mid = leg['objectives']['midpoint']
        full = leg['objectives']['opposing_liquidity']
        if mid['status'] == 'observed_after_confirmation' and mid.get('evidence'):
            text += f"midpoint in {source(mid['evidence'])}; "
        variant = leg.get('variant', {})
        if variant.get('code') == 'V6':
            text += f"later V6 re-soup by {candle_label(variant['acting_candle_start_ny'], value['anchor_timeframe'])}; "
        if full['status'] == 'observed_after_confirmation' and full.get('evidence'):
            text += f"full {opposite}-side in {source(full['evidence'])}."
        else:
            text = text.rstrip('; ') + '.'
        if leg['status'] != 'opposing_liquidity_delivered':
            text += ' Post-confirmation full outcome: ' + leg['status'].replace('_', ' ') + '.'
        from gbop_voice_web.range_delivery_sequence import pre_confirmation_sentence
        text += pre_confirmation_sentence(leg)

    return text


def young_context_sentence(value, original_fact=None):
    clock = lambda s: datetime.fromisoformat(s).strftime('%I:%M %p').lstrip('0')
    manipulation = value.get('manipulation', {})
    name = f"the Young Lefty {clock(value['anchor_start_ny'])} H1 range"
    delivery = value.get('delivery_recap')
    if delivery and 'original' not in delivery:
        # Compact range facts already carry the exact original phase once.
        value = deepcopy(value)
        delivery = value['delivery_recap']
        fact = original_fact or {}
        delivery['original'] = {'direction': fact['direction'], 'status': fact['outcome'],
            'variant': fact['variant'], 'first_purge': fact.get('first_purge_interval') or fact.get('first_purge')}
        for key in ('midpoint', 'opposing_liquidity'):
            delivery['original'][key] = {'status': fact[key]['status'], 'evidence': fact[key].get('source_interval')}
    text = (_delivery_sentence(value, name) if delivery else
            f"Young Lefty: the {clock(manipulation['start_ny'])} H1 candle swept both sides of {name} and closed inside it.")
    distribution = value.get('distribution', {})
    if distribution.get('complete') and not delivery:
        bounds = {'bullish': value['anchor_bounds']['high'], 'bearish': value['anchor_bounds']['low']}
        relation = ('above' if distribution['close'] > bounds['bullish'] else
                    'below' if distribution['close'] < bounds['bearish'] else 'inside')
        text += (f" The {clock(distribution['start_ny'])} H1 candle closed inside {name}." if relation == 'inside' else
                 f" The {clock(distribution['start_ny'])} H1 candle closed outside of {name}, {relation} its {'buy' if relation == 'above' else 'sell'}-side.")
    if delivery and delivery.get('invalidated_at_ny'):
        text += (f" Later {closing_candle(delivery['invalidated_at_ny'])['spoken_label']} invalidated the range; earlier delivery remains recorded.")
    text += (' HTF trade direction and execution remain unselected.' if delivery else
             ' Trade direction remains HTF/narrative-dependent; conditional direction checks do not select a trade thesis.')
    return text


def neutral_thesis_fact(fact):
    """Expose confirmed range delivery without selecting a member trade thesis."""
    delivery = fact.get('young_lefty_context', {}).get('delivery_recap')
    if delivery:
        fact['direction_scope'] = delivery['scope']
        fact['variant']['scope'] = delivery['scope']
        delivery.pop('original', None)
        delivery['original_phase'] = 'enclosing_range'
        return fact
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
