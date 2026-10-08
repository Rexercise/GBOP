"""Short, evidence-only default shift reply; detailed range evidence stays queryable.

The seven o'clock range is evaluated independently even when eight/nine fails.
Young Lefty's status is always stated; only a real early purge creates its setup.
No assigned-candle candidate list or entry inference is needed for this view.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.candle_naming import candle_label, source_timeframe
from gbop_voice_web.shift_narrative import directional_outcome
from gbop_voice_web.smt_reference import closing_candle
from gbop_voice_web.active_range_story import selected_range_story, shift_end_state, _double_context, _double_sentence
from gbop_voice_web.variant_explanation import NAMES, variant_clause
from gbop_voice_web.target_approach import owner_inducement_example, inducement_clause
from gbop_voice_web.chronological_context import (build_context_graph, compact_context_graph, context_sentence,
    pending_range_facts, pending_sentence, pending_reversal_facts, pending_reversal_sentence,
    transition_sentence, CONTRACT as CONTEXT_CONTRACT)


SYNOPSIS_CONTRACT = (
    'Opening plays in evidenced event-time order, including 9ate8 verdict and Young Lefty status, then selected chronology to shift_end. '
    'Seven is prelude to the official nine-to-twelve AM/PM shift. Shared candle evidence is not another member trade; parent targets remain separate. '
    'Lead with full delivery and final variant; midpoint delivery_manner is its frozen partial milestone, not the final variant. Milestone known-at is its source_interval close. '
    'Keep failed secondary ranges brief; exact range detail retains their mechanics. '
    'Continue after delivery with each evidenced later range and its own outcome; independent context is not a new selection. '
    'Only recorded completion/invalidation handoffs change the range under review; selection does not establish a CRT. Explain supplied variants/candidates and missing conditions. '
    'Preserve earlier delivery and BUT induced 50% gap/path. Body is not thesis; no glossary/profit. '
    'Keep supplied same-range double-purge chronology and reversal separate from original delivery. '
    'Primary body identity belongs to the original local direction, not a paired/boneless thesis. '
    'Use detail_request for further Model 1/CISD/Soup; omission is not absence. ' + CONTEXT_CONTRACT)

RANGE_IDENTITY_CONTRACT = (
    'Name acting candle AND affected range. Own closure fixes anchor bounds; own CRT needs subsequent '
    'purge/return evidence. Recheck the same parent on challenges, not another anchor.')
SYNOPSIS_CONTRACT += ' ' + RANGE_IDENTITY_CONTRACT


OTHER_RANGES_CONTRACT = (
    'Follow mode through shift end; exclude discussed ranges, retain bridge. Under review is not CRT confirmation. '
    'Name acting candle AND affected range. Keep primary/own CRT/CISD, parent, re-purges, variant and DOL distinct. '
    'Omission is not absence; retrieval is not discussion.')


def negative_claim_guard(young_status, story):
    """Presentation scope only; this does not classify an aggregate shift outcome."""
    unverified = ['Young Lefty'] if young_status in (None, 'unverified', 'context_dependent') else []
    if not story.get('coverage', {}).get('complete') or not story.get('progression_complete'):
        unverified.append('affected shift ranges')
    if not unverified:
        return None
    return {'shift_wide_absence': 'not_assessed', 'unverified_contexts': unverified,
        'instruction': 'Qualify negative outcomes by verified named range and cutoff; '
            'unverified contexts block whole-shift none claims. '
            'Assess double-purge absence from its own evidence, never from an objective summary.'}


def _missing_young_anchor(young):
    """Explain seven's missing evidence separately from whole-shift coverage."""
    anchor = (young or {}).get('anchor', {})
    if not anchor or (anchor.get('complete') and anchor.get('source_coverage_complete') is not False):
        return {}, None
    coverage = {key: deepcopy(anchor[key]) for key in (
        'start_ny', 'end_ny', 'complete', 'source_resolution_seconds', 'bar_count',
        'missing_bar_count', 'missing_intervals', 'missing_interval_count', 'coverage_note',
        'ohlc_complete', 'ohlc_basis', 'source_coverage_complete', 'native_h1_status',
        'native_ohlc_provenance') if key in anchor}
    if anchor.get('native_h1_status') == 'conflicting_ohlc':
        return coverage, (f"Young Lefty: unverified; native H1 and source candles disagree on "
                          f"the {_clock(anchor['start_ny'])} range.")
    if anchor.get('complete'):
        return coverage, None
    missing = anchor.get('missing_bar_count')
    step = anchor.get('source_resolution_seconds')
    if not missing or not step or not anchor.get('start_ny'):
        return coverage, None
    unit = source_timeframe(step) or f'{step}-second'
    noun = 'candle' if missing == 1 else 'candles'
    sentence = (f"Young Lefty: unverified; its {_clock(anchor['start_ny'])} H1 range "
                f"is missing {missing} {unit} source {noun}")
    intervals = anchor.get('missing_intervals', [])
    if intervals:
        descriptions = []
        for gap in intervals[:3]:
            first, last = gap['first_bar_open_ny'], gap['last_bar_open_ny']
            descriptions.append(_clock(first) if first == last else f'{_clock(first)} through {_clock(last)}')
        sentence += ' (openings: ' + '; '.join(descriptions)
        remaining = anchor.get('missing_interval_count', len(intervals)) - len(descriptions)
        if remaining > 0:
            sentence += f'; {remaining} more missing intervals'
        sentence += ', New York)'
    return coverage, sentence + '.'


def _active_context(story, records, anchor_start_ny=None, asset=None):
    anchor = anchor_start_ny or story.get('active_anchor_ny')
    row = next((r for r in story.get('ranges', []) if r['anchor_start_ny'] == anchor), None)
    if not row:
        return None
    if row.get('role') != 'selected_range':
        if anchor_start_ny:
            raise ValueError('The named range is independent context; retrieve its exact range detail '
                             'instead of changing the selected-range story.')
        return None
    fact = _paired_fact(row, records, _local_fact(row), story['end_ny'])
    return selected_range_story(story, row, fact, asset)


