"""Small, named views of directional purge identities and their own sequels.

This module derives evidence only from a full CRT/shift range review. It neither
replays bars nor changes the authoritative lifecycle or directional outcome.
Shared scope applies to every identity/card; a later opposite purge cannot join
or replace the original directional attempt. All chronology is bar-bounded.
"""
from copy import deepcopy

from gbop_voice_web.candle_evidence import parse_time

VERSION = 'directional-candidate-evidence-2026-10-03'
MAX_CARDS = 32


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _interval(value):
    return _pick(value or {}, ('bar_open_ny', 'bar_close_ny', 'precision_seconds',
                               'exact_tick_time_known'))


def _time(value):
    try:
        return parse_time(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _source_relation(source, delivery):
    times = [_time((value or {}).get(key)) for value in (source, delivery)
             for key in ('bar_open_ny', 'bar_close_ny')]
    if any(value is None for value in times):
        return 'unverified_source_interval'
    start, end, target_start, target_end = times
    if start >= end or target_start >= target_end:
        return 'unverified_source_interval'
    if end <= target_start:
        return 'before_delivery'
    if start >= target_end:
        return 'after_delivery'
    if start == target_start and end == target_end:
        return 'same_source_bar_order_unknown'
    return 'overlapping_source_intervals_order_unknown'


def _formation_relation(identified_at, delivery):
    known = _time(identified_at)
    start, end = (_time((delivery or {}).get(key)) for key in ('bar_open_ny', 'bar_close_ny'))
    if known is None or start is None or end is None or start >= end:
        return 'unverified_formation_time'
    if known <= start:
        return 'before_delivery'
    if known >= end:
        return 'at_or_after_delivery_source_close'
    return 'within_delivery_source_interval_order_unknown'


def _objective(value):
    """Keep level, status and time/order, without duplicating range descriptions."""
    result = _pick(value or {}, ('level', 'status', 'relative_to_model1_invalidation',
        'same_purge_bar_order_unknown', 'same_formation_bar_touch_order_unknown'))
    evidence = (value or {}).get('evidence') or (value or {}).get('first_touch')
    if evidence:
        result['source_interval'] = _interval(evidence)
        if 'level' not in result and 'level' in evidence:
            result['level'] = evidence['level']
    return result


def _original_attempt(review):
    outcome = review.get('directional_outcome', {})
    anchor = review.get('anchor', {})
    direction = outcome.get('direction', review.get('direction_observed', review.get('observed_direction')))
    targets = {row['objective']: row for row in review.get('objectives', []) if 'objective' in row}
    result = {'direction': direction, 'status': outcome.get('status', 'unverified'),
              'initiating_candle_open_ny': (outcome.get('initiating_identity') or {}).get('bar_open_ny')}
    for name in ('midpoint', 'opposing_liquidity'):
        result[name] = _objective(outcome.get(name) or targets.get(name))
        result[name].setdefault('level', anchor.get('midpoint') if name == 'midpoint' else
                               anchor.get('low') if direction == 'bearish' else
                               anchor.get('high') if direction == 'bullish' else None)
    result['objective_side'] = 'sell' if direction == 'bearish' else 'buy' if direction == 'bullish' else None
    return result


def _identity(fact, original):
    kind = fact.get('purge_type')
    result = _pick(fact, ('purge_type', 'timeframe', 'bar_open_ny', 'bar_close_ny',
        'identified_at_ny', 'purged_side', 'purged_level', 'direction'))
    if fact.get('both_boundaries_pierced_in_same_candle'):
        result['both_boundaries_pierced_in_same_candle'] = True
    result['identity'] = 'Model 1 candle' if kind == 'body_soup' else 'Turtle Wick Soup'
    if fact.get('lifecycle_detail_available') is False:
        result['lifecycle_detail_available'] = False
    result['purge_source_interval'] = _interval(fact.get('purge_source_interval'))
    direction, original_direction = fact.get('direction'), original.get('direction')
    delivery = original.get('opposing_liquidity', {})
    delivered = (original.get('status') == 'opposing_liquidity_delivered'
                 and delivery.get('status') == 'observed_after_purge')
    if delivered:
        result['source_vs_original_delivery'] = _source_relation(
            fact.get('purge_source_interval'), delivery.get('source_interval'))
        result['formation_vs_original_delivery'] = _formation_relation(
            fact.get('identified_at_ny'), delivery.get('source_interval'))
    else:
        result['source_vs_original_delivery'] = 'original_delivery_not_established'
        result['formation_vs_original_delivery'] = 'original_delivery_not_established'
    if not original_direction or not direction:
        role = 'unverified_original_direction'
    elif direction != original_direction:
        role = 'separate_opposite_direction'
    elif result['source_vs_original_delivery'] == 'after_delivery':
        role = 'separate_same_direction_after_delivery'
    elif result['source_vs_original_delivery'] in (
            'same_source_bar_order_unknown', 'overlapping_source_intervals_order_unknown',
            'unverified_source_interval'):
        role = 'same_direction_attempt_order_unverified'
    elif fact.get('bar_open_ny') == original.get('initiating_candle_open_ny'):
        role = 'initiating_original_direction'
    else:
        role = 'same_as_original_direction'
    result['attempt_role'] = role
    return result


def candidate_lifecycle_card(fact):
    """Named, bounded summary of one identity; keep its full detail queryable.

    A wick identity never inherits a same-opening body candle's Model 1 sequel.
    Own objectives refer to this exact Model 1, not the selected parent range.
    """
    card = _pick(fact, ('bar_open_ny', 'purge_type', 'purged_side', 'lifecycle_detail_available'))
    if fact.get('purge_type') != 'body_soup':
        card['csd'] = {'status': 'not_a_model1_body_candle'}
        return card
    csd = fact.get('csd', {})
    card['csd'] = _pick(csd, ('status', 'reference_level', 'reference_boundary'))
    card['csd'].setdefault('status', 'not_assessed')
    if csd.get('rule'):
        card['csd']['rule'] = 'strict_full_extreme_close'
    if csd.get('evidence'):
        card['csd']['candle'] = _interval(csd['evidence'])
        card['csd']['confirmed_at_ny'] = csd['evidence'].get('confirmed_at_ny',
                                                               csd['evidence'].get('bar_close_ny'))
    soup = fact.get('super_soup', {})
    structure = fact.get('super_soup_structure', {})
    card['super_soup'] = {'pre_csd_status': soup.get('status', 'not_assessed')}
    card['super_soup'].update(_pick(structure, ('structure_status', 'structural_quality',
        'variants', 'structure_known_at_ny', 'csd_same_assigned_close',
        'local_crt_outcome', 'local_function_outcome', 'parent_function_outcome',
        'local_crt_invalidated_at_ny', 'local_function_window_end_ny')))
    if structure.get('event'):
        card['super_soup']['candle'] = _interval(structure['event'])
    if structure.get('purge_source_interval'):
        card['super_soup']['purge_source_interval'] = _interval(structure['purge_source_interval'])
    pre = soup.get('evidence') or {}
    # The first sweep and later return can be DIFFERENT assigned candles.
    if pre.get('return_candle'):
        card['super_soup']['return_candle'] = _interval(pre['return_candle'])
    if pre.get('purge') and not structure.get('event'):
        card['super_soup']['candle'] = _interval(pre['purge'])
    # Full detail retains validity-bounded and parent targets; the small card
    # needs only own physical targets plus the independent outcome axes above.
    for name in ('local_function_objectives',):
        if name in structure:
            card['super_soup'][name] = {key: _objective(value)
                                      for key, value in structure[name].items()}
    if 'sequence_gap_at_ny' in fact:
        card['sequence_gap_at_ny'] = fact['sequence_gap_at_ny']
    if fact.get('next_relation_detail_start_ny'):
        card['next_relation_detail_start_ny'] = fact['next_relation_detail_start_ny']
    return card


def directional_candidate_evidence(review, asset=None, *, max_cards=1,
                                   include_later_wicks=False, focus_open_ny=None):
    """Return reusable scope, attempt, named identity index and lifecycle cards.

    By default retain every available Model 1 and the earliest wick in each
    direction. Later wicks are explicitly omitted, not classified as absent.
    Set include_later_wicks=True for the entire already bounded backend index.
    A focus always retains all identities with that opening, including dual-side
    candles. max_cards bounds lifecycle detail only, never Model 1 identities.
    Callers can construct detail_candle_start_ny requests from indexed openings;
    first_omitted_available_candle_open_ny is also an actual available identity.
    backend_remaining_from_ny is a backend cap, NOT an executable page cursor.
    """
    if not isinstance(max_cards, int) or not 0 <= max_cards <= MAX_CARDS:
        raise ValueError(f'max_cards must be an integer from 0 to {MAX_CARDS}.')
    anchor, lifecycle = review.get('anchor', {}), review.get('candle_lifecycle', {})
    coverage = review.get('range_observation_coverage', review.get('observation_coverage', {}))
    original = _original_attempt(review)
    scope = {'asset': asset or review.get('asset'),
             'anchor_start_ny': anchor.get('start_ny', review.get('anchor_start_ny')),
             'anchor_end_ny': anchor.get('end_ny'),
             'anchor_timeframe': review.get('anchor_timeframe', anchor.get('timeframe')),
             'assigned_timeframe': review.get('assigned_timeframe', lifecycle.get('assigned_timeframe')),
             'high': anchor.get('high'), 'midpoint': anchor.get('midpoint'), 'low': anchor.get('low'),
             'through_ny': review.get('observation_coverage', {}).get('end_ny',
                 coverage.get('end_ny', lifecycle.get('window_end_ny'))),
             'range_invalidated_at_ny': review.get('invalidated_at_ny', lifecycle.get('range_invalidated_at_ny'))}
    if coverage.get('end_ny') != scope['through_ny']:
        scope['range_observation_end_ny'] = coverage.get('end_ny')
    facts = [fact for fact in lifecycle.get('purge_candles', [])
             if fact.get('purge_type') in ('body_soup', 'wick_soup')]
    identity_key = lambda fact: (fact.get('bar_open_ny'), fact.get('purged_side'), fact.get('purge_type'))
    available = {identity_key(fact) for fact in facts}
    # Model 1 and lifecycle have independent backend caps. A lifecycle cap may
    # precede a later body identity already supplied by the Model 1 index.
    for body in review.get('model1', {}).get('candles', []):
        if body.get('body_cross_and_close_through_level') is not True:
            continue
        fact = {**body, 'purge_type': 'body_soup', 'lifecycle_detail_available': False}
        if identity_key(fact) not in available:
            facts.append(fact)
            available.add(identity_key(fact))
    facts.sort(key=lambda fact: (_time(fact.get('bar_open_ny')) or 0,
                                fact.get('purged_side', ''), fact['purge_type']))
    focused_time = _time(focus_open_ny) if focus_open_ny else None
    def focused(fact):
        return focused_time is not None and _time(fact.get('bar_open_ny')) == focused_time
    retained, omitted, wick_directions = [], [], set()
    for fact in facts:
        is_wick = fact['purge_type'] == 'wick_soup'
        keep = (not is_wick or include_later_wicks or focused(fact)
                or fact.get('direction') not in wick_directions)
        (retained if keep else omitted).append(fact)
        if is_wick:
            wick_directions.add(fact.get('direction'))
    if focus_open_ny:
        card_facts = [fact for fact in retained if focused(fact)]
    else:
        bodies = [fact for fact in retained if fact['purge_type'] == 'body_soup']
        card_facts = ([fact for fact in bodies if fact.get('direction') == original['direction']]
                      + [fact for fact in bodies if fact.get('direction') != original['direction']])
    selected_cards = card_facts[:max_cards]
    return {'version': VERSION, 'scope': scope, 'original_attempt': original,
            'identity_index': [_identity(fact, original) for fact in retained],
            'candidate_cards': [candidate_lifecycle_card(fact) for fact in selected_cards],
            'coverage': {'anchor_complete': anchor.get('complete'),
                'observation_complete': lifecycle.get('observation_complete', coverage.get('complete')),
                'range_observation_complete': coverage.get('complete'),
                **_pick(coverage, ('missing_bar_count', 'source_resolution_seconds')),
                'lifecycle_status': lifecycle.get('status', 'not_assessed'),
                'backend_identified_count': lifecycle.get('identified_count', len(facts)),
                'available_identity_count': len(facts), 'indexed_identity_count': len(retained),
                'omitted_available_identity_count': len(omitted),
                'first_omitted_available_candle_open_ny': omitted[0]['bar_open_ny'] if omitted else None,
                'backend_remaining_from_ny': lifecycle.get('next_identity_open_ny'),
                'backend_remaining_model1_from_ny': review.get('model1', {}).get('next_candle_start_ny'),
                'omitted_lifecycle_card_count': len(facts) - len(selected_cards)},
            'contract': 'Scope applies to every identity. Model 1 means body purge; wick is Turtle Wick Soup. '
                'Opposite direction is a separate attempt. Source order and closed-candle formation are separate. '
                'Own objectives belong to the named Model 1; later delivery never restores CRT validity. '
                'Omitted/gapped/capped detail is unverified, not absent; backend cap is not a page cursor. '
                'No tick order or member execution is inferred.'}
