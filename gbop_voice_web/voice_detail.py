"""One precise CRT lifecycle page; keep the original anchor and review cutoff."""
from copy import deepcopy
import json

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.voice_payload import _bounded_error, _factor_review, _paired, _pick, _voice_market_context
from gbop_voice_web.directional_evidence import directional_candidate_evidence

DETAIL_CHARACTER_BUDGET = 32000
DETAIL_COMPACTION_TARGET_CHARS = 31000


def _measurement(value):
    """Keep target identity and both explicitly named percentage denominators."""
    return _pick(value, ('target', 'gap_price_points', 'touch_relation',
        'full_range_reference', 'boundary_to_target_reference',
        'inducement_classification', 'numeric_inducement_threshold'))


def _approach(value):
    """Keep measured distance/order evidence without duplicate delivery ratios."""
    out = {key: deepcopy(child) for key, child in value.items()
           if key not in ('response_contract', 'window_rule', 'objectives')}
    out['objectives'] = {}
    for name, target in value.get('objectives', {}).items():
        fact = _pick(target, ('level', 'spoken_label', 'status', 'distance_price_points',
            'observed_distance_price_points', 'closest_observed_price', 'closest_source_interval',
            'first_touch_order_verified', 'boundary_observations', 'gtop_context'))
        # A verified delivered target already carries its zero gap and exact
        # source evidence. New percentage fields add no proximity information.
        if target.get('target_approach') and target.get('distance_price_points') != 0:
            fact['target_approach'] = _measurement(target['target_approach'])
        if 'coverage_through_touch' in target:
            fact['coverage_through_touch_complete'] = target['coverage_through_touch'].get('complete')
        out['objectives'][name] = fact
    return out


def _double_purge(value, original):
    """Separate the reversal evidence while sharing the original delivery."""
    out = {key: deepcopy(child) for key, child in value.items()
           if key not in ('response_contract', 'spoken_summary')}
    prior = out.get('original_outcome')
    if prior:
        comparable = deepcopy(prior)
        prefixes = {}
        for name in ('midpoint', 'opposing_liquidity'):
            if comparable.get(name, {}).get('coverage_through_touch') is not None:
                prefixes[name] = comparable[name].pop('coverage_through_touch')
        if all(original.get(k) == v for k, v in comparable.items()
               if k not in ('response_contract', 'spoken_summary')):
            out['original_outcome'] = {'same_evidence_as': '#/review/directional_outcome'}
            if prefixes:
                out['original_coverage_through_touch'] = prefixes
        else:
            out['original_outcome'] = _pick(prior, ('direction', 'status',
                'range_invalidated_at_ny', 'delivery_before_later_invalidation'))
            for name in ('midpoint', 'opposing_liquidity'):
                if name in prior:
                    out['original_outcome'][name] = _pick(prior[name],
                        ('status', 'spoken_label', 'evidence', 'coverage_through_touch'))
    thesis = out.get('reversal_thesis', {})
    thesis.pop('window_rule', None)
    for target in thesis.get('objectives', {}).values():
        if target.get('approach'):
            measured = _measurement(target['approach'])
            # Named identity is already on the objective, immediately above.
            measured.pop('target', None)
            target['approach'] = measured
    return out