def _continuity_bridge(context):
    """Short reference for an already-discussed anchor, preserving its key ending."""
    name = f"{_clock(context['anchor_start_ny'])} H1 range"
    parts = [f'The {name} remains the under-review reference for this sequence.']
    for hour in context['hourly_development']:
        actions = []
        if hour.get('purge'):
            actions.append(f"swept the {name}'s {hour['purge']['side']}-side")
            if hour.get('candle_science') in ('wick_above', 'wick_below', 'both_sides_wicked', 'inside_range'):
                actions.append(f"closed {hour.get('candle_body_direction') or ''} back inside that range".replace('  ', ' '))
        for key, label in (('midpoint', '50%'), ('opposing_liquidity',
                'sell-side' if context.get('direction') == 'bearish' else 'buy-side')):
            event = hour.get(key)
            if event:
                actions.append(f"delivered the {name}'s {label} in " + candle_label(event['bar_open_ny'],
                    source_timeframe(event.get('precision_seconds'))))
        if actions:
            parts.append(f"The {_clock(hour['candle_start_ny'])} H1 candle " + ' and '.join(actions) + '.')
    parts.append(f"The {name}: " + variant_clause(context.get('variant', {}), include_known=True) + '.')
    if context.get('invalidated_at_ny'):
        parts.append(f"The {name} was invalidated on {closing_candle(context['invalidated_at_ny'])['spoken_label']}"
                     + (' after recorded delivery.' if context['conclusion']['status'] == 'opposing_liquidity_delivered' else '.'))
    if context.get('next_selected_range'):
        nxt = context['next_selected_range']
        parts.append(transition_sentence(nxt))
    elif context['still_selected_at_cutoff']:
        parts.append('No later range replaced it under review in this GTOP shift.')
    if context.get('double_purge'):
        parts.append(_double_sentence(context['double_purge'], short=True))
    return ' '.join(parts)


def _short_selected_summary(context):
    """Default answer is short; source purge/return detail remains structured."""
    name = f"{_clock(context['anchor_start_ny'])} H1 range"
    parts = [transition_sentence(context['review_handoff']) if context.get('review_handoff')
             else f'The {name} remained under review.']
    for hour in context['hourly_development']:
        actions = []
        if hour.get('purge'):
            actions.append(f"swept the {name}'s {hour['purge']['side']}-side")
            if hour.get('candle_science') in ('wick_above', 'wick_below', 'both_sides_wicked', 'inside_range'):
                actions.append(f"closed {hour.get('candle_body_direction') or ''} inside that range".replace('  ', ' '))
        for key, label in (('midpoint', '50%'), ('opposing_liquidity',
                'sell-side' if context.get('direction') == 'bearish' else 'buy-side')):
            event = hour.get(key)
            if event:
                verb = 'delivered ' if not any('delivered ' in action for action in actions) else ''
                actions.append(f"{verb}the {name}'s {label} in {_clock(event['bar_open_ny'])} "
                               f"{source_timeframe(event.get('precision_seconds')) or 'source'}")
        if actions:
            parts.append(f"{_clock(hour['candle_start_ny'])} H1 candle " + ', '.join(actions) + '.')
    conclusion = f"The {name}: " + variant_clause(context.get('variant', {}), include_known=True)
    if context['conclusion']['status'] == 'pending_at_review_cutoff':
        conclusion += '; full DOL remains pending'
    elif context['conclusion']['status'] == 'unverified':
        conclusion += '; DOL unverified'
    if context.get('invalidated_at_ny'):
        conclusion += f"; invalidated on {closing_candle(context['invalidated_at_ny'])['spoken_label']}"
        if context['conclusion']['status'] == 'opposing_liquidity_delivered':
            conclusion += ' after recorded delivery'
    parts.append(conclusion + '.')
    if context.get('next_selected_range'):
        nxt = context['next_selected_range']
        parts.append(transition_sentence(nxt))
        if nxt.get('at_review_cutoff'):
            parts.append('No later evidence before the end of the GTOP shift.')
    if context.get('double_purge'):
        parts.append(_double_sentence(context['double_purge'], short=True))
    return ' '.join(parts)


def _compact_active_context(context):
    if not context:
        return None
    out = _pick(context, ('anchor_start_ny', 'selected_at_ny', 'selected_through_ny',
        'selection_status', 'crt_status', 'still_selected_at_cutoff', 'direction', 'invalidated_at_ny', 'variant_known_at_ny', 'next_selected_range'))
    out['conclusion'] = _pick(context['conclusion'], ('status', 'known_at_ny'))
    out['hourly_development'] = []
    for hour in context['hourly_development']:
        item = _pick(hour, ('candle_start_ny', 'own_range_crt_status_at_cutoff',
                           'candle_body_direction', 'candle_science', 'return_inside'))
        # Exact purge/objective intervals are already in this anchor's ranges
        # fact. Preserve the hour's relationship without a second interval copy.
        if hour.get('purge'):
            item['purge_side'] = hour['purge']['side']
        for key in ('midpoint', 'opposing_liquidity'):
            if hour.get(key):
                item[key + '_delivered'] = True
        out['hourly_development'].append(item)
    return out


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _first_event_time(fact):
    """Opening order comes from observed setup events, never branded priority."""
    event = (fact.get('first_purge_interval') or fact.get('paired_setup')
             or fact.get('physical_path_audit', {}).get('first_purge_interval') or {})
    value = event.get('bar_open_ny') or event.get('start_ny')
    return parse_time(value) if value else None


def _objective(value, level=None):
    out = _pick(value, ('status', 'level', 'liquidity_side'))
    if 'level' not in out and level is not None:
        out['level'] = level
    evidence = value.get('evidence')
    if evidence:
        out['source_interval'] = _pick(evidence, ('bar_open_ny', 'bar_close_ny', 'precision_seconds'))
    elif value.get('touch_bar_open_ny'):
        out['source_interval'] = {'bar_open_ny': value['touch_bar_open_ny']}
    return out


def _delivery_manner(fact, variant):
    """Reuse objective intervals; never promote partial delivery to completion."""
    labels = [v['code'] for v in fact['variant']['labels']]
    for name, milestone in variant.get('delivery_milestones', {}).items():
        if name not in ('midpoint', 'opposing_liquidity') or milestone.get('status') != 'observed':
            continue
        target = fact[name]
        # Paired objectives are reconciled separately and must not inherit a
        # different local direction's delivery manner.
        source = target.get('source_interval', {})
        evidence = milestone.get('source_interval', {})
        if not source or any(source.get(k) != evidence.get(k) for k in ('bar_open_ny', 'bar_close_ny')):
            continue
        code = milestone.get('manner', {}).get('primary_code')
        if not code:
            continue
        if name == 'opposing_liquidity' and code in labels:
            if len(labels) > 1:
                fact['variant']['primary_code'] = code
            if variant.get('status') != 'distribution_observed':
                target['delivery_manner'] = {'primary_code': code}
        elif name == 'opposing_liquidity' or labels != [code] or fact['outcome'] != 'opposing_liquidity_delivered':
            target['delivery_manner'] = {'primary_code': code}


