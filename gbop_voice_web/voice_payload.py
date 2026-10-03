"""Bounded market overviews for voice; authoritative detailed tools stay intact.

The overview is a purpose-built view, never an arbitrary cut of serialized JSON.
Every range and identified candle is named. Deeper OHLC/lifecycle evidence is
explicitly queryable by the precise original asset, anchor and cutoff.
"""
from copy import deepcopy
import json

from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.directional_evidence import directional_candidate_evidence

SHIFT_OVERVIEW_TARGET_CHARS = 32000


def _encoded_size(value):
    return len(json.dumps(value, separators=(',', ':')))


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _voice_market_context(context, *, evidence_ref=None):
    """Keep the conversation fence, not a second copy of the same tool evidence.

    The full snapshot remains in MarketConversation and the raw tool result.
    Only our known snapshot schema may be reduced, and only when its evidence
    is represented by this voice view. Unknown/external contexts stay intact.
    A failed page must never point to evidence that was not actually supplied.
    """
    out = deepcopy(context)
    if not isinstance(out, dict) or out.get('source_tool') not in {
            'review_market_session', 'review_market_crt'}:
        return out
    if not all(key in out for key in ('selection', 'scope_id', 'evidence_id')):
        return out
    for key in ('recap', 'range_outcomes'):
        out.pop(key, None)
    if evidence_ref:
        out['evidence_ref'] = evidence_ref
        out['snapshot_note'] = ('Repeated conversation recap/outcomes omitted; use the supplied review '
                                'within its declared coverage and paging limits.')
    else:
        out.pop('evidence_ref', None)
        out['snapshot_note'] = ('Evidence details were not supplied on this page. Scope/provenance '
                                'alone do not establish an outcome; use the scoped detail request.')
    return out