def _double_purge_summary(value, request):
    """Bound a secondary reversal; the opposite identity page has full detail."""
    out = _pick(value, ('status', 'observed', 'scope', 'range_start_ny', 'range_timeframe',
        'review_cutoff_ny', 'validity_cutoff_ny', 'range_invalidated_at_ny',
        'source_resolution_seconds', 'exact_tick_time_known', 'original_outcome',
        'original_completion_preserved', 'original_first_purged_side', 'reverse_direction',
        'original_coverage_through_touch'))
    for key in ('range_start_ny', 'range_timeframe', 'review_cutoff_ny',
                'validity_cutoff_ny', 'range_invalidated_at_ny', 'reverse_direction'):
        out.pop(key, None)
    out['scope_note'] = 'Selected review anchor/cutoff; reversal validity and direction below.'
    out['sequence'] = _pick(value.get('sequence', {}), ('source_return_inside',
        'assigned_return_inside', 'known_at_ny', 'coverage', 'coverage_through_return'))
    for key in ('coverage', 'coverage_through_return'):
        if key in out['sequence']:
            # Both begin at the selected range end. The enclosing selected
            # cutoff and source-return known-at provide their respective ends.
            out['sequence'][key] = _pick(out['sequence'][key], ('complete', 'missing_bar_count'))
    if 'original_coverage_through_touch' in out:
        out['original_coverage_through_touch'] = {name: _pick(prefix,
            ('complete', 'missing_bar_count'))
            for name, prefix in out['original_coverage_through_touch'].items()}
    prior = out.get('original_outcome', {})
    if 'same_evidence_as' not in prior:
        out['original_outcome'] = _pick(prior, ('direction', 'status', 'range_invalidated_at_ny'))
        for name in ('midpoint', 'opposing_liquidity'):
            if name in prior:
                out['original_outcome'][name] = _pick(prior[name], ('status',))
                if 'coverage_through_touch' in prior[name]:
                    out['original_outcome'][name]['coverage_through_touch'] = _pick(
                        prior[name]['coverage_through_touch'], ('complete', 'missing_bar_count'))
    thesis = value.get('reversal_thesis', {})
    out['reversal_thesis'] = _pick(thesis, ('direction', 'status', 'objective_side',
        'objective_level', 'objective_basis', 'midpoint_level', 'midpoint_role',
        'window_start_ny', 'validity_cutoff_ny', 'coverage',
        'earlier_delivery_preserved_after_invalidation'))
    if 'coverage' in out['reversal_thesis']:
        coverage = thesis['coverage']
        bounded = _pick(coverage, ('complete', 'missing_bar_count'))
        for key, scope_key in (('start_ny', 'window_start_ny'), ('end_ny', 'validity_cutoff_ny')):
            if coverage.get(key) != thesis.get(scope_key):
                bounded[key] = coverage.get(key)
        out['reversal_thesis']['coverage'] = bounded
    if 'objectives' in thesis:
        targets = out['reversal_thesis']['objectives'] = {}
        for name, target in thesis['objectives'].items():
            fact = _pick(target, ('level', 'spoken_label', 'status', 'distance_price_points',
                'observed_distance_price_points', 'closest_observed_price',
                'closest_source_interval', 'coverage_through_touch', 'gtop_context'))
            if fact.get('closest_source_interval'):
                fact['closest_source_interval'] = _pick(fact['closest_source_interval'],
                    ('bar_open_ny', 'bar_close_ny', 'precision_seconds'))
            if fact.get('distance_price_points') == fact.get('observed_distance_price_points'):
                fact.pop('observed_distance_price_points', None)
            if target.get('approach'):
                measured = target['approach']
                fact['approach'] = {
                    'full_range_reference': _pick(measured.get('full_range_reference', {}),
                        ('denominator_price_points', 'gap_percent')),
                    'boundary_to_target_reference': _pick(measured.get('boundary_to_target_reference', {}),
                        ('denominator_price_points', 'remaining_gap_percent', 'progress_percent'))}
                out['proximity_policy'] = _pick(measured,
                    ('numeric_inducement_threshold', 'inducement_classification'))
            # Preserve actual boundary uncertainty, even when its price rows
            # move to the opposite identity page.
            if target.get('boundary_observations'):
                fact['boundary_order_verified'] = all(
                    row.get('post_return_before_invalidation_order_known') is True
                    for row in target['boundary_observations'])
            targets[name] = fact
    out['opposite_identity_count'] = len(value.get('opposite_identities', []))
    out['detail_omissions'] = ('Identity sequence/boundary rows omitted, not absent or ordered; use double_purge_detail_request.')
    out['double_purge_detail_request'] = deepcopy(request)
    out['double_purge_detail_request']['args'] = {k: v for k, v in request['args'].items()
        if v is not None or k == 'detail_from_ny'}
    return out