def _shared_opening_evidence(opening, young, lead, young_fact, cutoff):
    """Join exact original body identities, never merely aligned directions."""
    if not young_fact or young_fact.get('young_lefty_context') or not lead.get('direction'):
        return None
    bodies, retirements = [], []
    identity_keys = ('bar_open_ny', 'bar_close_ny', 'timeframe', 'direction',
                     'open', 'high', 'low', 'close', 'identified_at_ny', 'source_resolution_seconds')
    for row, fact in ((young, young_fact), (opening, lead)):
        if fact.get('direction') != lead['direction'] or fact.get('paired_setup'):
            return None
        models = sorted((c for c in row.get('model1', {}).get('candles', [])
                         if c.get('direction') == fact['direction']), key=lambda c: c['bar_open_ny'])
        if not models:
            return None
        model = models[0]
        if (not model.get('complete') or any(model.get(k) is None for k in identity_keys)
                or model.get('purged_range_start_ny') != fact['anchor_start_ny']):
            return None
        known = parse_time(model['identified_at_ny'])
        if not parse_time(row['anchor']['end_ny']) <= parse_time(model['bar_open_ny']) < known <= parse_time(cutoff):
            return None
        prefix = row.get('context_qualification', {}).get('evidence_through_ny')
        if not prefix or parse_time(prefix) < known:
            return None
        retired = [fact.get('invalidated_at_ny')]
        if fact['outcome'] == 'opposing_liquidity_delivered':
            retired.append(fact['opposing_liquidity'].get('source_interval', {}).get('bar_close_ny'))
        if any(t and parse_time(t) <= known for t in retired):
            return None
        retirements.extend(t for t in retired if t)
        body = next((c for c in row.get('candle_lifecycle', {}).get('purge_candles', [])
                     if c.get('purge_type') == 'body_soup'
                     and c.get('range_start_ny') == fact['anchor_start_ny']
                     and _pick(c, identity_keys) == _pick(model, identity_keys)), None)
        if not body:
            return None
        bodies.append(body)
    if _pick(bodies[0], identity_keys) != _pick(bodies[1], identity_keys):
        return None
    result = {'parent_anchors_ny': [young_fact['anchor_start_ny'], lead['anchor_start_ny']],
              'direction': lead['direction'], 'model1': _pick(bodies[0],
                  ('bar_open_ny', 'bar_close_ny', 'timeframe', 'identified_at_ny')),
              'member_execution_inferred': False}
    soups = [body.get('super_soup', {}) for body in bodies]
    structures = [body.get('super_soup_structure', {}) for body in bodies]
    if (soups[0] == soups[1] and soups[0].get('status') in
            ('observed_before_csd', 'observed_same_assigned_close_as_csd')
            and soups[0].get('evidence') and all(s.get('structure_status') == 'observed' for s in structures)):
        evidence = soups[0]['evidence']
        returned = evidence.get('return_candle') or {}
        known = returned.get('bar_close_ny')
        if (known and parse_time(known) <= parse_time(cutoff)
                and not any(parse_time(t) <= parse_time(known) for t in retirements) and all(
                not b.get('sequence_gap_at_ny') or parse_time(known) <= parse_time(b['sequence_gap_at_ny']) for b in bodies)):
            result['super_soup'] = {'status': soups[0]['status'],
                **_pick(evidence.get('purge', {}), ('bar_open_ny', 'bar_close_ny')),
                'known_at_ny': known}
            previous = [next((c for c in b.get('following_candle_relations', [])
                if c.get('bar_close_ny') == result['super_soup'].get('bar_open_ny')
                and c.get('complete') and c.get('relationship') == 'inside_bar'), None) for b in bodies]
            if previous[0] and previous[0] == previous[1]:
                result['super_soup']['preceding_inside_bar_ny'] = previous[0]['bar_open_ny']
    csds = [b.get('csd', {}) for b in bodies]
    evidence = csds[0].get('evidence') or {}
    if (csds[0] == csds[1] and csds[0].get('status') == 'confirmed'
            and evidence.get('confirmed_at_ny') and parse_time(evidence['confirmed_at_ny']) <= parse_time(cutoff)
            and not any(parse_time(t) <= parse_time(evidence['confirmed_at_ny']) for t in retirements)
            and all(not b.get('sequence_gap_at_ny') or parse_time(evidence['confirmed_at_ny']) <=
                    parse_time(b['sequence_gap_at_ny']) for b in bodies)):
        result['strict_CISD'] = _pick(evidence, ('bar_open_ny', 'timeframe', 'confirmed_at_ny'))
    return result


def _shared_sentence(value):
    model = value['model1']
    text = 'Young Lefty and 9ate8 shared the ' + _clock(model['bar_open_ny']) + ' ' + model['timeframe'] + ' Model 1'
    soup = value.get('super_soup')
    if soup:
        text += '; Super Soup in ' + candle_label(soup['bar_open_ny'], model['timeframe'])
        if soup.get('preceding_inside_bar_ny'):
            text += ' after the ' + _clock(soup['preceding_inside_bar_ny']) + ' inside bar'
    csd = value.get('strict_CISD')
    if csd:
        text += '; strict CISD on the closure of ' + candle_label(csd['bar_open_ny'], csd['timeframe'])
    return text + '; each parent keeps its own targets and delivery.'


def compact_double_purge(row):
    """Carry confirmation facts; first purge stays on the enclosing range fact."""
    evidence = row.get('double_purge', {})
    if not evidence.get('observed') and not evidence.get('developing'):
        return None
    out = _pick(evidence, ('status', 'observed', 'original_first_purged_side',
        'original_completion_preserved', 'developing', 'confirmed_at_ny'))
    out['original_outcome'] = _pick(evidence.get('original_outcome', {}), ('direction', 'status'))
    sequence = evidence.get('sequence', {})
    out['sequence'] = {}
    for name in ('opposing_purge', 'source_return_inside', 'assigned_return_inside',
                 'selected_timeframe_return_inside'):
        event = sequence.get(name)
        keys = ('bar_open_ny', 'bar_close_ny') if name.endswith('purge') else (
            'bar_open_ny', 'timeframe', 'known_at_ny', 'close')
        out['sequence'][name] = _pick(event, keys) if event else None
    thesis = evidence.get('reversal_thesis', {})
    out['reversal_thesis'] = _pick(thesis, ('direction', 'status', 'objective_side',
        'objective_level', 'midpoint_level', 'midpoint_role', 'window_start_ny'))
    out['reversal_thesis']['objectives'] = {}
    for name, target in thesis.get('objectives', {}).items():
        fact = _pick(target, ('status', 'distance_price_points'))
        if target.get('evidence'):
            fact['source_interval'] = _pick(target['evidence'],
                ('bar_open_ny', 'bar_close_ny', 'timeframe', 'known_at_ny'))
        out['reversal_thesis']['objectives'][name] = fact
    return out


