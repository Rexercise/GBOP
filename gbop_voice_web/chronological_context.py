"""Bounded shift handoffs and event-time DOL contexts, never a trade vote.

An under-review anchor is navigation. Source return, own-timeframe return and
named-play eligibility remain distinct facts; this module does not classify a
named play or infer a confirmed CRT from source return alone.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time, stamp, h1_anchor
from gbop_voice_web.candle_naming import candle_label, closure_label, range_label

VERSION = 'chronological-range-context-2026-10-06'
CONTRACT = ('Under review is not a confirmed CRT or named-play eligibility. Keep source return and '
    'H1 return confirmation separate. Count distinct named ranges, not candles, closures or Soup '
    'confirmations. Compare contexts at the later event time; retire full delivery/invalidation, '
    'keep 50%-only full DOL pending. Chronological precedence is descriptive, never a majority '
    'vote, probability improvement or instruction to trade.')


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def review_progression(hours, ranges):
    """Advance after full delivery or outside close, on that candle's closure."""
    ledger, transitions, active = [], [], 0
    blocked = not hours[0]['complete']
    for i, candle in enumerate(hours[1:], 1):
        anchor = hours[active]
        item = {'candle_start_ny': candle['start_ny'], 'candle_end_ny': candle['end_ny'],
                'anchor_start_ny': anchor['start_ny'], 'complete': candle['complete']}
        if blocked or not candle['complete']:
            item['status'] = 'progression_unverified_missing_or_unfinished_hour'
            blocked = True
        else:
            hi, lo = anchor['high'], anchor['low']
            above, below = candle['high'] > hi, candle['low'] < lo
            outside = not lo <= candle['close'] <= hi
            item.update(swept_buy_side=above, swept_sell_side=below, close=candle['close'],
                candle_science=('close_above' if candle['close'] > hi else
                    'close_below' if candle['close'] < lo else 'both_sides_wicked' if above and below else
                    'wick_above' if above else 'wick_below' if below else 'inside_range'),
                status=('invalidated_by_hourly_close' if outside else 'two_sided_sweep' if above and below else
                        'sweep_and_close_back_inside' if above or below else 'inside_range'))
            row = ranges[active]
            objective = next((o for o in row['objectives'] if o['objective'] == 'opposing_liquidity'), {})
            hit = objective.get('evidence') or {}
            invalid = row.get('invalidated_at_ny')
            completed = (objective.get('status') == 'observed_after_purge' and hit.get('bar_close_ny')
                and parse_time(hit['bar_close_ny']) <= parse_time(candle['end_ny'])
                and (not invalid or parse_time(hit['bar_close_ny']) < parse_time(invalid)))
            if outside or completed:
                transitions.append({'from_anchor_ny': anchor['start_ny'], 'to_anchor_ny': candle['start_ny'],
                    'confirmed_at_ny': candle['end_ny'],
                    'reason': 'opposing_objective_completed' if completed else 'hourly_close_outside_selected_range',
                    'next_status': 'range_under_review', 'crt_established_by_handoff': False,
                    'close': candle['close']})
                active = i
        ledger.append(item)
    selected = {hours[0]['start_ny']: hours[0]['end_ny']} if hours[0]['complete'] else {}
    selected.update({t['to_anchor_ny']: t['confirmed_at_ny'] for t in transitions})
    for row in ranges:
        row['selected_at_ny'] = selected.get(row['anchor_start_ny'])
        row['role'] = 'selected_range' if row['selected_at_ny'] else 'independent_range_context'
        row['selection_status'] = 'range_under_review' if row['selected_at_ny'] else 'independent_range_context'
    return ledger, transitions, None if blocked else hours[active]['start_ny'], not blocked


