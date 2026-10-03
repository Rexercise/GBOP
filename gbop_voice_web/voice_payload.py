"""Bounded market overviews for voice; authoritative detailed tools stay intact.

The overview is a purpose-built view, never an arbitrary cut of serialized JSON.
Every range and identified candle is named. Deeper OHLC/lifecycle evidence is
explicitly queryable by the precise original asset, anchor and cutoff.
"""
from copy import deepcopy
import json

from gbop_voice_web.voice_runtime import compact_voice_tool_result

SHIFT_OVERVIEW_TARGET_CHARS = 32000


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _interval(value):
    return _pick(value or {}, ('bar_open_ny', 'bar_close_ny', 'precision_seconds',
                              'exact_tick_time_known', 'confirmed_at_ny'))


def _objective(value):
    out = _pick(value, ('objective', 'level', 'status', 'spoken_label', 'scope',
                       'relative_to_model1_invalidation',
                       'same_purge_bar_order_unknown', 'same_formation_bar_touch_order_unknown'))
    for key in ('evidence', 'first_touch'):
        if key in value:
            out[key] = _interval(value[key]) if value[key] else None
    for key in ('touch_bar_open_ny', 'touch_bar_close_ny', 'range_invalidated_at_ny'):
        if key in value:
            out[key] = value[key]
    return out


def _identity(value):
    return _pick(value, ('identity', 'purge_type', 'timeframe', 'bar_open_ny',
                        'bar_close_ny', 'identified_at_ny', 'purged_side',
                        'purged_level', 'direction', 'purge_source_interval',
                        'source_resolution_seconds', 'exact_tick_time_known'))


def _lifecycle(fact):
    csd = fact.get('csd', {})
    soup = fact.get('super_soup_structure') or {}
    targets = soup.get('local_function_objectives', {})
    def target(name):
        value = targets.get(name, {})
        evidence = value.get('evidence') or {}
        return [value.get('level'), value.get('status'), evidence.get('bar_open_ny'),
                evidence.get('bar_close_ny'), value.get('relative_to_model1_invalidation'),
                value.get('same_purge_bar_order_unknown')]
    return [fact['bar_open_ny'], csd.get('status'), csd.get('reference_level'),
        (csd.get('evidence') or {}).get('bar_close_ny'), (csd.get('evidence') or {}).get('bar_open_ny'), soup.get('structure_status'),
        soup.get('structural_quality'), soup.get('variants'), soup.get('structure_known_at_ny'),
        soup.get('local_crt_outcome'), soup.get('local_crt_invalidated_at_ny'),
        soup.get('local_function_outcome'), soup.get('parent_function_outcome'),
        target('midpoint'), target('opposing_liquidity'),
        fact.get('super_soup', {}).get('status'), soup.get('csd_same_assigned_close')]


OUTCOME_COLUMNS = ['model1_bar_open_ny', 'csd_status', 'csd_body_reference_level',
    'csd_confirmed_at_ny', 'csd_candle_open_ny', 'soup_structure_status', 'soup_structural_quality',
    'soup_variants', 'soup_known_at_ny', 'valid_local_crt_outcome',
    'local_crt_invalidated_at_ny', 'physical_local_function_outcome',
    'parent_range_function_outcome', 'own_midpoint', 'own_opposing_liquidity',
    'pre_csd_soup_status', 'csd_same_assigned_close']
TARGET_COLUMNS = ['level', 'status', 'touch_bar_open_ny', 'touch_bar_close_ny',
                  'relative_to_model1_invalidation', 'same_purge_bar_order_unknown']