def _local_fact(row, play=None):
    outcome = row.get('directional_outcome') or directional_outcome(row)
    variant = row.get('variant_evidence', {})
    labels = [_pick(v, ('code', 'name')) for v in variant.get('labels', [])]
    invalid = outcome.get('range_invalidated_at_ny')
    status = outcome['status']
    initiated = any(e['kind'].endswith('_side_purge') for e in row.get('events', []))
    verdict = ('delivered' if status == 'opposing_liquidity_delivered' else
               'failed' if invalid and status in ('midpoint_only', 'failed_before_objectives') else
               'unverified' if status == 'unverified' and initiated else
               'unverified' if not row.get('anchor', {}).get('complete') else
               'not_initiated' if not initiated else
               'clean' if variant.get('manipulation_closed_outside') is False else 'pending')
    first = next((e for e in row.get('events', []) if e['kind'].endswith('_side_purge')), {})
    fact = {'anchor_start_ny': row['anchor']['start_ny'], 'anchor_timeframe': 'H1', 'play': play,
            'role': row.get('role', 'independent_range_context'),
            'direction': outcome['direction'], 'verdict': verdict, 'outcome': status,
            'variant': {'status': 'structure_observed' if variant.get('status') == 'structure_observed_distribution_unresolved' else
                            'established' if labels else 'not_established' if invalid else
                            variant.get('explanation', {}).get('status', 'pending'),
                        'labels': labels, **({'explanation': {k: deepcopy(v) for k, v in variant['explanation'].items()
                            if v is not None and v != []}}
                            if variant.get('explanation') and (labels or variant['explanation'].get('candidates')) else {})},
            'midpoint': _objective(outcome['midpoint'], row['anchor'].get('midpoint')),
            'opposing_liquidity': _objective(outcome['opposing_liquidity'],
                row['anchor'].get('high' if outcome['direction'] == 'bullish' else 'low') if outcome['direction'] else None),
            'invalidated_at_ny': invalid,
            'first_purge_interval': _pick(first, ('bar_open_ny', 'bar_close_ny', 'precision_seconds')),
            'coverage_complete': row.get('observation_coverage', {}).get('complete', False)}
    _delivery_manner(fact, variant)
    double = compact_double_purge(row) if play == '9ate8' and row.get('role') == 'selected_range' else None
    if double:
        fact['double_purge'] = double
    if play == 'Young Lefty' and row.get('young_lefty_context'):
        from gbop_voice_web.young_lefty_context import compact_young_context, neutral_thesis_fact
        fact['young_lefty_context'] = compact_young_context(row['young_lefty_context'])
        neutral_thesis_fact(fact)
    pending = None if fact.get('young_lefty_context') else pending_range_facts(row, fact)
    if pending:
        fact['pending_range'] = pending
    reversal = None if fact.get('young_lefty_context') else pending_reversal_facts(row)
    if reversal:
        fact['pending_reversal'] = reversal
    return fact


def _paired_fact(row, records, local, through_ny=None):
    # Reconciliation has already rejected same-interval dual purges and scoped
    # objectives to this asset. Never use another asset's target or a later
    # opposite local attempt to overwrite the qualifying paired thesis.
    record = next((r for r in records if r.get('anchor_start_ny') == local['anchor_start_ny']
                   and r.get('asset_role') in ('boneless leg', 'potential boneless leg')), None)
    if not record:
        return local
    setup = record.get('setup_interval', {})
    known = setup.get('qualified_at_ny') or setup.get('end_ny')
    if through_ny and known and parse_time(known) > parse_time(through_ny):
        return local
    own = record.get('objective_status', {})
    full = own.get('opposing_liquidity', {})
    mid = own.get('midpoint', {})
    complete = full.get('status') == 'objective_complete_while_range_valid'
    potential = record.get('setup_interval', {}).get('potential_smt', False)
    invalid = full.get('range_invalidated_at_ny')
    failed = full.get('status') == 'not_completed_before_invalidation'
    status = ('opposing_liquidity_delivered' if complete else
              'midpoint_only' if mid.get('status') == 'objective_complete_while_range_valid' else
              'failed_before_objectives' if failed else
              'pending_at_review_cutoff' if full.get('status') == 'pending_at_review_cutoff' else 'unverified')
    verdict = ('boneless_potential' if potential else 'boneless_delivered' if complete else
               'boneless_failed' if failed else 'boneless_pending')
    if local['outcome'] == 'opposing_liquidity_delivered' and not complete:
        # Completed local delivery cannot be downgraded by a different, still
        # pending paired direction. Keep the alternative scoped and separate.
        return {**local, 'paired_alternative': {
            'direction': record['direction'], 'verdict': verdict, 'outcome': status,
            'midpoint': _objective(mid), 'opposing_liquidity': _objective(full),
            'setup_interval': _pick(record.get('setup_interval', {}), (
                'start_ny', 'end_ny', 'qualified_smt', 'potential_smt'))}}
    return {**local, 'direction': record['direction'],
            'verdict': verdict,
            'outcome': status, 'midpoint': _objective(mid), 'opposing_liquidity': _objective(full),
            'invalidated_at_ny': invalid,
            # A local opposite-direction variant is not the boneless variant.
            'variant': {'status': 'pending' if not failed else 'not_established', 'labels': []},
            'paired_setup': _pick(record.get('setup_interval', {}), (
                'start_ny', 'end_ny', 'qualified_smt', 'potential_smt', 'qualified_at_ny')),
            'local_direction': local['direction'], 'first_purge_interval': {}}