def _factor_review(out):
    """Losslessly share repeated evidence at resolvable, in-payload pointers.

    Run after all omissions so no reference can point at a removed field.
    Factoring changes the transport only, never a fact, time, or outcome.
    """
    seen = {}

    def factor(value, path, key=None):
        if isinstance(value, (dict, list)) or key == 'response_contract':
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
            if len(encoded) > 180:
                if encoded in seen:
                    ref = {'same_evidence_as': seen[encoded]}
                    if _encoded_size(ref) < len(encoded):
                        return ref
                seen[encoded] = path
        if isinstance(value, dict):
            return {k: factor(v, path + '/' + k.replace('~', '~0').replace('/', '~1'), k)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [factor(v, path + '/' + str(i)) for i, v in enumerate(value)]
        return value

    out['review'] = factor(out['review'], '#/review')


def _bounded_error(error, budget=SHIFT_OVERVIEW_TARGET_CHARS):
    """An oversized index/context must not make the budget error oversized too."""
    if _encoded_size(error) <= budget:
        return error
    out = _pick(error, ('ok', 'status', 'asset', 'anchor_start_ny', 'through_ny',
                        'message', 'detail_request', 'raw_candle_request', 'backend_remaining_from_ny'))
    context = error.get('market_context')
    if isinstance(context, dict):
        out['market_context'] = _pick(context, ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'))
        out['market_context']['snapshot_note'] = 'Evidence and oversized indexes omitted; no outcome is established by this error.'
    out['evidence_omitted'] = True
    for name in ('range_index', 'identity_index'):
        if name in error:
            out[name + '_count'] = len(error[name])
    if _encoded_size(out) <= budget:
        return out
    # Reject malformed/unbounded metadata rather than truncate an identifier,
    # timestamp or request into a different, apparently valid scope.
    return {'ok': False, 'status': error.get('status'), 'evidence_omitted': True,
            'message': 'The scoped evidence and metadata exceed the voice budget. '
                       'Ask for one asset, exact range and candle; no market outcome was supplied.'}


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


def _paired_event(event, asset, *, overview=False):
    out = _pick(event, ('direction', 'side', 'bar_open_ny', 'bar_close_ny',
        'precision_seconds', 'exact_tick_time_known', 'swept_asset', 'nonconfirming_asset',
        'boneless_asset', 'potential_boneless_asset', 'boneless_status',
        'local_purge_inferred_for_boneless_asset', 'entry_confirmed',
        'play_context', 'historical_event_only', 'setup_interval', 'recap_eligible'))
    out['scope'] = {k: deepcopy(v) for k, v in event.get('scope', {}).items() if k != 'response_contract'}
    out['objective_status'] = {a: {key: _pick(target, ('level', 'status', 'touch_bar_open_ny', 'touch_bar_close_ny', 'range_invalidated_at_ny', 'same_event_bar_touch_order_unknown')) for key, target in targets.items()}
                             for a, targets in event.get('objective_status', {}).items() if a == asset}
    model = event.get('paired_model1', {})
    out['paired_model1'] = _pick(model, ('status', 'assigned_timeframe', 'purge_asset',
        'boneless_asset', 'local_body_purge_inferred', 'csd_status', 'execution_status',
        'smt_qualified_at_ny', 'partner_csd', 'thesis_support_status'))
    for key in ('origin_model1', 'boneless_reference'):
        if model.get(key):
            out['paired_model1'][key] = _pick(model[key], ('identity', 'timeframe', 'bar_open_ny', 'bar_close_ny', 'identity_basis', 'asset', 'local_purge_observed', 'purge_context_supplied_by', 'csd_status', 'execution_status', 'smt_qualified_at_ny'))
    if overview and event.get('boneless_status') == 'disqualified' and event.get('recap_eligible') is False:
        # A rejected paired thesis is not an entry candidate or confluence.
        # Keep its exact rejection evidence; its independent target/CSD detail
        # is available at the enclosing range request. Never apply this to
        # potential, pending or qualified/completed boneless evidence.
        out.pop('objective_status', None)
        out.pop('paired_model1', None)
        out['setup_interval'] = _pick(event.get('setup_interval', {}), (
            'start_ny', 'end_ny', 'complete', 'qualified_smt', 'potential_smt',
            'status', 'qualified_at_ny', 'own_purge_observed'))
        out['scope'] = _pick(event.get('scope', {}), ('status',
            'peer_catchup_bar_open_ny', 'peer_catchup_bar_close_ny',
            'whole_shift_boneless_identity_established'))
        for key in ('play_context', 'setup_interval_context', 'local_purge_inferred_for_boneless_asset',
                    'boneless_asset', 'potential_boneless_asset', 'historical_event_only'):
            out.pop(key, None)
        out['detail_omitted'] = 'Disqualified paired target/CSD detail; use this range detail_request.'
    return out


def _paired(pair, asset, *, overview=False):
    out = _pick(pair, ('ok', 'status', 'assessment_status', 'message', 'assets', 'anchor_start_ny', 'anchor_end_ny',
        'anchor_timeframe', 'through_ny', 'divergence_window_end_ny', 'divergence_confirmed',
        'paired_coverage_complete', 'paired_through_ny', 'execution_candles',
        'invalidating_closes_ny', 'same_bar_both_swept_sides', 'recap_eligible'))
    out['events'] = [_paired_event(event, asset, overview=overview) for event in pair.get('events', [])]
    return out


def shift_voice_overview(result):
    review = result['review']
    story = review['shift_story']
    asset = result.get('asset')
    end = story.get('end_ny')
    out = {key: deepcopy(value) for key, value in result.items() if key != 'review'}
    if 'market_context' in out:
        out['market_context'] = _voice_market_context(out['market_context'], evidence_ref='#/review')
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
        if 'directional_outcome' in row:
            item['directional_outcome'] = _pick(row['directional_outcome'], (
                'direction', 'status', 'initiating_identity', 'first_source_return_inside',
                'delivery_before_later_invalidation'))
        item['anchor'] = _pick(row.get('anchor', {}), ('start_ny', 'end_ny', 'open', 'high',
            'low', 'close', 'midpoint', 'complete', 'source_resolution_seconds'))
        item['objectives'] = [_objective(x) for x in row.get('objectives', [])]
        item['observation_coverage'] = _pick(row.get('observation_coverage', {}), (
            'complete', 'start_ny', 'end_ny', 'source_resolution_seconds', 'missing_bar_count'))
        model = row.get('model1', {})
        item['model1'] = _pick(model, ('status', 'assigned_timeframe', 'identified_count',
            'next_candle_start_ny', 'observation_complete', 'forming_assigned_candle',
            'incomplete_assigned_candles'))
        candidates = directional_candidate_evidence(row, asset,
            max_cards=1 if row.get('role') == 'selected_range' else 0)
        item['model1']['candles'] = [identity for identity in candidates['identity_index']
                                    if identity['purge_type'] == 'body_soup']
        lifecycle = row.get('candle_lifecycle', {})
        item['candle_lifecycle'] = _pick(lifecycle, ('status', 'identified_count',
            'next_identity_open_ny', 'observation_complete', 'range_invalidated_at_ny'))
        item['candle_lifecycle']['model1_outcomes'] = candidates['candidate_cards']
        item['candle_lifecycle']['wick_soup_candles'] = [identity for identity in candidates['identity_index']
                                                       if identity['purge_type'] == 'wick_soup']
        item['candle_lifecycle']['detail_omissions'] = _pick(candidates['coverage'], (
            'omitted_available_identity_count', 'first_omitted_available_candle_open_ny',
            'omitted_lifecycle_card_count'))
        if row.get('role') != 'selected_range':
            item['candle_lifecycle']['independent_range_detail_omitted'] = True
        bt = row.get('blessed_thief', {})
        item['blessed_thief'] = _pick(bt, ('status', 'summary', 'timeframe', 'direction',
            'window_end_ny', 'range_invalidated_at_ny', 'source_gap_at_ny'))
        item['detail_request'] = {'tool': 'review_market_crt', 'args': {
            'asset': asset, 'context_action': 'continue', 'anchor_start_ny': row['anchor_start_ny'],
            'anchor_timeframe': 'H1', 'through_ny': end}}
        ranges.append(item)
    if review.get('paired_smt') is not None:
        view['paired_smt'] = _paired(review['paired_smt'], asset, overview=True)
    context = review.get('paired_context', {})
    view['paired_context'] = _pick(context, ('status', 'comparison_asset', 'missing_asset', 'message'))
    view['paired_context']['ranges'] = []
    for row in context.get('ranges', []):
        pair = row['paired_review']
        if pair == review.get('paired_smt'):
            pair_view = {'same_evidence_as': 'review.paired_smt'}
        else:
            pair_view = _paired(pair, asset, overview=True)
        view['paired_context']['ranges'].append({**_pick(row, ('anchor_start_ny', 'role')), 'paired_review': pair_view})
    out['review'] = view
    out['voice_view'] = {
        'kind': 'shift_overview', 'detail_omitted': True,
                'note': 'Every hourly range, identified Model 1 body candle, parent objective outcome and uncertainty is represented. '
                'Selected ranges include the first original-direction Model 1 lifecycle card. Other lifecycle cards and later wick identities are explicitly omitted and require the range detail_request with exact detail_candle_start_ny. '
                'Own objectives in model1_outcomes mean midpoint/opposing liquidity of that exact Model 1 candle. Every identity has an attempt_role; separate_opposite_direction cannot be added to the original setup. '
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
            'Closed OHLC only; same-bar tick order unknown. Gaps/forming means unverified. '
            'Independent ranges are not selected; incomplete hours cannot verify promotion. '
            'Model 1 identity is separate from CSD/Soup/fills. Later delivery never restores invalid CRTs.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Identical paired qualification/evidence is emitted once. References
        # resolve within this same payload, never through another API request.
        seen = {}
        eligible = {'scope', 'setup_interval', 'execution_candles', 'paired_model1',
                    'objective_status', 'response_contract'}
        def factor(value, path, key=None):
            if key in eligible and isinstance(value, (dict, list, str)):
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
        # Keep the outcome-first selected-shift narrative and every structured
        # range. The second, per-range prose retelling is not new evidence.
        recap = out['review']['shift_recap']
        recap.pop('hourly_crt_summary', None)
        recap.pop('range_summaries', None)
        out['voice_view'].pop('hourly_summary_reference', None)
        out['voice_view']['range_recap_prose_omitted'] = (
            'Duplicate range prose omitted; named ranges, directions, objectives, identities and uncertainty remain. '
            'Use exact detail_request. Later invalidation preserves prior delivery.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        out['voice_view']['note'] = (
            'Named Model 1 bodies remain; first original-direction lifecycle and first wick per direction supplied. Fetch other exact candle details. Own targets belong to that Model 1; parent targets belong to its range. '
            'directional_outcome names the initial local thesis; paired_smt/paired_context retain separate paired theses. '
            'Do not infer absence from omission. Use exact detail_request for omitted prices, touch times, wick candles or lifecycle detail. '
            'Source intervals differ from assigned candle closes. No member fills or restored CRT validity are inferred.')
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        for row in ranges:
            row['blessed_thief'] = {**_pick(row['blessed_thief'], ('status', 'timeframe', 'source_gap_at_ny')),
                'detail_omitted': True}
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        _factor_review(out)
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Fail explicitly rather than serialize an unbounded request or pretend
        # to include a complete overview. Scope and provenance stay pinned.
        return _bounded_error({'ok': False, 'status': 'voice_overview_budget_exceeded',
            'asset': out.get('asset'), 'market_context': _voice_market_context(out.get('market_context')),
            'message': 'This shift has more identified detail than fits one voice reply. '
                'Use a named range detail_request; the full shift was not supplied. Do not infer absent events.',
            'range_index': [_pick(row, ('anchor_start_ny', 'label', 'role', 'status', 'direction_observed',
                'invalidated_at_ny', 'objectives', 'detail_request')) for row in ranges],
            'detail_request': next((row['detail_request'] for row in ranges if row.get('role') == 'selected_range'), None)})
    return out


def voice_tool_payload(name, result):
    """Use the overview only for successful full shifts, never precise questions."""
    if (name == 'review_market_session' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('shift_story'), dict)):
        return shift_voice_overview(result)
    if (name == 'review_market_crt' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('candle_lifecycle'), dict)):
        from gbop_voice_web.voice_detail import crt_voice_detail
        return crt_voice_detail(result)
    return compact_voice_tool_result(name, result)