def _paired_event(event, asset):
    out = _pick(event, ('direction', 'side', 'bar_open_ny', 'bar_close_ny',
        'precision_seconds', 'exact_tick_time_known', 'swept_asset', 'nonconfirming_asset',
        'boneless_asset', 'local_purge_inferred_for_boneless_asset', 'entry_confirmed',
        'play_context', 'historical_event_only', 'setup_interval', 'recap_eligible'))
    out['scope'] = {k: deepcopy(v) for k, v in event.get('scope', {}).items() if k != 'response_contract'}
    out['objective_status'] = {a: {key: _pick(target, ('level', 'status', 'touch_bar_open_ny', 'touch_bar_close_ny', 'range_invalidated_at_ny')) for key, target in targets.items()}
                             for a, targets in event.get('objective_status', {}).items() if a == asset}
    model = event.get('paired_model1', {})
    out['paired_model1'] = _pick(model, ('status', 'assigned_timeframe', 'purge_asset',
        'boneless_asset', 'local_body_purge_inferred', 'csd_status', 'execution_status', 'smt_qualified_at_ny'))
    for key in ('origin_model1', 'boneless_reference'):
        if model.get(key):
            out['paired_model1'][key] = _pick(model[key], ('identity', 'timeframe', 'bar_open_ny', 'bar_close_ny', 'identity_basis', 'asset', 'local_purge_observed', 'purge_context_supplied_by', 'csd_status', 'execution_status', 'smt_qualified_at_ny'))
    return out


def _paired(pair, asset):
    out = _pick(pair, ('ok', 'status', 'assets', 'anchor_start_ny', 'anchor_end_ny',
        'anchor_timeframe', 'through_ny', 'divergence_window_end_ny', 'divergence_confirmed',
        'paired_coverage_complete', 'paired_through_ny', 'execution_candles',
        'invalidating_closes_ny', 'same_bar_both_swept_sides', 'recap_eligible'))
    out['events'] = [_paired_event(event, asset) for event in pair.get('events', [])]
    return out


def shift_voice_overview(result):
    review = result['review']
    story = review['shift_story']
    asset = result.get('asset')
    end = story.get('end_ny')
    out = {key: deepcopy(value) for key, value in result.items() if key != 'review'}
    recap = review.get('shift_recap', story.get('recap', {}))
    view = _pick(review, ('date_ny', 'shift', 'timezone', 'source_resolution_seconds', 'limits'))
    view['shift_recap'] = _pick(recap, ('headline', 'spoken_summary', 'closing', 'evidence_precedence', 'hourly_crt_summary', 'range_summaries'))
    view['shift_story'] = _pick(story, ('start_ny', 'end_ny', 'active_anchor_ny',
        'progression_complete', 'range_transitions', 'hourly_progression', 'limits'))
    view['shift_story']['coverage'] = _pick(story.get('coverage', {}), (
        'complete', 'start_ny', 'end_ny', 'source_resolution_seconds', 'bar_count',
        'missing_bar_count', 'coverage_note'))
    ranges = view['shift_story']['ranges'] = []
    for row in story.get('ranges', []):
        item = _pick(row, ('anchor_start_ny', 'label', 'selected_at_ny', 'role', 'status',
            'direction_observed', 'invalidated_at_ny', 'entry_confirmed', 'execution_status',
            'variant_evidence', 'scoped_coverage'))
        item['anchor'] = _pick(row.get('anchor', {}), ('start_ny', 'end_ny', 'open', 'high',
            'low', 'close', 'midpoint', 'complete', 'source_resolution_seconds'))
        item['objectives'] = [_objective(x) for x in row.get('objectives', [])]
        item['observation_coverage'] = _pick(row.get('observation_coverage', {}), (
            'complete', 'start_ny', 'end_ny', 'source_resolution_seconds', 'missing_bar_count'))
        model = row.get('model1', {})
        item['model1'] = _pick(model, ('status', 'assigned_timeframe', 'identified_count',
            'next_candle_start_ny', 'observation_complete', 'forming_assigned_candle',
            'incomplete_assigned_candles'))
        item['model1']['candles'] = [_identity(candle) for candle in model.get('candles', [])]
        lifecycle = row.get('candle_lifecycle', {})
        item['candle_lifecycle'] = _pick(lifecycle, ('status', 'identified_count',
            'next_identity_open_ny', 'observation_complete', 'range_invalidated_at_ny'))
        item['candle_lifecycle']['model1_outcome_rows'] = [_lifecycle(fact)
            for fact in lifecycle.get('purge_candles', []) if fact.get('purge_type') == 'body_soup' and row.get('role') == 'selected_range']
        if row.get('role') != 'selected_range':
            item['candle_lifecycle']['independent_range_detail_omitted'] = True
        item['candle_lifecycle']['wick_soup_candles'] = [_pick(fact, ('bar_open_ny', 'bar_close_ny', 'purged_side'))
            for fact in lifecycle.get('purge_candles', []) if fact.get('purge_type') == 'wick_soup']
        bt = row.get('blessed_thief', {})
        item['blessed_thief'] = _pick(bt, ('status', 'summary', 'timeframe', 'direction',
            'window_end_ny', 'range_invalidated_at_ny', 'source_gap_at_ny'))
        item['detail_request'] = {'tool': 'review_market_crt', 'args': {
            'asset': asset, 'anchor_start_ny': row['anchor_start_ny'],
            'anchor_timeframe': 'H1', 'through_ny': end}}
        ranges.append(item)
    if review.get('paired_smt') is not None:
        view['paired_smt'] = _paired(review['paired_smt'], asset)
    context = review.get('paired_context', {})
    view['paired_context'] = _pick(context, ('status', 'comparison_asset', 'missing_asset', 'message'))
    view['paired_context']['ranges'] = []
    for row in context.get('ranges', []):
        pair = row['paired_review']
        if pair == review.get('paired_smt'):
            pair_view = {'same_evidence_as': 'review.paired_smt'}
        else:
            pair_view = _paired(pair, asset)
        view['paired_context']['ranges'].append({**_pick(row, ('anchor_start_ny', 'role')), 'paired_review': pair_view})
    out['review'] = view
    out['voice_view'] = {
        'kind': 'shift_overview', 'detail_omitted': True,
        'model1_outcome_columns': OUTCOME_COLUMNS, 'own_objective_columns': TARGET_COLUMNS,
        'note': 'Every hourly range, identified Model 1 identity, parent objective outcome and uncertainty is represented. '
                'Selected ranges include local Model 1 outcomes. Independent-range lifecycle details and the partner asset objective details require the range detail_request. '
                'Own objectives in model1_outcome_rows mean midpoint/opposing liquidity of that exact Model 1 candle. '
                'Paired-event objectives belong to that pair anchor and the named asset only. '
                'Parent outcomes refer to the enclosing H1 range objectives. All times are New York candle intervals, not exact ticks. '
                'Raw tables, repeated narrative and deeper lifecycle OHLC are omitted. For a specific candle, '
                'CSD, retest or Blessed Thief follow-up, use that range detail_request before answering omitted facts. '
                'Do not infer absence from omission. Source purge intervals and assigned-candle closes are different. '
                'Objective touches are not member fills; later physical delivery never restores CRT validity.'}
    return _budget_overview(out)