def _factor_intervals(out):
    """Share short repeated source intervals with short, resolvable pointers."""
    from collections import Counter
    counts, examples = Counter(), {}
    def scan(value):
        if isinstance(value, dict):
            if ('bar_open_ny' in value and 'bar_close_ny' in value
                    and 'same_evidence_as' not in value):
                encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
                counts[encoded] += 1
                examples[encoded] = value
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)
    scan(out['review'])
    pool, refs = [], {}
    for encoded, count in counts.items():
        ref = {'same_evidence_as': '#/review/shared_intervals/' + str(len(pool))}
        if count > 1 and (len(encoded) * (count - 1) >
                          len(json.dumps(ref, separators=(',', ':'))) * count + 2):
            refs[encoded] = ref
            pool.append(deepcopy(examples[encoded]))
    def replace(value):
        if isinstance(value, dict):
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
            if encoded in refs:
                return deepcopy(refs[encoded])
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value
    if pool:
        candidate = replace(out['review'])
        candidate['shared_intervals'] = pool
        if len(json.dumps(candidate, separators=(',', ':'))) < len(json.dumps(out['review'], separators=(',', ':'))):
            out['review'] = candidate


def crt_voice_detail(result):
    compact = compact_voice_tool_result('review_market_crt', result)
    review = compact['review']
    selection = compact.get('voice_detail_selection', {})
    focus = selection.get('detail_candle_start_ny')
    cursor = selection.get('detail_from_ny')
    lifecycle = review.get('candle_lifecycle', {})
    facts = lifecycle.get('purge_candles', [])
    anchor = review.get('anchor', {})
    cutoff = selection.get('through_ny') or review.get('observation_coverage', {}).get('end_ny')
    base_args = {
        'asset': compact.get('asset'), 'context_action': 'continue', 'anchor_start_ny': anchor.get('start_ny'),
        'anchor_timeframe': review.get('anchor_timeframe'), 'through_ny': cutoff,
        'confirmation_timeframe': selection.get('confirmation_timeframe'),
        'blessed_thief_timeframe': selection.get('blessed_thief_timeframe'),
        'blessed_thief_from_ny': selection.get('blessed_thief_from_ny'),
        'detail_candle_start_ny': None, 'detail_from_ny': None}
    candidates = directional_candidate_evidence(review, compact.get('asset'), max_cards=0,
                                                include_later_wicks=True, focus_open_ny=focus)
    index = [_pick(fact, ('identity', 'purge_type', 'timeframe', 'bar_open_ny',
        'bar_close_ny', 'purged_side', 'direction', 'attempt_role'))
        for fact in candidates['identity_index']]
    def error(status, message):
        return _bounded_error({'ok': False, 'status': status, 'asset': compact.get('asset'),
            **({'market_context': _voice_market_context(compact['market_context'])} if 'market_context' in compact else {}),
            'anchor_start_ny': anchor.get('start_ny'), 'through_ny': cutoff,
            'identity_index': index, 'message': message,
            'detail_request': {'tool': 'review_market_crt', 'args': {
                **base_args, 'detail_candle_start_ny': focus, 'detail_from_ny': cursor}},
            'backend_remaining_from_ny': lifecycle.get('next_identity_open_ny')}, DETAIL_CHARACTER_BUDGET)
    try:
        if focus and cursor:
            return error('ambiguous_detail_selection', 'Choose one exact candle or one page cursor, not both.')
        selected = None
        if focus:
            selected = next((f['bar_open_ny'] for f in facts
                             if parse_time(f['bar_open_ny']) == parse_time(focus)), None)
            if selected is None:
                return error('detail_identity_not_in_available_page',
                    'The exact requested candle identity is not in the available lifecycle records. '
                    'No substitute candle was selected. Missing or backend-capped evidence is not proof of absence.')
        elif cursor:
            selected = next((f['bar_open_ny'] for f in facts
                             if parse_time(f['bar_open_ny']) >= parse_time(cursor)), None)
            if selected is None:
                return error('detail_page_exhausted',
                    'No further identity is available in this bounded backend result. '
                    'If backend_remaining_from_ny is set, later lifecycle records are not exposed by this query; '
                    'do not repeat this cursor or infer no later setup.')
        elif facts:
            selected = facts[0]['bar_open_ny']
    except (TypeError, ValueError, OverflowError):
        return error('invalid_detail_selection', 'Use an exact ISO New York assigned-candle opening or explicit offset.')
    page = [fact for fact in facts if fact['bar_open_ny'] == selected]
    following = next((fact['bar_open_ny'] for fact in facts
                      if selected and parse_time(fact['bar_open_ny']) > parse_time(selected)), None)
    out = {key: deepcopy(value) for key, value in compact.items()
           if key not in ('review', 'voice_detail_selection')}
    if 'focused_evidence_request' in out:
        # The successful page already pins exact scope, selection and cursors.
        # Keep intent without repeating the full null-filled request envelope.
        out['focused_evidence_request'] = _pick(out['focused_evidence_request'],
                                               ('tool', 'query_purpose', 'status'))
    if 'market_context' in out:
        out['market_context'] = _voice_market_context(out['market_context'], evidence_ref='#/review')
    out['review'] = {key: deepcopy(value) for key, value in review.items() if key not in (
        'model1', 'assigned_candles', 'candle_lifecycle', 'paired_smt', 'recap', 'blessed_thief')}
    view = out['review']
    view['selected_directional_identities'] = [deepcopy(fact) for fact in candidates['identity_index']
                                              if fact['bar_open_ny'] == selected]
    if view.get('objective_approach'):
        view['objective_approach'] = _approach(view['objective_approach'])
    if view.get('double_purge'):
        view['double_purge'] = _double_purge(view['double_purge'], review.get('directional_outcome', {}))
    if view.get('range_observation_coverage') == view.get('observation_coverage'):
        view['range_observation_coverage'] = {'same_evidence_as': '#/review/observation_coverage'}
    view['model1'] = _pick(review.get('model1', {}), ('status', 'assigned_timeframe',
        'identified_count', 'next_candle_start_ny', 'observation_complete',
        'forming_assigned_candle', 'incomplete_assigned_candles', 'window_start_ny', 'window_end_ny'))
    # Selected identity retains exact OHLC/body qualification as well as the
    # independent full lifecycle card. All other identities remain addressable.
    view['model1']['candles'] = [deepcopy(candle) for candle in review.get('model1', {}).get('candles', [])
                                if candle.get('bar_open_ny') == selected]
    view['candle_lifecycle'] = {key: deepcopy(value) for key, value in lifecycle.items()
        if key not in ('purge_candles', 'spoken_summary', 'performance_summary')}
    view['candle_lifecycle']['purge_candles'] = deepcopy(page)
    view['candle_lifecycle']['identity_index'] = index
    if len(page) == 1 and page[0].get('super_soup_structure', {}).get('performance_summary'):
        view['candle_lifecycle']['performance_summary'] = page[0]['super_soup_structure']['performance_summary']
    if review.get('paired_smt') is not None:
        view['paired_smt'] = _paired(review['paired_smt'], compact.get('asset'))
    if review.get('recap'):
        view['recap'] = _pick(review['recap'], ('headline', 'spoken_summary', 'evidence_precedence'))
    bt = review.get('blessed_thief', {})
    view['blessed_thief'] = {k: deepcopy(v) for k,v in bt.items() if k not in ('candles', 'response_contract')}
    bt_rows = bt.get('candles', [])
    view['blessed_thief']['candles'] = deepcopy(bt_rows[:1])
    if len(bt_rows) > 1:
        view['blessed_thief']['next_candle_start_ny'] = bt_rows[1]['bar_open_ny']
        view['blessed_thief']['voice_page_partial'] = True
    next_request = {'tool': 'review_market_crt', 'args': {**base_args, 'detail_from_ny': following}} if following else None
    out['voice_detail_page'] = {
        'selected_candle_start_ny': selected, 'focus_requested': focus is not None,
        'returned_identity_count': len(page), 'available_identity_count': len(facts),
        'next_request': next_request, 'backend_remaining_from_ny': lifecycle.get('next_identity_open_ny'),
        'raw_candle_request': {'tool': 'inspect_market_candles', 'args': {
            'asset': compact.get('asset'), 'context_action': 'continue',
            'start_ny': selected or anchor.get('end_ny'), 'end_ny': cutoff,
            'timeframe': review.get('assigned_timeframe') or review.get('anchor_timeframe')}},
        'character_budget': DETAIL_CHARACTER_BUDGET,
        'note': 'Exact candle page; own and parent targets/directions remain separate. '
            'same_evidence_as resolves in this payload. Fetch needed identities with detail_candle_start_ny '
            'or next_request; preserve scope/cutoff. backend_remaining_from_ny marks unavailable deeper records, '
            'not a page cursor. Raw/BT cursors are separate. Missing detail proves no absence; '
            'distances are source-bar price points, not tick order or fills.'}
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_COMPACTION_TARGET_CHARS:
        # Repeated pair narrative is not the requested lifecycle evidence.
        view.pop('recap', None)
        view.pop('limits', None)
        # Scope, target, direction and ordering fields remain authoritative;
        # their repeated policy prose is already stated by the page contract.
        view.get('directional_outcome', {}).pop('response_contract', None)
        view['blessed_thief'] = _pick(bt, ('status', 'timeframe', 'window_end_ny', 'next_candle_start_ny'))
        view['blessed_thief']['detail_omitted'] = True
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_COMPACTION_TARGET_CHARS:
        # Repeated coverage extrema are not lifecycle evidence. Keep exact
        # anchor OHLC/first extremes and all gaps/precision, not last occurrences.
        for key in ('high_last_seen', 'low_last_seen', 'high_occurrences', 'low_occurrences'):
            view['anchor'].pop(key, None)
        for key in ('observation_coverage', 'range_observation_coverage'):
            if key in view and 'same_evidence_as' not in view[key]:
                view[key] = _pick(view[key], ('start_ny', 'end_ny', 'complete',
                    'source_resolution_seconds', 'bar_count', 'missing_bar_count', 'coverage_note'))
        out['voice_detail_page']['coverage_extrema_omitted'] = (
            'Repeated extrema omitted; OHLC/first extremes, gaps and lifecycle times retained.')
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_COMPACTION_TARGET_CHARS:
        # Rejected paired theses are not the selected candle's lifecycle.
        # Potential/qualified boneless evidence remains complete. Explicit
        # paired investigations have their own unmodified review tool.
        if review.get('paired_smt') is not None:
            view['paired_smt'] = _paired(review['paired_smt'], compact.get('asset'), overview=True)
            peer = next((asset for asset in review['paired_smt'].get('assets', [])
                         if asset != compact.get('asset')), None)
            if peer:
                out['voice_detail_page']['paired_detail_request'] = {
                    'tool': 'review_market_smt', 'args': {
                        'asset': compact.get('asset'), 'comparison_asset': peer,
                        'context_action': 'continue', 'anchor_start_ny': anchor.get('start_ny'),
                        'anchor_timeframe': review.get('anchor_timeframe'), 'through_ny': cutoff}}
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_COMPACTION_TARGET_CHARS:
        # Full lifecycle cards remain untouched. Secondary reversal details
        # have the same selected range/cutoff and an exact opposite-candle page.
        double = view.get('double_purge', {})
        opposite = double.get('opposite_identities', [])
        detail_identity = next((identity for identity in opposite
                                if identity.get('purge_type') == 'wick_soup'),
                               opposite[0] if opposite else {})
        first_opposite = detail_identity.get('bar_open_ny')
        if first_opposite and first_opposite != selected:
            request = {'tool': 'review_market_crt', 'args': {
                **base_args, 'detail_candle_start_ny': first_opposite}}
            view['double_purge'] = _double_purge_summary(double, request)
        view['candle_lifecycle'].pop('response_contract', None)
        approach = view.get('objective_approach', {})
        if approach.get('coverage'):
            approach['coverage'] = _pick(approach['coverage'],
                ('start_ny', 'end_ny', 'complete', 'missing_bar_count'))
        for target in approach.get('objectives', {}).values():
            if target.get('distance_price_points') == target.get('observed_distance_price_points'):
                target.pop('observed_distance_price_points', None)
            for boundary in target.get('boundary_observations', []):
                # The exact target level and measured gap already identify
                # this redundant price; boundary order and interval remain.
                if boundary.get('distance_price_points', 0) > 0:
                    boundary.pop('observed_price', None)
        # Identity names retain the body/wick distinction in this index.
        for identity in view['candle_lifecycle']['identity_index']:
            identity.pop('purge_type', None)
        pair = view.get('paired_smt', {})
        events = pair.get('events', [])
        if events and all(e.get('boneless_status') == 'disqualified'
                          and e.get('recap_eligible') is False for e in events):
            pair['events'] = [_pick(e, ('direction', 'boneless_status', 'setup_interval',
                                       'scope', 'recap_eligible')) for e in events]
            for event in pair['events']:
                event['setup_interval'] = _pick(event['setup_interval'], ('start_ny', 'end_ny',
                    'complete', 'qualified_smt', 'potential_smt', 'status'))
            pair['detail_omitted'] = 'Disqualified pair details; use paired_detail_request.'
            # Status and exact scope remain explicit; other pair fields are
            # independently addressable via the canonical paired request.
            kept_pair = _pick(pair, ('ok', 'status', 'assets', 'anchor_start_ny',
                'anchor_end_ny', 'anchor_timeframe', 'through_ny',
                'paired_coverage_complete', 'recap_eligible', 'events', 'detail_omitted'))
            pair.clear()
            pair.update(kept_pair)
        for identity in view['selected_directional_identities']:
            match = next((i for i, card in enumerate(page)
                          if card.get('bar_open_ny') == identity.get('bar_open_ny')
                          and card.get('purged_side') == identity.get('purged_side')), None)
            if match is not None:
                kept = _pick(identity, ('identity', 'bar_open_ny', 'timeframe', 'direction',
                    'attempt_role', 'source_vs_original_delivery', 'formation_vs_original_delivery'))
                kept['lifecycle_evidence'] = {'same_evidence_as':
                    '#/review/candle_lifecycle/purge_candles/' + str(match)}
                identity.clear()
                identity.update(kept)
        # Body classification remains on the selected Model 1; its identical
        # OHLC, targets and source interval are on the exact lifecycle card.
        for candle in view['model1']['candles']:
            match = next((i for i, card in enumerate(page)
                          if card.get('bar_open_ny') == candle.get('bar_open_ny')
                          and card.get('purged_side') == candle.get('purged_side')), None)
            if match is not None:
                kept = _pick(candle, ('bar_open_ny', 'body_cross_and_close_through_level'))
                kept['lifecycle_evidence'] = {'same_evidence_as':
                    '#/review/candle_lifecycle/purge_candles/' + str(match)}
                candle.clear()
                candle.update(kept)
        out['voice_detail_page']['note'] = (
            'Exact lifecycle; same_evidence_as resolves here. Keep scope/cutoff and targets separate. '
            'Fetch identities with detail_candle_start_ny or next_request. backend_remaining_from_ny '
            'marks unavailable deeper records, not a cursor. Raw/BT cursors are separate. '
            'Missing detail proves no absence; source bars do not prove tick order or fills.')
        _factor_review(out)
        _factor_intervals(out)
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
        failed = error('voice_detail_budget_exceeded',
            'This identity has more evidence than fits one voice page; its details were not sent. '
            'Request the named raw candle interval or a narrower review cutoff; do not infer missing lifecycle outcomes.')
        failed['raw_candle_request'] = out['voice_detail_page']['raw_candle_request']
        return _bounded_error(failed, DETAIL_CHARACTER_BUDGET)
    return out
