"""Short, evidence-only default shift reply; detailed range evidence stays queryable.

The seven o'clock range is evaluated independently even when eight/nine fails.
Only a real early boundary purge makes Young Lefty relevant to the default reply.
No assigned-candle candidate list or entry inference is needed for this view.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.candle_naming import candle_label, source_timeframe
from gbop_voice_web.shift_narrative import directional_outcome
from gbop_voice_web.smt_reference import closing_candle


SYNOPSIS_CONTRACT = (
    'Default shift answer: use spoken_summary, briefly. Lead with 9ate8 direction/verdict, '
    'supported variant or pending, and its own 50%/opposing delivery/invalidation. '
    'Then only relevant named ranges in chronology. Young Lefty is independently evaluated '
    'on seven: mention only supplied relevant evidence; it can oppose 9ate8. '
    'Omitted candidates are not absent. Model 1, CISD, Soup and detailed candle questions '
    'require the exact range detail_request; never infer fills from delivery.')

OTHER_RANGES_CONTRACT = (
    'Answer the other-ranges follow-up using spoken_summary and ranges in chronology. '
    'Previously discussed anchors are excluded even when they failed; do not repeat them. '
    'An unbranded H1 CRT is still relevant: play=null never means absent. '
    'Keep each range\'s own direction, variant, objectives, invalidation and selection role. '
    'Independent context is not a selected-range transition. A range closing at the cutoff '
    'has no later delivery evidence. Say the explicit H1 range opening for each range '
    'actually discussed; retrieval alone is not a completed spoken response.')


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _objective(value, level=None):
    out = _pick(value, ('status', 'level', 'liquidity_side', 'spoken_label'))
    if 'level' not in out and level is not None:
        out['level'] = level
    evidence = value.get('evidence')
    if evidence:
        out['source_interval'] = _pick(evidence, ('bar_open_ny', 'bar_close_ny', 'precision_seconds'))
    elif value.get('touch_bar_open_ny'):
        out['source_interval'] = {'bar_open_ny': value['touch_bar_open_ny']}
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
    return {'anchor_start_ny': row['anchor']['start_ny'], 'anchor_timeframe': 'H1', 'play': play,
            'role': row.get('role', 'independent_range_context'),
            'direction': outcome['direction'], 'verdict': verdict, 'outcome': status,
            'variant': {'status': 'established' if labels else 'not_established' if invalid else 'pending',
                        'labels': labels},
            'midpoint': _objective(outcome['midpoint'], row['anchor'].get('midpoint')),
            'opposing_liquidity': _objective(outcome['opposing_liquidity'],
                row['anchor'].get('high' if outcome['direction'] == 'bullish' else 'low') if outcome['direction'] else None),
            'invalidated_at_ny': invalid,
            'first_purge_interval': _pick(first, ('bar_open_ny', 'bar_close_ny', 'precision_seconds')),
            'coverage_complete': row.get('observation_coverage', {}).get('complete', False)}


def _paired_fact(row, records, local):
    # Reconciliation has already rejected same-interval dual purges and scoped
    # objectives to this asset. Never use another asset's target or a later
    # opposite local attempt to overwrite the qualifying paired thesis.
    record = next((r for r in records if r.get('anchor_start_ny') == local['anchor_start_ny']
                   and r.get('asset_role') in ('boneless leg', 'potential boneless leg')), None)
    if not record:
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


def _sentence(fact, *, young=False):
    name = f"{fact['play']} ({_clock(fact['anchor_start_ny'])} H1 range)" if fact.get('play') else f"The {_clock(fact['anchor_start_ny'])} H1 range"
    intro = (fact.get('direction') or 'direction unverified')
    if fact['verdict'].startswith('boneless'):
        intro += ' boneless' + (' potential' if fact['verdict'] == 'boneless_potential' else '')
    elif fact['verdict'] == 'clean':
        intro += ' clean setup'
    text = f'{name}: {intro}, {_delivery_text(fact)}'
    if fact['verdict'] == 'boneless_potential':
        text += '; setup qualification pending'
    labels = fact['variant']['labels']
    if labels:
        text += '; ' + '/'.join(v['code'] + ' ' + v['name'] for v in labels)
    elif not young and fact['verdict'] != 'not_initiated':
        text += '; variant ' + ('not established' if fact['variant']['status'] == 'not_established' else 'pending')
    if fact.get('invalidated_at_ny'):
        text += '; ' + ('later invalidated on ' if fact['outcome'] == 'opposing_liquidity_delivered' else 'invalidated on ')
        text += closing_candle(fact['invalidated_at_ny'])['spoken_label']
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


def build_shift_synopsis(review, asset=None):
    """Produce bounded facts without changing the authoritative full review."""
    story = review['shift_story']
    ranges = story.get('ranges', [])
    opening = next((r for r in ranges if r.get('label') == '9ate8'), ranges[0] if ranges else None)
    if not opening:
        return {'spoken_summary': 'Shift evidence is unavailable.', 'ranges': [], 'range_index': [],
                'response_contract': SYNOPSIS_CONTRACT}
    records = story.get('recap', {}).get('paired_interpretation', [])
    lead = _paired_fact(opening, records, _local_fact(opening, '9ate8'))
    facts, sentences = [lead], [_sentence(lead)]
    # Seven's own range is independent of eight and nine. An invalidated local
    # eight/nine or an opposite-direction paired thesis must not hide it.
    young = next((o.get('evidence') for o in review.get('observations', [])
                  if o.get('play') == 'Young Lefty'), None)
    young_relevant = False
    if young and young.get('anchor', {}).get('complete'):
        start = parse_time(young['anchor']['start_ny'])
        early = [e for e in young.get('events', []) if e['kind'].endswith('_side_purge')
                 and start + 3600 <= parse_time(e['bar_open_ny']) < start + 3 * 3600]
        if early:
            item = _local_fact(young, 'Young Lefty')
            item['opposes_9ate8'] = bool(item['direction'] and lead['direction'] and item['direction'] != lead['direction'])
            facts.append(item)
            sentences.append(('Earlier, ' if parse_time(early[0]['bar_open_ny']) < parse_time(story['start_ny']) else 'Separately, ')
                             + _sentence(item, young=True))
            young_relevant = True
    for row in ranges:
        if row is opening:
            continue
        item = _paired_fact(row, records, _local_fact(row))
        # No untouched candidate parade or range selected only at the cutoff.
        relevant = (item['verdict'] not in ('not_initiated',) and
                    (row.get('role') == 'selected_range' or item['outcome'] == 'opposing_liquidity_delivered'))
        if relevant:
            facts.append(item)
            sentences.append(_sentence(item))
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
    return {'spoken_summary': ' '.join(sentences), 'ranges': facts, 'range_index': index,
            'young_lefty_evaluated': True, 'young_lefty_relevant': young_relevant,
            'coverage_complete': story.get('coverage', {}).get('complete', False),
            'through_ny': story['end_ny'], 'response_contract': SYNOPSIS_CONTRACT}


def build_other_ranges(review, asset=None, discussed=()):
    """Remaining evidence in hourly order, not just named or winning setups.

    This is an explicit follow-up view; the short default synopsis remains
    unchanged. A final closed range without observation bars is included with
    an explicit cutoff limit, never presented as another completed setup.
    """
    story = review['shift_story']
    excluded = set(discussed)
    records = story.get('recap', {}).get('paired_interpretation', [])
    synopsis = build_shift_synopsis(review, asset)
    facts = [deepcopy(row) for row in synopsis['ranges'] if row.get('play') == 'Young Lefty']
    for row in story.get('ranges', []):
        play = row.get('label') if row.get('label') in {'9ate8', 'Young Lefty'} else None
        facts.append(_paired_fact(row, records, _local_fact(row, play)))
    index = {row['anchor_start_ny']: row for row in synopsis['range_index']}
    remaining, sentences = [], []
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
            text = _sentence(fact, young=fact.get('play') == 'Young Lefty')
        remaining.append(fact)
        sentences.append(text)
    if not remaining:
        sentences.append('All retained ranges in this shift have already been discussed. '
                         'Name a range to revisit it, or explicitly start the review over.')
    return {'spoken_summary': ' '.join(sentences), 'ranges': remaining,
            'excluded_discussed_anchors': sorted(excluded),
            'all_ranges_discussed': not remaining,
            'through_ny': story['end_ny'], 'coverage_complete': story.get('coverage', {}).get('complete', False),
            'response_contract': OTHER_RANGES_CONTRACT}