def _delivery_text(fact):
    full, mid = fact['opposing_liquidity'], fact['midpoint']
    full_done = full.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
    mid_done = mid.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
    side = 'sell-side' if fact['direction'] == 'bearish' else 'buy-side' if fact['direction'] == 'bullish' else 'opposing liquidity'
    if full_done:
        text = ('50% and ' if mid_done else '') + f'{side} delivered'
    elif mid_done:
        uncertain = any(word in full.get('status', '') for word in ('unverified', 'unresolved', 'unknown'))
        text = f'50% delivered; {side} ' + ('delivery unverified' if uncertain else
            'not delivered before invalidation' if fact['invalidated_at_ny'] else 'pending')
    elif fact['outcome'] == 'failed_before_objectives':
        text = f'failed before 50%/{side} delivery'
    elif fact['verdict'] == 'not_initiated':
        text = 'no directional setup established'
    elif fact['outcome'] == 'unverified':
        text = f'50%/{side} delivery unverified'
    else:
        text = f'50%/{side} delivery pending'
    if full_done:
        evidence = full.get('source_interval', {})
        if evidence.get('bar_open_ny'):
            text += ' in ' + candle_label(evidence['bar_open_ny'], source_timeframe(evidence.get('precision_seconds')))
    return text


def _sentence(fact, *, young=False, compact=False):
    if fact.get('young_lefty_context'):
        from gbop_voice_web.young_lefty_context import young_context_sentence
        return young_context_sentence(fact['young_lefty_context'])
    name = f"{fact['play']} ({_clock(fact['anchor_start_ny'])} H1 range)" if fact.get('play') else f"The {_clock(fact['anchor_start_ny'])} H1 range"
    intro = (('failed ' if fact['verdict'] in ('failed', 'boneless_failed') else '')
             + (fact.get('direction') or 'direction unverified'))
    if fact['verdict'].startswith('boneless'):
        intro += ' boneless' + (' potential' if fact['verdict'] == 'boneless_potential' else '')
    elif fact['verdict'] == 'clean':
        intro += ' clean setup'
    delivery = _delivery_text(fact)
    text = (f'{name}: {intro} ' + delivery.removeprefix('failed ')
            if compact and fact['verdict'] in ('failed', 'boneless_failed')
            and delivery.startswith('failed ') else f'{name}: {intro}, {delivery}')
    if fact['verdict'] == 'boneless_potential':
        text += '; setup qualification pending'
    full = fact['opposing_liquidity']
    code = full.get('delivery_manner', {}).get('primary_code')
    known_manner = (code in NAMES and fact['outcome'] == 'opposing_liquidity_delivered'
                   and full.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid'))
    if known_manner:
        text += '; completed in ' + variant_clause({'labels': [{'code': code, 'name': NAMES[code]}]}) + ' manner'
        detail = fact['variant'].get('explanation', {})
        if detail.get('status') != 'completed':
            # A later own-timeframe closure can still be needed for structural
            # confirmation. It cannot make an ordered source delivery pending.
            needs = [c['requires'].rstrip('.') for c in detail.get('candidates', [])
                     if c.get('code') == code and c.get('requires')]
            text += '; own-timeframe structural confirmation pending'
            if needs:
                text += ': ' + '; '.join(needs)
    elif (fact['variant']['labels'] or fact['variant'].get('explanation', {}).get('candidates')
            or not young and fact['verdict'] != 'not_initiated'):
        variant = fact['variant']
        if variant.get('primary_code'):
            variant = {**variant, 'labels': sorted(variant['labels'],
                       key=lambda v: v['code'] != variant['primary_code'])}
        timing = [v for v in variant['labels'] if v['code'] == 'V3'
                  and variant.get('primary_code') not in (None, 'V3')]
        if timing:
            variant = {**variant, 'labels': [v for v in variant['labels'] if v not in timing]}
            name = variant_clause({'labels': variant['labels']})
            clause = variant_clause(variant)
            text += '; ' + name + ' (' + variant_clause({'labels': timing}) + ' timing)' + clause[len(name):]
        else:
            text += '; ' + variant_clause(variant)
    if fact.get('invalidated_at_ny'):
        later = 'later ' if fact['outcome'] == 'opposing_liquidity_delivered' else ''
        text += '; ' + later + 'invalidated on ' + closing_candle(fact['invalidated_at_ny'])['spoken_label']
    alternative = fact.get('paired_alternative')
    if alternative:
        state = ('potential, qualification pending' if alternative['verdict'] == 'boneless_potential' else
                 'failed before full delivery' if alternative['verdict'] == 'boneless_failed' else
                 'delivery unverified' if alternative['outcome'] == 'unverified' else 'full delivery pending')
        text += f"; separate {alternative['direction']} boneless {state}"
    if young and fact.get('opposes_9ate8'):
        text += ', opposite 9ate8'
    if fact['role'] == 'independent_range_context' and not fact.get('play'):
        text += ' (independent range)'
    return text + '.'


def _later_range_relevance(story, row, fact):
    """A real transition or closed setup evidence, never just another hour."""
    cutoff = parse_time(story['end_ny'])
    anchor_close = parse_time(row['anchor']['end_ny'])
    selected_at = row.get('selected_at_ny')
    if row.get('role') == 'selected_range' and selected_at:
        if anchor_close <= parse_time(selected_at) <= cutoff:
            return {'basis': 'selected_range_transition', 'known_at_ny': selected_at}
        return None
    if anchor_close >= cutoff:
        return None
    paired = fact.get('paired_setup')
    if paired:
        opened = paired.get('start_ny')
        known = paired.get('qualified_at_ny') or paired.get('end_ny')
        if not opened or not known or not anchor_close <= parse_time(opened) < parse_time(known) <= cutoff:
            return None
        relevance = {'basis': 'paired_setup', 'known_at_ny': known}
    else:
        event = fact.get('first_purge_interval') or {}
        opened, known = event.get('bar_open_ny'), event.get('bar_close_ny')
        if not opened or not known or not anchor_close <= parse_time(opened) < parse_time(known) <= cutoff:
            return None
        sides = {e['kind'].split('_')[0] for e in row.get('events', [])
                 if e['kind'].endswith('_side_purge') and e.get('bar_open_ny') == opened}
        relevance = {'basis': 'post_close_purge', 'known_at_ny': known,
                     'purged_side': next(iter(sides)) if len(sides) == 1 else 'both'}
    selected = [r for r in story.get('ranges', []) if r.get('selected_at_ny')
                and parse_time(r['selected_at_ny']) <= parse_time(opened)]
    if selected:
        reference = max(selected, key=lambda r: parse_time(r['selected_at_ny']))
        relevance['selected_anchor_ny'] = reference['anchor_start_ny']
    return relevance


def _independent_later_sentence(fact):
    """Keep a later range's cause and outcome audible without another full recap."""
    relevance = fact['relevance']
    text = f"Independent {_clock(fact['anchor_start_ny'])} H1 range: {fact.get('direction') or 'direction unverified'}"
    if relevance['basis'] == 'post_close_purge':
        side = relevance['purged_side']
        source = source_timeframe(fact['first_purge_interval'].get('precision_seconds')) or 'source candle'
        text += (f"; {_clock(fact['first_purge_interval']['bar_open_ny'])} {source} "
                 + ('two-sided' if side == 'both' else side + '-side') + ' purge')
    else:
        text += ' boneless' + (' potential' if fact['verdict'] == 'boneless_potential' else '')
        text += f" from paired setup by {_clock(relevance['known_at_ny'])}"
    side = ('sell-side' if fact['direction'] == 'bearish' else
            'buy-side' if fact['direction'] == 'bullish' else 'opposing liquidity')
    outcome = {'failed_before_objectives': f'failed before 50%/{side}',
               'pending_at_review_cutoff': f'50%/{side} pending',
               'unverified': f'50%/{side} unverified'}.get(fact['outcome'])
    text += '; ' + (outcome or _delivery_text(fact))
    if fact.get('invalidated_at_ny'):
        text += ('; later invalidated ' if fact['outcome'] == 'opposing_liquidity_delivered'
                 else '; invalidated ') + 'on ' + closing_candle(fact['invalidated_at_ny'])['spoken_label']
    labels = fact.get('variant', {}).get('labels', [])
    if labels:
        text += '; ' + variant_clause({'labels': labels})
    candidates = fact.get('variant', {}).get('explanation', {}).get('candidates', [])
    if candidates:
        # Factor only these exact shared conditions. Other conditional paths
        # retain their complete explanation rather than losing a prerequisite.
        timing = {'Ordered opposing delivery in candle 3 with complete H1 evidence.': 'in',
                  'Ordered opposing delivery after candle 3 with complete H1 evidence.': 'after'}
        if all(c.get('requires') in timing for c in candidates):
            names = variant_clause({'labels': candidates})
            when = '/'.join(timing[c['requires']] for c in candidates)
            text += f"; {names} candidate because {fact['variant']['explanation']['reason']}: ordered opposing delivery {when} candle 3, complete H1 required"
        else:
            text += '; ' + variant_clause(fact['variant'])
    return text + '.'


def _failed_secondary_sentence(fact, continuity=None, *, transitions_only=False):
    """Keep the verdict and handoffs audible; mechanics remain in exact detail."""
    parts = []
    if transitions_only:
        for transition in ((continuity or {}).get('review_handoff'),
                           (continuity or {}).get('next_selected_range')):
            if transition:
                cause = ('full delivery' if transition['reason'] == 'opposing_objective_completed' else 'invalidation')
                parts.append(f"After {_clock(transition['from_anchor_ny'])} H1 {cause}, "
                    f"{_clock(transition['to_anchor_ny'])} H1 came under review at {_clock(transition['confirmed_at_ny'])}.")
                if transition.get('at_review_cutoff'):
                    parts.append('No later evidence before the end of the GTOP shift.')
        return ' '.join(parts)
    if continuity and continuity.get('review_handoff'):
        parts.append(transition_sentence(continuity['review_handoff']))
    text = ('The ' if continuity else 'Independent ') + f"{_clock(fact['anchor_start_ny'])} H1 range: {fact['direction']}"
    text += ', 50% only' if fact['outcome'] == 'midpoint_only' else ', failed before its objectives'
    text += '; invalidated on ' + closing_candle(fact['invalidated_at_ny'])['spoken_label']
    if fact.get('variant', {}).get('labels'):
        text += '; ' + variant_clause(fact['variant'])
    parts.append(text + '.')
    if continuity and continuity.get('next_selected_range'):
        parts.append(transition_sentence(continuity['next_selected_range']))
        if continuity['next_selected_range'].get('at_review_cutoff'):
            parts.append('No later evidence before the end of the GTOP shift.')
    return ' '.join(parts)


def build_shift_synopsis(review, asset=None):
    """Produce bounded facts without changing the authoritative full review."""
    story = review['shift_story']
    ranges = story.get('ranges', [])
    opening = next((r for r in ranges if r.get('label') == '9ate8'), ranges[0] if ranges else None)
    if not opening:
        return {'spoken_summary': 'Shift evidence is unavailable. Young Lefty: unverified.',
                'ranges': [], 'range_index': [], 'young_lefty_status': 'unverified',
                'negative_claim_guard': negative_claim_guard('unverified', story),
                'response_contract': SYNOPSIS_CONTRACT}
    records = story.get('recap', {}).get('paired_interpretation', [])
    lead = _paired_fact(opening, records, _local_fact(opening, '9ate8'), story['end_ny'])
    lead_sentence = _sentence(lead, compact=True)
    approach = opening.get('objective_approach', {}).get('objectives', {}).get('midpoint', {})
    annotation = owner_inducement_example(asset, opening['anchor'], lead['direction'], approach)
    if annotation:
        lead['midpoint_approach'] = {'status': approach['status'],
            'distance_price_points': approach['distance_price_points'], 'gtop_context': annotation,
            'boundary_to_target_reference': deepcopy(approach['target_approach']['boundary_to_target_reference'])}
        lead_sentence = lead_sentence.rstrip('.') + '; ' + inducement_clause(lead['midpoint_approach'], lead['direction']) + '.'
    facts, sentences = [lead], [lead_sentence]
    # The opening range is skipped by the later selected-range loop. Its
    # already-verified double purge must survive the default synopsis too.
    opening_double = _double_context(opening, asset, story)
    if opening_double:
        sentences.append(_double_sentence(opening_double, short=True))
    # Seven's own range is independent of eight and nine. An invalidated local
    # eight/nine or an opposite-direction paired thesis must not hide it.
    young = next((o.get('evidence') for o in review.get('observations', [])
                  if o.get('play') == 'Young Lefty'), None)
    young_relevant = False
    young_fact = None
    young_status = 'unverified'
    young_sentence = 'Young Lefty: unverified; missing or unfinished seven-range evidence.'
    young_coverage, missing_sentence = _missing_young_anchor(young)
    if missing_sentence:
        young_sentence = missing_sentence
    if young and young.get('anchor', {}).get('complete'):
        start = parse_time(young['anchor']['start_ny'])
        early = [e for e in young.get('events', []) if e['kind'].endswith('_side_purge')
                 and start + 3600 <= parse_time(e['bar_open_ny']) < start + 3 * 3600]
        if early:
            item = _local_fact(young, 'Young Lefty')
            item['opposes_9ate8'] = (None if item.get('young_lefty_context') else
                bool(item['direction'] and lead['direction'] and item['direction'] != lead['direction']))
            facts.append(item)
            young_fact = item
            young_sentence = _sentence(item, young=True, compact=True)
            if item.get('pending_range'):
                young_sentence += ' ' + pending_sentence(item['pending_range'], item['anchor_start_ny'])
            if item.get('pending_reversal'):
                young_sentence += ' ' + pending_reversal_sentence(item['pending_reversal'], item['anchor_start_ny'])
            young_status = 'context_dependent' if item.get('young_lefty_context') else item['verdict']
            young_relevant = True
        elif young.get('observation_coverage', {}).get('complete'):
            young_status = 'absent'
            young_sentence = 'Young Lefty: absent; no early seven-range purge by eight/nine.'
    if lead.get('pending_range'):
        sentences.append(pending_sentence(lead['pending_range'], lead['anchor_start_ny']))
    if lead.get('pending_reversal'):
        sentences.append(pending_reversal_sentence(lead['pending_reversal'], lead['anchor_start_ny'],
                         include_confirmation=not bool(opening_double)))
    young_time = _first_event_time(young_fact) if young_fact else None
    lead_time = _first_event_time(lead)
    if young_time is not None and lead_time is not None and young_time < lead_time:
        sentences.insert(0, young_sentence)
    else:
        # Absent, incomplete or tied evidence does not establish earlier order.
        sentences.append(young_sentence)
    shared = _shared_opening_evidence(opening, young, lead, young_fact, story['end_ny'])
    completed_shared = bool(shared and all(f['outcome'] == 'opposing_liquidity_delivered' for f in (lead, young_fact)))
    if shared:
        # Opening conclusions stay first; shared support precedes later ranges.
        sentences.insert(sentences.index(young_sentence) + 1 if young_time is None or lead_time is None
                         or young_time >= lead_time else 2, _shared_sentence(shared))
    if (completed_shared and opening_double and opening_double.get('invalidated_at_ny')
            and opening_double.get('reversal_outcome') != 'original_side_delivered'):
        sentences.remove(_double_sentence(opening_double, short=True))
    cutoff_transitions_spoken = set()
    brief_failures = []
    for row in ranges:
        if row is opening:
            continue
        item = _paired_fact(row, records, _local_fact(row), story['end_ny'])
        relevance = _later_range_relevance(story, row, item)
        if not relevance:
            continue
        item['relevance'] = relevance
        facts.append(item)
        if row.get('selected_at_ny') == story['end_ny']:
            # A genuine final transition is relevant, but cannot establish a
            # new setup or outcome without any post-selection observations.
            item.update(verdict='unverified', outcome='unverified',
                        observation_status='no_post_close_evidence_at_cutoff')
            if item['anchor_start_ny'] not in cutoff_transitions_spoken:
                transition = next(t for t in story['range_transitions'] if t['to_anchor_ny'] == item['anchor_start_ny'])
                sentences.append(transition_sentence(transition) + ' At the end of the GTOP shift, later setup/delivery is unknown.')
        else:
            continuity = selected_range_story(story, row, item, asset)
            failed_secondary = item['verdict'] in ('failed', 'boneless_failed') and item['coverage_complete']
            if completed_shared and failed_secondary:
                brief_failures.append(f"{_clock(item['anchor_start_ny'])} {item['direction']}")
            summary = (_failed_secondary_sentence(item, continuity, transitions_only=completed_shared)
                if failed_secondary and (completed_shared or continuity) else
                _short_selected_summary(continuity) if continuity else _independent_later_sentence(item))
            if summary:
                sentences.append(summary)
            if continuity and item.get('paired_setup'):
                known = item['paired_setup'].get('qualified_at_ny') or item['paired_setup'].get('end_ny')
                sentences.append(f"Its {item['direction']} boneless from paired setup by {_clock(known)}: "
                                 + _delivery_text(item) + '.')
            if item.get('pending_range'):
                sentences.append(pending_sentence(item['pending_range'], item['anchor_start_ny']))
            if item.get('pending_reversal'):
                sentences.append(pending_reversal_sentence(item['pending_reversal'], item['anchor_start_ny'],
                                 include_confirmation=not bool(continuity and continuity.get('double_purge'))))
            if continuity and (continuity.get('next_selected_range') or {}).get('at_review_cutoff'):
                cutoff_transitions_spoken.add(continuity['next_selected_range']['to_anchor_ny'])
    if brief_failures:
        sentences.append('Secondary ' + ' and '.join(brief_failures) + ' H1 attempts failed before full delivery; details remain available by range.')
    if not story.get('coverage', {}).get('complete') or not story.get('progression_complete'):
        sentences.append('Missing or unfinished candles limit the affected ranges.')
    # Every named range, including an uninitiated seven, remains recoverable on
    # demand; an index is navigation, not evidence of a setup or its absence.
    anchors = [(r['anchor_start_ny'], r.get('label'), r.get('role')) for r in ranges]
    if young and young.get('anchor', {}).get('start_ny'):
        anchors.append((young['anchor']['start_ny'], 'Young Lefty', 'independent_range_context'))
    index = [{'anchor_start_ny': start, 'label': label, 'role': role,
              'detail_request': {'tool': 'review_market_crt', 'args': {
                  'asset': asset, 'context_action': 'continue', 'anchor_start_ny': start,
                  'anchor_timeframe': 'H1', 'through_ny': story['end_ny']}}}
             for start, label, role in sorted(anchors)]
    ending = shift_end_state(story)
    graph = build_context_graph(story, young)
    # Failed secondary countertrend mechanics remain structured/retrievable.
    # Pending contexts and coverage-limited relationships still need narration.
    spoken_graph = deepcopy(graph)
    if story.get('coverage', {}).get('complete') and story.get('progression_complete'):
        failed = {c['context_id'] for c in graph['contexts'] if c['status'] == 'invalidated'
                  and c['retirement_reason'] == 'range_invalidated'}
        spoken_graph['relationships'] = [r for r in graph['relationships']
                                        if r['later_context_id'] not in failed]
    if context_sentence(spoken_graph):
        sentences.append(context_sentence(spoken_graph))
    compact_graph = compact_context_graph(spoken_graph if completed_shared else graph)
    omitted = len(graph['relationships']) - len(spoken_graph['relationships'])
    if completed_shared and omitted:
        # The full graph remains on shift_story; exact range navigation covers
        # these retired secondary contexts without using default recap space.
        compact_graph['failed_secondary_relationships_omitted_count'] = omitted
    sentences.append(ending['spoken_summary'])
    guard = negative_claim_guard(young_status, story)
    return {'spoken_summary': ' '.join(sentences), 'ranges': facts, 'range_index': index,
            **({'shared_setup_evidence': shared} if shared else {}),
            **({'negative_claim_guard': guard} if guard else {}),
            **({'young_lefty_coverage': young_coverage} if young_coverage else {}),
            'active_range_context': _compact_active_context(_active_context(story, records, asset=asset)),
            'chronological_context': compact_graph,
            'shift_end': {k: v for k, v in ending.items() if k != 'spoken_summary'},
            'young_lefty_evaluated': True, 'young_lefty_relevant': young_relevant,
            'young_lefty_status': young_status,
            'coverage_complete': story.get('coverage', {}).get('complete', False),
            'through_ny': story['end_ny'], 'response_contract': SYNOPSIS_CONTRACT}


def build_other_ranges(review, asset=None, discussed=(), *, continue_active=False, anchor_start_ny=None):
    """Remaining evidence in hourly order, not just named or winning setups.

    This is an explicit follow-up view; the short default synopsis remains
    unchanged. A final closed range without observation bars is included with
    an explicit cutoff limit, never presented as another completed setup.
    """
    story = review['shift_story']
    excluded = set(discussed)
    records = story.get('recap', {}).get('paired_interpretation', [])
    synopsis = build_shift_synopsis(review, asset)
    active = _active_context(story, records, anchor_start_ny if continue_active else None, asset)
    ending = shift_end_state(story)
    if continue_active:
        if active is None:
            raise ValueError('The active range is unverified because the selected-range progression is incomplete.')
        path, current = [], active
        while current.get('next_selected_range'):
            current = _active_context(story, records, current['next_selected_range']['to_anchor_ny'], asset)
            if current is None:
                break
            path.append(current)
        return {'mode': 'continue_active_range', 'spoken_summary': ' '.join(
                    [active['spoken_summary']] + [r['spoken_summary'] for r in path] + [ending['spoken_summary']]),
                'ranges': [], 'active_range_context': active, 'next_selected_context': path,
                'shift_end': {k: v for k, v in ending.items() if k != 'spoken_summary'}, 'excluded_discussed_anchors': sorted(excluded),
                'all_ranges_discussed': False, 'through_ny': story['end_ny'],
                'coverage_complete': story.get('coverage', {}).get('complete', False),
                'response_contract': OTHER_RANGES_CONTRACT}
    # A newly selected cutoff range has no later development. Preserve the
    # preceding story and its genuine transition before independent candidates.
    # Likewise keep a discussed unbranded selected range's completion bridge
    # when a later selected range is still an undisclosed opportunity.
    if active and active.get('selected_at_ny') == story['end_ny']:
        prior = next((t['from_anchor_ny'] for t in story.get('range_transitions', [])
                      if t['to_anchor_ny'] == active['anchor_start_ny']), None)
        if prior:
            active = _active_context(story, records, prior, asset)
    discussed_selected = [r for r in story.get('ranges', []) if r.get('role') == 'selected_range'
        and r['anchor_start_ny'] in excluded and r.get('label') != '9ate8']
    if discussed_selected:
        prior = max(discussed_selected, key=lambda r: r['anchor_start_ny'])
        active = _active_context(story, records, prior['anchor_start_ny'], asset)
    facts = [deepcopy(row) for row in synopsis['ranges'] if row.get('play') == 'Young Lefty']
    for row in story.get('ranges', []):
        play = row.get('label') if row.get('label') in {'9ate8', 'Young Lefty'} else None
        facts.append(_paired_fact(row, records, _local_fact(row, play), story['end_ny']))
    index = {row['anchor_start_ny']: row for row in synopsis['range_index']}
    remaining, sentences = [], []
    if active and active['anchor_start_ny'] in excluded:
        sentences.append(_continuity_bridge(active))
    for fact in sorted(facts, key=lambda row: row['anchor_start_ny']):
        anchor = fact['anchor_start_ny']
        if anchor in excluded:
            continue
        fact['detail_request'] = deepcopy(index[anchor]['detail_request'])
        closes = parse_time(anchor) + 3600
        if closes >= parse_time(story['end_ny']):
            fact['observation_status'] = 'no_post_close_evidence_at_cutoff'
            text = (f"The {_clock(anchor)} H1 range closes at the {_clock(story['end_ny'])} "
                    'review cutoff; no later setup or delivery evidence is available.')
        else:
            fact['observation_status'] = 'observed' if fact['coverage_complete'] else 'incomplete'
            row = next((r for r in story['ranges'] if r['anchor_start_ny'] == anchor), None)
            continuity = selected_range_story(story, row, fact, asset) if row else None
            text = (continuity['spoken_summary'] if continuity and not fact.get('play') else
                    _sentence(fact, young=fact.get('play') == 'Young Lefty'))
            if fact['role'] == 'independent_range_context' and not fact.get('play'):
                text = 'Separately, treating this candle as an independent range: ' + text
            if fact.get('pending_range'):
                text += ' ' + pending_sentence(fact['pending_range'], fact['anchor_start_ny'])
            if fact.get('pending_reversal'):
                text += ' ' + pending_reversal_sentence(fact['pending_reversal'], fact['anchor_start_ny'])
        remaining.append(fact)
        sentences.append(text)
    if not remaining:
        sentences.append('All retained ranges in this shift have already been discussed. '
                         'Name a range to revisit it, or explicitly start the review over.')
    sentences.append(ending['spoken_summary'])
    if active:
        # The same selected-range prose is already in spoken_summary or the
        # continuity bridge; keep its complete structured evidence only once.
        active.pop('spoken_summary', None)
    return {'mode': 'other_ranges', 'spoken_summary': ' '.join(sentences), 'ranges': remaining,
            'active_range_context': active, 'shift_end': {k: v for k, v in ending.items() if k != 'spoken_summary'},
            'excluded_discussed_anchors': sorted(excluded),
            'all_ranges_discussed': not remaining,
            'through_ny': story['end_ny'], 'coverage_complete': story.get('coverage', {}).get('complete', False),
            'response_contract': OTHER_RANGES_CONTRACT}
