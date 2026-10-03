"""One precise CRT lifecycle page; keep the original anchor and review cutoff."""
from copy import deepcopy
import json

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.voice_payload import _bounded_error, _factor_review, _paired, _pick, _voice_market_context
from gbop_voice_web.directional_evidence import directional_candidate_evidence

DETAIL_CHARACTER_BUDGET = 32000


def _approach(value):
    """Keep every measured distance/order caveat without duplicate labels."""
    out = {key: deepcopy(child) for key, child in value.items()
           if key not in ('response_contract', 'window_rule', 'objectives')}
    out['objectives'] = {name: _pick(target, (
        'level', 'status', 'distance_price_points', 'observed_distance_price_points',
        'closest_observed_price', 'closest_source_interval', 'first_touch_order_verified',
        'boundary_observations')) for name, target in value.get('objectives', {}).items()}
    for name, target in value.get('objectives', {}).items():
        if 'coverage_through_touch' in target:
            out['objectives'][name]['coverage_through_touch_complete'] = target['coverage_through_touch'].get('complete')
    return out


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
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
        # Repeated pair narrative is not the requested lifecycle evidence.
        view.pop('recap', None)
        view.pop('limits', None)
        # Scope, target, direction and ordering fields remain authoritative;
        # their repeated policy prose is already stated by the page contract.
        view.get('directional_outcome', {}).pop('response_contract', None)
        view['blessed_thief'] = _pick(bt, ('status', 'timeframe', 'window_end_ny', 'next_candle_start_ny'))
        view['blessed_thief']['detail_omitted'] = True
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
        # Repeated coverage extrema are not lifecycle evidence. Keep exact
        # anchor OHLC/first extremes and all gaps/precision, not last occurrences.
        for key in ('high_last_seen', 'low_last_seen', 'high_occurrences', 'low_occurrences'):
            view['anchor'].pop(key, None)
        for key in ('observation_coverage', 'range_observation_coverage'):
            if key in view and 'same_evidence_as' not in view[key]:
                view[key] = _pick(view[key], ('start_ny', 'end_ny', 'complete',
                    'source_resolution_seconds', 'bar_count', 'missing_bar_count', 'coverage_note'))
        out['voice_detail_page']['coverage_extrema_omitted'] = (
            'Repeated extrema omitted; anchor OHLC/first extremes, source gaps and lifecycle times retained.')
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
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
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
        _factor_review(out)
    if len(json.dumps(out, separators=(',', ':'))) > DETAIL_CHARACTER_BUDGET:
        failed = error('voice_detail_budget_exceeded',
            'This identity has more evidence than fits one voice page; its details were not sent. '
            'Request the named raw candle interval or a narrower review cutoff; do not infer missing lifecycle outcomes.')
        failed['raw_candle_request'] = out['voice_detail_page']['raw_candle_request']
        return _bounded_error(failed, DETAIL_CHARACTER_BUDGET)
    return out