def _budget_overview(out):
    def size():
        return len(json.dumps(out, separators=(',', ':')))
    ranges = out['review']['shift_story']['ranges']
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # These prose summaries duplicate the structured state and are not the
        # selected-range narrative. Exact Blessed Thief detail is still queryable.
        for row in ranges:
            row.get('blessed_thief', {}).pop('summary', None)
        out['voice_view']['additional_detail_omitted'] = 'Blessed Thief prose; use each exact range detail_request.'
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        recap = out['review']['shift_recap']
        for key in ('headline', 'closing'):
            if recap.get(key) and recap[key] in recap.get('spoken_summary', ''):
                del recap[key]
        # Consolidate repeated policy prose; exact coverage/order/status facts
        # are retained in each range and objective, not replaced by this note.
        out['review'].pop('limits', None)
        out['review']['shift_story'].pop('limits', None)
        out['voice_view']['consolidated_limits'] = (
            'Closed broker OHLC reconstruction, not continuous observation or trade execution. '
            'Same-source-bar tick order is unknown. Missing/forming candles mean unverified, not absent. '
            'Independent ranges are not selected; incomplete hours cannot verify promotion. '
            'Model 1 identity is independent of later CSD, Super Soup or member fills. '
            'Own function delivery after local invalidation does not restore that CRT.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Identical paired qualification/evidence is emitted once. References
        # resolve within this same payload, never through another API request.
        seen = {}
        eligible = {'scope', 'setup_interval', 'execution_candles', 'paired_model1', 'objective_status'}
        def factor(value, path, key=None):
            if key in eligible and isinstance(value, (dict, list)):
                encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
                if len(encoded) > 180:
                    if encoded in seen:
                        return {'same_evidence_as': seen[encoded]}
                    seen[encoded] = path
            if isinstance(value, dict):
                return {k: factor(v, path + '/' + k.replace('~', '~0').replace('/', '~1'), k)
                        for k, v in value.items()}
            if isinstance(value, list):
                return [factor(v, path + '/' + str(i)) for i, v in enumerate(value)]
            return value
        for key in ('paired_smt', 'paired_context'):
            if key in out['review']:
                out['review'][key] = factor(out['review'][key], '#/review/' + key)
        out['voice_view']['reference_format'] = (
            'same_evidence_as is an exact duplicate at that JSON pointer in THIS payload. '
            'Read the referenced object for all qualification/status/timing facts; no detail call is needed.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        for row in ranges:
            lifecycle = row['candle_lifecycle']
            wick = lifecycle.pop('wick_soup_candles', [])
            lifecycle['wick_soup_candle_count_in_view'] = len(wick)
            lifecycle['wick_soup_details'] = 'Wick identities: see detail_request.'
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Keep every Model 1 identity/time. Compact repeated keys into a declared
        # table instead of silently slicing the identity list.
        columns = ['identity', 'timeframe', 'bar_open_ny', 'bar_close_ny',
                   'identified_at_ny', 'purged_side', 'purged_level', 'direction',
                   'purge_source_interval', 'source_resolution_seconds', 'exact_tick_time_known']
        for row in ranges:
            candles = row['model1'].pop('candles', [])
            row['model1']['identity_rows'] = [[c.get(k) for k in columns] for c in candles]
        out['voice_view']['model1_identity_columns'] = columns
    out['voice_view']['character_budget'] = SHIFT_OVERVIEW_TARGET_CHARS
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        recap = out['review']['shift_recap']
        named = recap.get('range_summaries', [])
        joined = ' '.join(row.get('text', '') for row in named if row.get('role') == 'selected_range')
        if recap.get('hourly_crt_summary') == joined:
            recap['hourly_crt_summary'] = {'join_text_from': '#/review/shift_recap/range_summaries',
                'where_role': 'selected_range', 'separator': ' '}
            out['voice_view']['hourly_summary_reference'] = (
                'hourly_crt_summary joins the inline selected range_summaries text in order; no extra retrieval.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # A normal shift overview retains the local-vs-parent outcome index;
        # exact nested target prices/touch intervals belong to focused detail.
        indexes = [i for i, name in enumerate(OUTCOME_COLUMNS) if name not in ('own_midpoint', 'own_opposing_liquidity')]
        for row in ranges:
            lifecycle = row['candle_lifecycle']
            records = lifecycle.pop('model1_outcome_rows', [])
            lifecycle['model1_outcome_index'] = [[record[i] for i in indexes] for record in records]
            lifecycle['nested_objective_detail_omitted'] = 'Nested target prices/times: see detail_request.'
        out['voice_view']['model1_outcome_index_columns'] = [OUTCOME_COLUMNS[i] for i in indexes]
        out['voice_view'].pop('model1_outcome_columns', None)
        out['voice_view'].pop('own_objective_columns', None)
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        out['voice_view']['note'] = (
            'Overview only. Own objectives belong to the named Model 1; parent outcomes belong to the enclosing range. '
            'Do not infer absence from omission. Use exact detail_request for omitted prices, touch times, wick candles or lifecycle detail. '
            'Source intervals differ from assigned candle closes. No member fills or restored CRT validity are inferred.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        for row in ranges:
            row['blessed_thief'] = {**_pick(row['blessed_thief'], ('status', 'timeframe', 'source_gap_at_ny')),
                'detail_omitted': True}
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Fail explicitly rather than serialize an unbounded request or pretend
        # to include a complete overview. Pinned context remains unchanged.
        return {'ok': False, 'status': 'voice_overview_budget_exceeded',
            'asset': out.get('asset'), 'market_context': out.get('market_context'),
            'message': 'This shift has more identified detail than fits one voice reply. '
                'Use a named range detail_request; the full shift was not supplied. Do not infer absent events.',
            'range_index': [_pick(row, ('anchor_start_ny', 'label', 'role', 'status', 'objectives', 'detail_request')) for row in ranges]}
    return out


def voice_tool_payload(name, result):
    """Use the overview only for successful full shifts, never precise questions."""
    if (name == 'review_market_session' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('shift_story'), dict)):
        return shift_voice_overview(result)
    return compact_voice_tool_result(name, result)