def attach_qualification(row, bars, end, step, native_h1=None):
    """Facts only: do not change CRT/named-play classifiers or their verdicts."""
    anchor = row['anchor']
    start = parse_time(anchor['end_ny'])
    end = min(end, parse_time(row.get('validity_evidence_through_ny') or stamp(end)))
    invalid = row.get('invalidated_at_ny')
    stop = min(end, parse_time(invalid)) if invalid else end
    prefix = start
    for bar in sorted((b for b in bars if start <= b['time'] and b['time'] + step <= stop), key=lambda b: b['time']):
        if bar['time'] != prefix:
            break
        prefix += step
    result = {'source_return_observed': False, 'selected_tf_return_confirmed': False,
        'evidence_through_ny': stamp(prefix), 'crt_status': 'not_established',
        'named_play_eligibility': 'unchanged_separate_assessment'}
    direction = row.get('direction_observed', row.get('observed_direction'))
    purges = sorted((e for e in row.get('events', []) if e['kind'].endswith('_side_purge')), key=lambda e: e['bar_open_ny'])
    if not anchor.get('complete') or not direction or not purges:
        row['context_qualification'] = result
        return result
    first = purges[0]
    if parse_time(first['bar_close_ny']) > prefix:
        result['crt_status'] = 'unverified_source_prefix'
        row['context_qualification'] = result
        return result
    returned = next((b for b in bars if parse_time(first['bar_open_ny']) <= b['time']
        and b['time'] + step <= prefix and anchor['low'] <= b['close'] <= anchor['high']), None)
    if returned:
        result.update(source_return_observed=True, source_return_known_at_ny=stamp(returned['time'] + step),
                      crt_status='developing_source_return_only')
    cursor = max(start, parse_time(first['bar_open_ny']) // 3600 * 3600)
    while cursor + 3600 <= stop:
        candle = h1_anchor(bars, cursor, step, native_h1, end)
        if not candle.get('complete') or not anchor['low'] <= candle['close'] <= anchor['high']:
            break
        if returned and returned['time'] + step <= cursor + 3600:
            result.update(selected_tf_return_confirmed=True,
                selected_tf_return_candle_ny=candle['start_ny'], selected_tf_return_known_at_ny=candle['end_ny'],
                crt_status='purge_and_H1_return_observed')
            break
        cursor += 3600
    row['context_qualification'] = result
    return result


def attach_phase_coverage(row, bars, end, step):
    """Native confirmation does not require a missing earlier source minute."""
    double = row.get('double_purge', {})
    if not double.get('observed') or not double.get('confirmed_at_ny'):
        return
    cursor = parse_time(double['confirmed_at_ny'])
    stop = min(end, parse_time(row.get('invalidated_at_ny') or stamp(end)),
               parse_time(row.get('validity_evidence_through_ny') or stamp(end)))
    for bar in sorted((b for b in bars if cursor <= b['time'] and b['time'] + step <= stop), key=lambda b: b['time']):
        if bar['time'] != cursor:
            break
        cursor += step
    row.setdefault('context_qualification', {})['double_purge_evidence_through_ny'] = stamp(cursor)


def transition_sentence(transition):
    opening = transition.get('from_anchor_ny', transition['to_anchor_ny'])
    prior = f"the {_clock(opening)} H1 range"
    if datetime.fromisoformat(opening).hour in (8, 20):
        prior = f"the 9ate8 ({_clock(opening)} H1 range)"
    reason = (prior + "'s full objective completed" if transition['reason'] == 'opposing_objective_completed'
              else 'the candle closed outside of ' + prior)
    return (f"After {reason}, the {_clock(transition['to_anchor_ny'])} H1 became the next range under review on "
            f"{closure_label(transition['to_anchor_ny'], 'H1')}; this is not automatic CRT confirmation.")


def _event_time(target):
    event = target.get('evidence') or target.get('source_interval') or {}
    return event.get('known_at_ny') or event.get('bar_close_ny')


def _context(row, phase, direction, known, full, midpoint, cutoff):
    anchor = row['anchor']
    invalid = row.get('invalidated_at_ny')
    full = deepcopy(full)
    delivered = full.get('status') in ('observed_after_purge', 'observed_after_confirmation')
    completed = _event_time(full) if delivered else None
    if completed and invalid and parse_time(completed) >= parse_time(invalid):
        full['status'] = 'touch_in_invalidating_bar_order_unresolved'
        completed = None
    # Full delivery before establishment is historical; it must not become a
    # still-active context merely because confirmation is known later.
    retired = min((t for t in (completed, invalid) if t), key=parse_time, default=None)
    prefix = row.get('context_qualification', {}).get(
        'double_purge_evidence_through_ny' if phase == 'double_purge' else 'evidence_through_ny')
    ambiguous = full.get('status') in ('same_bar_order_unknown', 'touch_in_invalidating_bar_order_unresolved',
        'unverified_boundary_bar_order', 'observed_touch_validity_unverified')
    boundary_times = [(b.get('source_interval') or {}).get('known_at_ny') or
                      (b.get('source_interval') or {}).get('bar_close_ny')
                      for b in full.get('boundary_observations', [])]
    boundary_at = min((t for t in boundary_times if t), key=parse_time, default=None)
    ambiguous_at = (_event_time(full) or boundary_at or known) if ambiguous else None
    order_known = not ambiguous
    status = ('completed' if completed and (not invalid or parse_time(completed) < parse_time(invalid)) else
              'invalidated' if invalid else 'unverified' if not order_known or not prefix or parse_time(prefix) < parse_time(cutoff)
              else 'active_full_DOL_pending')
    name = (f"the {_clock(anchor['start_ny'])} H1 double-purge range" if phase == 'double_purge' else
            f"the {row['label']} ({_clock(anchor['start_ny'])} H1 range)" if row.get('label') in ('Young Lefty', '9ate8', 'GCT') else
            range_label(anchor))
    return {'context_id': anchor['start_ny'] + '/' + phase, 'range_id': anchor['start_ny'],
        'name': name, 'phase': phase, 'direction': direction, 'established_at_ny': known,
        'status': status, 'retired_at_ny': retired, 'retirement_reason': 'full_DOL_delivered' if retired == completed and retired else
            'range_invalidated' if retired else None, 'evidence_through_ny': prefix,
        'remaining_DOL': {'side': 'buy-side' if direction == 'bullish' else 'sell-side',
                          'level': full.get('level'), 'status': full.get('status')},
        'midpoint_status': midpoint.get('status'), 'objective_order_known': order_known,
        'ambiguous_objective_at_ny': ambiguous_at}


def _active_at(context, known):
    return ((not context.get('ambiguous_objective_at_ny') or parse_time(known) < parse_time(context['ambiguous_objective_at_ny']))
        and parse_time(context['established_at_ny']) <= parse_time(known)
        and (not context['retired_at_ny'] or parse_time(known) < parse_time(context['retired_at_ny']))
        and context.get('evidence_through_ny') and parse_time(known) <= parse_time(context['evidence_through_ny']))


def build_context_graph(story, young=None):
    """At most five hourly ranges/two phases; separate chronology from selection."""
    cutoff = story['end_ny']
    rows = list(story.get('ranges', []))
    if young and not young.get('young_lefty_context') and young.get('context_qualification'):
        rows = [{**young, 'label': 'Young Lefty'}] + rows
    contexts = []
    for row in rows:
        qualification = row.get('context_qualification', {})
        direction = row.get('direction_observed', row.get('observed_direction'))
        objectives = {o['objective']: o for o in row.get('objectives', [])}
        if row.get('directional_outcome'):
            objectives = {key: row['directional_outcome'][key] for key in ('midpoint', 'opposing_liquidity')}
        if qualification.get('selected_tf_return_confirmed') and direction and objectives:
            contexts.append(_context(row, 'original', direction, qualification['selected_tf_return_known_at_ny'],
                objectives.get('opposing_liquidity', {}), objectives.get('midpoint', {}), cutoff))
        double = row.get('double_purge', {})
        if double.get('observed') and double.get('confirmed_at_ny'):
            reverse = double['reversal_thesis']
            contexts.append(_context(row, 'double_purge', reverse['direction'], double['confirmed_at_ny'],
                reverse['objectives']['original_side'], reverse['objectives']['midpoint'], cutoff))
    contexts.sort(key=lambda c: (parse_time(c['established_at_ny']), parse_time(c['range_id']), c['phase']))
    relations = []
    for later in contexts:
        earlier = [c for c in contexts if parse_time(c['established_at_ny']) < parse_time(later['established_at_ny'])
                   and c['range_id'] != later['range_id'] and _active_at(c, later['established_at_ny'])]
        # A closure and a Soup on the same range are supporting evidence for
        # that range, not extra votes. Prefer the latest phase for each range.
        distinct = {c['range_id']: c for c in earlier}
        opposed = [c for c in distinct.values() if c['direction'] != later['direction']]
        aligned = [c for c in distinct.values() if c['direction'] == later['direction']]
        if opposed or aligned:
            relations.append({'later_context_id': later['context_id'], 'known_at_ny': later['established_at_ny'],
                'countertrend_to': [c['context_id'] for c in opposed], 'aligned_with': [c['context_id'] for c in aligned],
                'earlier_opposing_distinct_ranges': len(opposed), 'earlier_aligned_distinct_ranges': len(aligned)})
    active = [c for c in contexts if _active_at(c, cutoff)]
    distinct = {c['range_id']: c for c in active}
    return {'version': VERSION, 'contexts': contexts, 'relationships': relations,
        'at_shift_end': {'active_context_ids': [c['context_id'] for c in distinct.values()],
            'distinct_ranges': len(distinct),
            'bullish_ranges': sum(c['direction'] == 'bullish' for c in distinct.values()),
            'bearish_ranges': sum(c['direction'] == 'bearish' for c in distinct.values())},
        'response_contract': CONTRACT}


def context_sentence(graph):
    by_id = {c['context_id']: c for c in graph['contexts']}
    parts = []
    active = [by_id[i] for i in graph['at_shift_end']['active_context_ids']]
    if active:
        parts.append('Pending full DOL: ' + '; '.join(
            f"{_clock(c['range_id'])} {'double purge' if c['phase'] == 'double_purge' else 'H1'} {c['direction']} {c['remaining_DOL']['side']}" for c in active) + '.')
    for relation in graph['relationships']:
        if relation['countertrend_to']:
            later = by_id[relation['later_context_id']]
            prior = [by_id[key]['name'] for key in relation['countertrend_to']]
            parts.append(f"{later['name'][0].upper() + later['name'][1:]} formed a {later['direction']} context countertrend to "
                f"{len(prior)} earlier intact distinct range{'s' if len(prior) != 1 else ''}: " + ', '.join(prior) + '.')
    return ' '.join(parts)


def pending_range_facts(row, fact):
    """Original-direction primary body identity; later re-purges cannot replace it."""
    if fact.get('invalidated_at_ny') or fact.get('outcome') == 'opposing_liquidity_delivered':
        return None
    direction = row.get('direction_observed', row.get('observed_direction'))
    lifecycle = row.get('candle_lifecycle', {})
    if lifecycle.get('status') == 'no_observation_window':
        return None
    model = row.get('model1', {})
    identities = {c['bar_open_ny']: c for c in model.get('candles', [])
                  if c.get('direction') == direction}
    identities.update({c['bar_open_ny']: c for c in lifecycle.get('purge_candles', [])
        if c.get('purge_type') == 'body_soup' and c.get('direction') == direction})
    bodies = sorted(identities.values(), key=lambda c: parse_time(c['bar_open_ny']))
    primary = bodies[0] if bodies else None
    paged = bool(lifecycle.get('next_identity_open_ny') or model.get('next_candle_start_ny'))
    result = {'local_original_direction': direction,
        'qualification': {k: deepcopy(v) for k, v in row.get('context_qualification', {}).items()
                          if k in ('source_return_observed', 'selected_tf_return_confirmed', 'crt_status')},
        'primary_body_model1': {'status': 'unverified_more_identity_evidence' if paged else
            'not_observed_in_complete_window' if lifecycle.get('observation_complete')
            else 'unverified_incomplete_observation'}}
    if primary:
        own_invalid = primary.get('model1_crt_invalidating_close')
        gap = primary.get('sequence_gap_at_ny')
        own = ('invalidated' if own_invalid else 'unverified' if gap or 'model1_crt_invalidating_close' not in primary
               else 'no_invalidating_close_observed')
        result['primary_body_model1'] = {'status': 'present', 'bar_open_ny': primary['bar_open_ny'],
            'timeframe': primary['timeframe'], 'direction': primary['direction'],
            'own_CRT_status': own,
            'own_invalidating_candle_ny': own_invalid.get('bar_open_ny') if own_invalid else None,
            'strict_CISD': {k: deepcopy(v) for k, v in primary.get('csd', {}).items()
                if k in ('status', 'reference_level', 'reference_boundary')},
            'strict_CISD_known_at_ny': (primary.get('csd', {}).get('evidence') or {}).get('confirmed_at_ny'),
            'later_body_repurges': len(bodies) - 1,
            **({'later_body_count_is_lower_bound': True} if model.get('next_candle_start_ny') else {})}
        result['primary_body_model1'] = {k: v for k, v in result['primary_body_model1'].items() if v is not None}
        structure = primary.get('super_soup_structure', {})
        if structure.get('variant_status') == 'distribution_observed' and structure.get('variants'):
            result['primary_body_model1'].update(own_CRT_status='completed', completed_SS={
                'variants': [{k: v[k] for k in ('code', 'name') if k in v} for v in structure['variants']],
                'known_at_ny': structure.get('completion_known_at_ny'),
                'completion_preserved_after_outside_close': bool(structure.get('completion_preserved_after_outside_close'))})
    return result


def pending_sentence(value, anchor=None):
    if not value:
        return ''
    primary = value['primary_body_model1']
    prefix = f"For the {_clock(anchor)} H1 range, " if anchor else ''
    if primary['status'] != 'present':
        return prefix + ('Primary body Model 1 ' + ('not observed in complete data' if primary['status'].startswith('not_observed')
                                        else 'unverified with available coverage') + '.')
    text = prefix + f"Primary body Model 1 is {candle_label(primary['bar_open_ny'], primary['timeframe'])}"
    if primary.get('completed_SS'):
        from gbop_voice_web.variant_explanation import variant_clause
        text += '; own Super Soup ' + variant_clause({'labels': primary['completed_SS']['variants']}) + ' completed'
        if primary['completed_SS']['completion_preserved_after_outside_close']:
            text += '; completion preserved despite its outside close'
    elif primary['own_CRT_status'] == 'invalidated':
        text += '; its own CRT invalidated on ' + closure_label(primary['own_invalidating_candle_ny'], primary['timeframe'])
    elif primary['own_CRT_status'] == 'unverified':
        text += '; its own CRT validity is unverified'
    else:
        text += '; own CRT intact in observed closes'
    csd = primary['strict_CISD'].get('status', 'unverified')
    text += '; strict CISD ' + ('confirmed' if csd == 'confirmed' else
        'not observed before parent invalidation' if csd == 'not_observed_before_range_invalidation' else
        'pending' if csd == 'not_observed_by_review_cutoff' else 'unverified')
    if primary['later_body_repurges']:
        text += f"; {'at least ' if primary.get('later_body_count_is_lower_bound') else ''}{primary['later_body_repurges']} later re-purge(s) separate"
    return text + '.'


def compact_context_graph(graph):
    """Bounded chronology; full target detail remains on each source range."""
    relationships = [r for r in graph['relationships'] if r['countertrend_to']]
    used = set(graph['at_shift_end']['active_context_ids'])
    for relation in relationships:
        used.add(relation['later_context_id'])
        used.update(relation['countertrend_to'] + relation['aligned_with'])
    rows = [c for c in graph['contexts'] if c['context_id'] in used]
    ids = {c['context_id']: i for i, c in enumerate(rows)}
    contexts = [{'id': ids[c['context_id']], **{k: deepcopy(c[k]) for k in ('name', 'direction',
        'established_at_ny', 'status', 'retired_at_ny', 'retirement_reason')
        if c[k] is not None}, 'DOL_side': c['remaining_DOL']['side']} for c in rows]
    relations = [{'later': ids[r['later_context_id']],
        'countertrend_to': [ids[i] for i in r['countertrend_to']],
        'aligned_with': [ids[i] for i in r['aligned_with']],
        'distinct_ranges_by_list': True} for r in relationships]
    end = deepcopy(graph['at_shift_end'])
    end['active_context_ids'] = [ids[i] for i in end['active_context_ids']]
    return {'contexts': contexts, 'relationships': relations, 'at_shift_end': end,
        'aligned_history_omitted_count': len(graph['relationships']) - len(relationships)}


def pending_reversal_facts(row):
    """The opposite phase has its own primary identity, never the original body."""
    double = row.get('double_purge', {})
    reverse = double.get('reversal_thesis', {})
    if (row.get('invalidated_at_ny') or not double.get('observed')
            or reverse.get('status') in ('original_side_delivered', 'failed_before_objectives')):
        return None
    direction = reverse['direction']
    value = pending_range_facts({**row, 'direction_observed': direction},
        {'outcome': 'pending_at_review_cutoff', 'invalidated_at_ny': None})
    if not value:
        return None
    primary = value['primary_body_model1']
    primary = {key: deepcopy(primary[key]) for key in ('status', 'bar_open_ny', 'timeframe',
        'own_CRT_status', 'own_invalidating_candle_ny', 'completed_SS') if key in primary}
    full = value['primary_body_model1']
    if full.get('strict_CISD'):
        primary['strict_CISD_status'] = full['strict_CISD'].get('status', 'unverified')
    confirmation = double.get('sequence', {}).get('selected_timeframe_return_inside') or {}
    return {'direction': direction, 'remaining_DOL_side': reverse['objective_side'],
            'primary_body_model1': primary,
            'official_confirmation': {k: deepcopy(confirmation[k]) for k in
                ('bar_open_ny', 'timeframe', 'known_at_ny') if k in confirmation}}


def pending_reversal_sentence(value, anchor, *, include_confirmation=True):
    if not value:
        return ''
    primary = value['primary_body_model1']
    intro = f"For the {_clock(anchor)} {value['direction']} double-purge reversal"
    confirmation = value.get('official_confirmation', {})
    confirmed = ('; official confirmation on ' + closure_label(
        confirmation['bar_open_ny'], confirmation['timeframe'])) if include_confirmation and confirmation.get('bar_open_ny') else ''
    if primary['status'] != 'present':
        return intro + ', primary body Model 1 ' + ('not observed in complete data.'
            if primary['status'] == 'not_observed_in_complete_window' else 'is unverified.').rstrip('.') + confirmed + '.'
    text = intro + ', primary body Model 1 is ' + candle_label(primary['bar_open_ny'], primary['timeframe'])
    own = primary['own_CRT_status']
    if primary.get('completed_SS'):
        from gbop_voice_web.variant_explanation import variant_clause
        text += '; own Super Soup ' + variant_clause({'labels': primary['completed_SS']['variants']}) + ' completed'
        if primary['completed_SS']['completion_preserved_after_outside_close']:
            text += '; completion preserved despite its outside close'
    else:
        text += ('; own CRT invalidated on ' + closure_label(primary['own_invalidating_candle_ny'], primary['timeframe'])
            if own == 'invalidated' else '; own CRT validity unverified' if own == 'unverified'
            else '; own CRT intact in observed closes')
    csd = primary.get('strict_CISD_status', 'unverified')
    text += '; strict CISD ' + ('confirmed' if csd == 'confirmed' else
        'pending' if csd == 'not_observed_by_review_cutoff' else 'unverified')
    return text + confirmed + '.'
