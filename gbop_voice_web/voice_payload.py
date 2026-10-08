"""Bounded tool presentation; authoritative evidence and saved records stay intact.

Default shifts use a short synopsis, current reviews preserve snapshot freshness,
and journals use bounded saved-record views. Complete named-range evidence stays
queryable with the original asset, anchor, candle and cutoff; omission is not absence.
"""
from copy import deepcopy
import json

from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.directional_evidence import directional_candidate_evidence

SHIFT_OVERVIEW_TARGET_CHARS = 32000
VOICE_COMPACTION_TARGET_CHARS = 31000
SHIFT_SYNOPSIS_TARGET_CHARS = 12000
CURRENT_OVERVIEW_TARGET_CHARS = 16000


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
            'review_market_session', 'review_market_crt', 'review_current_market', 'review_other_market_ranges'}:
        return out
    if not all(key in out for key in ('selection', 'scope_id', 'evidence_id')):
        return out
    for key in ('recap', 'range_outcomes'):
        out.pop(key, None)
    # Focused candle pages have a tight evidence budget. Discussion navigation
    # stays in the server context/tool contract and is irrelevant to this page.
    if out.get('source_tool') == 'review_market_crt':
        out.pop('discussion_context', None)
    elif isinstance(out.get('discussion_context'), dict):
        out['discussion_context']['response_contract'] = (
            'Only completed playback marks discussion. Follow scope, mode and discussed anchors.')
    if evidence_ref:
        out['evidence_ref'] = evidence_ref
        out['snapshot_note'] = ('Use the supplied scoped review; repeated recap omitted.')
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
        if isinstance(value, (dict, list, str)):
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


def _compact_interval_tables(out):
    """Lossless column encoding for repeated, scalar-only interval schemas.

    Every source interval remains at its original evidence path. Only repeated
    field names move to the explicit column map; values, precision, uncertainty
    and parent/phase ownership are never merged or inferred.
    """
    from collections import Counter
    review = out['review']
    if 'interval_columns' in review:
        return
    allowed = {'bar_open_ny','bar_close_ny','precision_seconds','exact_tick_time_known',
               'timeframe','known_at_ny','confirmed_at_ny'}
    shapes = Counter()
    referenced_paths, marker_collision = set(), False

    def inspect(value):
        nonlocal marker_collision
        if isinstance(value, dict):
            marker_collision = marker_collision or 'interval_row' in value
            target = value.get('same_evidence_as')
            if isinstance(target, str):
                referenced_paths.add(target if target.startswith('#/') else '#/' + target.replace('.', '/'))
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(out)
    if marker_collision:
        return  # Unknown source content must never be mistaken for our codec.

    def eligible(value):
        return (isinstance(value, dict) and {'bar_open_ny','bar_close_ny'} <= set(value) <= allowed
                and all(not isinstance(item, (dict, list)) for item in value.values()))

    def eligible_path(value, path):
        return eligible(value) and not any(target.startswith(path + '/') for target in referenced_paths)

    def scan(value, path='#/review'):
        if eligible_path(value, path):
            shapes[tuple(sorted(value))] += 1
        elif isinstance(value, dict):
            for key, child in value.items():
                scan(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan(child, path + '/' + str(index))

    scan(review)
    columns, indexes = [], {}
    for keys, count in shapes.items():
        repeated_key_cost = sum(len(json.dumps(key)) + 1 for key in keys)
        if (repeated_key_cost - 20) * count > len(json.dumps(keys)) + 40:
            indexes[keys] = len(columns)
            columns.append(list(keys))
    if not columns:
        return

    def encode(value, path='#/review'):
        if eligible_path(value, path) and tuple(sorted(value)) in indexes:
            keys = tuple(sorted(value))
            return {'interval_row': [indexes[keys], *[value[key] for key in keys]]}
        if isinstance(value, dict):
            return {key: encode(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
                    for key, child in value.items()}
        if isinstance(value, list):
            return [encode(child, path + '/' + str(index)) for index, child in enumerate(value)]
        return value

    candidate = encode(review)
    candidate['interval_columns'] = columns
    instruction = 'interval_row[0] selects review.interval_columns; remaining values map exactly in order.'
    if _encoded_size(candidate) + len(instruction) + 24 < _encoded_size(review):
        out['review'] = candidate
        out['voice_view']['interval_tables'] = instruction


def _bounded_error(error, budget=SHIFT_OVERVIEW_TARGET_CHARS):
    """An oversized index/context must not make the budget error oversized too."""
    summary_note = ('Use verified_spoken_summary for the supplied outcomes. Detailed evidence is omitted; '
                    'scope/provenance alone adds no facts.')
    if error.get('verified_spoken_summary'):
        error = deepcopy(error)
        error.update(verified_summary_supplied=True, detail_evidence_omitted=True)
        if isinstance(error.get('market_context'), dict):
            error['market_context']['snapshot_note'] = summary_note
    if _encoded_size(error) <= budget:
        return error
    out = _pick(error, ('ok', 'status', 'asset', 'anchor_start_ny', 'through_ny',
                        'message', 'detail_request', 'raw_candle_request', 'backend_remaining_from_ny',
                        'negative_claim_guard', 'verified_spoken_summary',
                        'verified_summary_supplied', 'detail_evidence_omitted'))
    context = error.get('market_context')
    if isinstance(context, dict):
        out['market_context'] = _pick(context, ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'))
        out['market_context']['snapshot_note'] = (summary_note if out.get('verified_spoken_summary') else
            'Evidence and oversized indexes omitted; no outcome is established by this error.')
    out['evidence_omitted'] = True
    for name in ('range_index', 'identity_index'):
        if name in error:
            out[name + '_count'] = len(error[name])
    if _encoded_size(out) <= budget:
        return out
    # Reject malformed/unbounded metadata rather than truncate an identifier,
    # timestamp or request into a different, apparently valid scope.
    out = {'ok': False, 'status': error.get('status'), 'evidence_omitted': True,
           'message': 'The scoped evidence and metadata exceed the voice budget. '
                      'Ask for one asset, exact range and candle; no market outcome was supplied.'}
    for key in ('message', 'status', 'evidence_omitted'):
        if _encoded_size(out) <= budget:
            return out
        out.pop(key, None)
    if _encoded_size(out) <= budget:
        return out
    raise ValueError('Voice budget cannot fit even an explicit failure result.')


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


def _compact_shift_envelope(out):
    """Trim historical transport repetition without touching analytical evidence.

    The raw response and conversation snapshot retain the complete source data.
    Closed-hour/window lists duplicate a complete shift's coverage, but partial
    or unknown coverage must retain its exact gaps and observed windows.
    """
    availability = out.get('availability')
    if isinstance(availability, dict):
        review = out.get('review') or {}
        selection = (out.get('market_context') or {}).get('selection') or {}
        through = ((review.get('shift_synopsis') or {}).get('through_ny')
                   or (review.get('shift_story') or {}).get('end_ny'))
        for key, duplicate in (('asset', out.get('asset')), ('date_ny', review.get('date_ny')),
                               ('shift', review.get('shift')), ('anchor_start_ny', selection.get('anchor_start_ny')),
                               ('end_ny', through), ('source_resolution_seconds', out.get('available_precision_seconds'))):
            if key in availability and availability[key] == duplicate:
                availability.pop(key)
        if (availability.get('status') == 'available_full' and availability.get('shift_complete') is True
                and availability.get('anchor_complete') is True):
            for key in ('message', 'complete_hours_ny', 'observed_windows_ny'):
                availability.pop(key, None)
            availability['coverage_detail_omitted'] = True
    health = out.get('feed_health')
    if (isinstance(health, dict) and health.get('status') == 'recent_snapshot_stale_quote'
            and health.get('message') == 'A recent broker snapshot was received, but its last quote is old. This does not prove market closure or identify a feed failure.'):
        # The canonical warning is repeated in transport on every historical
        # page; keep its uncertainty explicitly, plus every freshness fact.
        health['message'] = 'Recent snapshot; quote still stale. Market closure and failure cause unverified.'
    context = out.get('market_context')
    if (isinstance(context, dict) and isinstance(context.get('discussion_context'), dict)
            and context['discussion_context'].get('response_contract') == 'Only completed playback marks discussion. Follow scope, mode and discussed anchors.'):
        context['discussion_context'].pop('response_contract', None)
    out['voice_view']['transport_detail_omitted'] = True


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
                'note': 'Every hourly range, identified Model 1 body candle, selected-range objective outcome and uncertainty is represented. '
                'Selected ranges include the first original-direction Model 1 lifecycle card. Other lifecycle cards and later wick identities are explicitly omitted and require the range detail_request with exact detail_candle_start_ny. '
                'Own objectives in model1_outcomes mean midpoint/opposing liquidity of that exact Model 1 candle. Every identity has an attempt_role; separate_opposite_direction cannot be added to the original setup. '
                'Paired-event objectives belong to that pair anchor and the named asset only. '
                'Selected-range outcomes refer to that named H1 range, not assumed candle containment. All times are New York candle intervals, not exact ticks. '
                'Raw tables, repeated narrative and deeper lifecycle OHLC are omitted. For a specific candle, '
                'CSD, retest or Blessed Thief follow-up, use that range detail_request before answering omitted facts. '
                'Do not infer absence from omission. Source purge intervals and assigned-candle closes are different. '
                'Objective touches are not member fills; later physical delivery never restores CRT validity.'}
    return _budget_overview(out)


def _budget_overview(out):
    def size():
        return len(json.dumps(out, separators=(',', ':')))
    ranges = out['review']['shift_story']['ranges']
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        # These prose summaries duplicate the structured state and are not the
        # selected-range narrative. Exact Blessed Thief detail is still queryable.
        for row in ranges:
            row.get('blessed_thief', {}).pop('summary', None)
        out['voice_view']['additional_detail_omitted'] = 'Blessed Thief prose; use detail_request.'
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        recap = out['review']['shift_recap']
        for key in ('headline', 'closing'):
            if recap.get(key) and recap[key] in recap.get('spoken_summary', ''):
                del recap[key]
        # Consolidate repeated policy prose; exact coverage/order/status facts
        # are retained in each range and objective, not replaced by this note.
        out['review'].pop('limits', None)
        out['review']['shift_story'].pop('limits', None)
        out['voice_view']['consolidated_limits'] = (
            'Same-bar order unknown; gaps block handoffs. Under review is not CRT confirmation. Later delivery never restores validity.')
    if size() > VOICE_COMPACTION_TARGET_CHARS:
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
            'same_evidence_as is an in-payload pointer.')
    out['voice_view']['character_budget'] = SHIFT_OVERVIEW_TARGET_CHARS
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        recap = out['review']['shift_recap']
        named = recap.get('range_summaries', [])
        joined = ' '.join(row.get('text', '') for row in named if row.get('role') == 'selected_range')
        if recap.get('hourly_crt_summary') == joined:
            recap['hourly_crt_summary'] = {'join_text_from': '#/review/shift_recap/range_summaries',
                'where_role': 'selected_range', 'separator': ' '}
            out['voice_view']['hourly_summary_reference'] = (
                'hourly_crt_summary joins the inline selected range_summaries text in order; no extra retrieval.')
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        # Keep the outcome-first selected-shift narrative and every structured
        # range. The second, per-range prose retelling is not new evidence.
        recap = out['review']['shift_recap']
        recap.pop('hourly_crt_summary', None)
        recap.pop('range_summaries', None)
        out['voice_view'].pop('hourly_summary_reference', None)
        out['voice_view']['range_recap_prose_omitted'] = (
            'Duplicate prose omitted; evidence remains.')
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        out['voice_view']['note'] = (
            'Model 1 bodies/initiating lifecycle retained. Do not infer absence; use detail_request. Own/parent/paired scopes differ.')
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        for row in ranges:
            row['blessed_thief'] = {**_pick(row['blessed_thief'], ('status', 'timeframe', 'source_gap_at_ny')),
                'detail_omitted': True}
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        # These are duplicate transport instructions, not analytical evidence.
        # Each omitted BT card and each in-payload reference remains explicit.
        out['voice_view'].pop('additional_detail_omitted', None)
        out['voice_view'].pop('reference_format', None)
        if 'range_recap_prose_omitted' in out['voice_view']:
            out['voice_view']['range_recap_prose_omitted'] = True
        out['voice_view'].pop('consolidated_limits', None)
        out['voice_view']['note'] = (
            'Keep own/parent/paired scopes distinct. Do not infer absence; use detail_request. '
            'same_evidence_as resolves here. Gaps/order uncertain; no fills/restored validity.')
        _factor_review(out)
    if size() > SHIFT_OVERVIEW_TARGET_CHARS and 'post_shift_followthrough' in out:
        out.pop('post_shift_followthrough')  # Registered tool's null-scope lookup retains exact access.
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        _compact_shift_envelope(out)
    if size() > VOICE_COMPACTION_TARGET_CHARS:
        _compact_interval_tables(out)
    if size() > SHIFT_OVERVIEW_TARGET_CHARS:
        # Fail explicitly rather than serialize an unbounded request or pretend
        # to include a complete overview. Scope and provenance stay pinned.
        summary = out['review']['shift_recap'].get('spoken_summary')
        retained_summary = (summary if isinstance(summary, str) and 0 < _encoded_size(summary) <= 6000 else None)
        return _bounded_error({'ok': False, 'status': 'voice_overview_budget_exceeded',
            'asset': out.get('asset'), 'market_context': _voice_market_context(out.get('market_context')),
            'message': 'This shift has more identified detail than fits one voice reply. '
                'Use a named range detail_request; the full shift was not supplied. Do not infer absent events.',
            **({'verified_spoken_summary': retained_summary} if retained_summary else {}),
            'range_index': [_pick(row, ('anchor_start_ny', 'label', 'role', 'status', 'direction_observed',
                'invalidated_at_ny', 'objectives', 'detail_request')) for row in ranges],
            'detail_request': next((row['detail_request'] for row in ranges if row.get('role') == 'selected_range'), None)})
    return out


def _compact_synopsis_navigation(out):
    """Share exact request scope and duplicate policy prose, never market facts.

    A compact index still names every range. Its common request is expanded by
    adding that row's anchor_start_ny; require exact equality before factoring,
    so an exceptional asset, cutoff, timeframe or future argument stays intact.
    This runs only on the copied voice view, after authoritative context capture.
    """
    synopsis = out['review']['shift_synopsis']
    index = synopsis.get('range_index', [])
    common, common_encoded = None, None
    for row in index:
        request = deepcopy(row.get('detail_request'))
        if not isinstance(request, dict) or not isinstance(request.get('args'), dict):
            return
        anchor = row.get('anchor_start_ny')
        if not isinstance(anchor, str) or not anchor or request['args'].pop('anchor_start_ny', None) != anchor:
            return
        encoded = json.dumps(request, sort_keys=True, separators=(',', ':'))
        if common_encoded is not None and encoded != common_encoded:
            return
        common, common_encoded = request, encoded
    if common is None:
        return
    synopsis['range_detail_request'] = common
    for row in index:
        del row['detail_request']
    out['voice_view']['note'] = (
        'Detail: add range_index.anchor_start_ny to range_detail_request.args.')
    # Consolidate the two synopsis instruction copies. Every structured fact,
    # spoken_summary, coverage caveat and negative-claim guard stays unchanged.
    synopsis['response_contract'] = (
        'Name each range through GTOP shift end. Under review is not CRT confirmation. Explain variants/DOL. '
        'Keep primary body, own CRT/CISD, parent, re-purges, prior delivery, DP and induced 50% distinct. '
        'Context counts are event-time ranges, not Soup/closure votes or probability. Name acting candle AND affected range; own anchor closure is not subsequent CRT confirmation. Omission is not absence; use range_detail_request.')



def _compact_synopsis_facts(out):
    """Lossless small-table encoding for dense concurrent range summaries."""
    synopsis = out['review']['shift_synopsis']
    rows = synopsis.get('ranges', [])
    defaults = {}
    for key in ('anchor_timeframe', 'play', 'role', 'direction', 'verdict', 'outcome',
                'invalidated_at_ny', 'coverage_complete'):
        if not rows or not all(key in row and (row[key] is None or isinstance(row[key], (str, int, float, bool))) for row in rows):
            continue
        choices = {}
        for row in rows:
            encoded = json.dumps(row[key], sort_keys=True)
            choices.setdefault(encoded, []).append(row)
        encoded, matching = max(choices.items(), key=lambda item: len(item[1]))
        if len(matching) < 2:
            continue
        defaults[key] = deepcopy(matching[0][key])
        for row in matching:
            del row[key]
    if defaults:
        synopsis['range_defaults'] = defaults
    columns = ('bar_open_ny', 'bar_close_ny', 'precision_seconds')
    count = 0
    objective_count = 0
    objective_columns = ('status', 'level', 'liquidity_side', 'source_interval')
    def intervals(value):
        nonlocal count, objective_count
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key in ('source_interval', 'first_purge_interval') and isinstance(child, dict) and set(child) == set(columns):
                    value[key] = [child[column] for column in columns]
                    count += 1
                else:
                    intervals(child)
                    if (key in ('midpoint', 'opposing_liquidity') and isinstance(child, dict)
                            and set(child) in (set(objective_columns), set(objective_columns[:3]))):
                        value[key] = [child[column] for column in objective_columns if column in child]
                        objective_count += 1
        elif isinstance(value, list):
            for child in value:
                intervals(child)
    intervals(synopsis)
    if count:
        synopsis['source_interval_columns'] = list(columns)
    if objective_count:
        synopsis['objective_columns'] = list(objective_columns)
    out['voice_view']['fact_tables'] = ('range_defaults fill missing range keys; arrays follow named columns; absent trailing cells stay absent.')
    synopsis['response_contract'] = ('Under review is not CRT confirmation. Keep acting candle, own/parent, original/reversal separate. '
        'Event-time order; no probability votes. Omission is not absence; use exact details.')


def shift_voice_synopsis(result):
    """Small default presentation after raw conversation evidence was captured.

    The complete overview remains available via shift_voice_overview. Nothing in
    this view replaces the raw identity index or exact range/candle detail tools.
    """
    from gbop_voice_web.shift_synopsis import build_shift_synopsis
    review = result['review']
    out = {key: deepcopy(value) for key, value in result.items() if key != 'review'}
    if 'market_context' in out:
        out['market_context'] = _voice_market_context(out['market_context'], evidence_ref='#/review')
    out['review'] = _pick(review, ('date_ny', 'shift', 'timezone', 'source_resolution_seconds'))
    out['review']['shift_synopsis'] = build_shift_synopsis(review, result.get('asset'))
    out['voice_view'] = {
        'kind': 'shift_synopsis', 'detail_omitted': True,
        'character_budget': SHIFT_SYNOPSIS_TARGET_CHARS,
        'note': 'Follow the selected range to shift_end; only a recorded transition '
                'changes selection. Name supported variants/candidates with reasons and missing conditions. '
                'Omission is not absence; range_index retrieves Model 1/CISD/Soup detail.'}
    synopsis = out['review']['shift_synopsis']
    range_index = deepcopy(synopsis['range_index'])
    # Factor repeated navigation before the hard ceiling, leaving room for
    # conversation scope and the newly required concurrent/pending evidence.
    if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS * .75:
        _compact_synopsis_navigation(out)
    if (_encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS
            or _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS * .75
            and any(row.get('pending_reversal') for row in synopsis.get('ranges', []))):
        _compact_synopsis_facts(out)
    if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS:
        _factor_review(out)
        out['voice_view']['fact_references'] = 'same_evidence_as is an in-payload pointer.'
    if _encoded_size(out) >= SHIFT_SYNOPSIS_TARGET_CHARS - 300 and 'post_shift_followthrough' in out:
        # Optional navigation is re-readable with expected_scope_id=null. Never
        # drop original shift evidence or fail a previously fitting synopsis
        # merely to carry future-context hashes in every spoken recap.
        out.pop('post_shift_followthrough')
    if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS - 600:
        _compact_shift_envelope(out)
    if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS:
        summary = synopsis.get('spoken_summary')
        retained_summary = (summary if isinstance(summary, str) and 0 < _encoded_size(summary) <= 6000 else None)
        return _bounded_error({'ok': False, 'status': 'voice_synopsis_budget_exceeded',
            'asset': out.get('asset'), 'market_context': _voice_market_context(out.get('market_context')),
            'message': ('The complete evidence view exceeds the response budget. Use the verified spoken summary below; '
                        'retrieve exact named-range details only for omitted facts.' if retained_summary else
                        'This synopsis exceeds the response budget. Request an exact named range; no synopsis was supplied.'),
            **({'verified_spoken_summary': retained_summary} if retained_summary else {}),
            'range_index': range_index,
            **({'negative_claim_guard': deepcopy(synopsis['negative_claim_guard'])}
               if synopsis.get('negative_claim_guard') else {}),
            'detail_request': next((r['detail_request'] for r in range_index if r['label'] == '9ate8'), None)},
            SHIFT_SYNOPSIS_TARGET_CHARS)
    return out


def current_voice_overview(result):
    """Current scope/freshness and compact states, without its raw journal tree."""
    review = result['review']
    out = {key: deepcopy(value) for key, value in result.items() if key != 'review'}
    if 'market_context' in out:
        out['market_context'] = _voice_market_context(out['market_context'], evidence_ref='#/review')
    out['review'] = _pick(review, ('mode', 'as_of_ny', 'analysis_cutoff_ny', 'observed_through_ny',
        'current_scope', 'session_clock', 'freshness', 'selection_status', 'current_candle',
        'ranges', 'paired_context', 'response_contract'))
    out['review']['current_candle'] = _pick(review.get('current_candle', {}), (
        'start_ny', 'end_ny', 'timeframe', 'status', 'complete', 'forming', 'open', 'high',
        'low', 'close', 'midpoint', 'observed_through_ny', 'bar_count', 'missing_bar_count',
        'source_resolution_seconds', 'coverage_note'))
    out['voice_view'] = {
        'kind': 'current_market', 'detail_omitted': True,
        'character_budget': CURRENT_OVERVIEW_TARGET_CHARS,
        'note': 'On-demand current snapshot. Preserve actual as-of, source cutoff and freshness; '
                'forming candles stay provisional. Do not infer absence from omission. '
                'Raw selected-range lifecycle remains retained for exact detail/journal binding; '
                'use each detail_request at this cutoff. Never substitute a completed shift.'}
    if _encoded_size(out) > CURRENT_OVERVIEW_TARGET_CHARS:
        _factor_review(out)
    if _encoded_size(out) > CURRENT_OVERVIEW_TARGET_CHARS:
        return _bounded_error({'ok': False, 'status': 'voice_current_budget_exceeded',
            'asset': out.get('asset'), 'market_context': _voice_market_context(out.get('market_context')),
            'message': 'Current evidence exceeds the response budget. Use the scoped detail request; no current overview was supplied.',
            'detail_request': next((r.get('detail_request') for r in review.get('ranges', [])
                                    if r.get('role') == 'selected_range'), None)},
            CURRENT_OVERVIEW_TARGET_CHARS)
    return out


def voice_tool_payload(name, result):
    """Select a bounded presentation after authoritative context capture."""
    if name == 'review_market_contexts':
        return deepcopy(result)  # Already bounded; never omit one requested member.
    if name == 'select_market_context' and result.get('ok'):
        try:
            selected = voice_tool_payload(result['source_tool'], result)
        except (KeyError, TypeError, ValueError):
            return _bounded_error({'ok': False, 'status': 'selected_context_payload_invalid',
                'market_context': result.get('market_context'),
                'message': 'The retained evidence cannot be presented safely. Retrieve its exact range again; no outcome was supplied.'})
        if _encoded_size(selected) > SHIFT_OVERVIEW_TARGET_CHARS:
            return _bounded_error({'ok': False, 'status': 'selected_context_payload_budget_exceeded',
                'market_context': result.get('market_context'),
                'message': 'The selected evidence exceeds the output budget; request one exact range and candle. No outcome was supplied.'})
        return selected
    if name == 'scan_young_lefty':
        if _encoded_size(result) > 28000:
            return {'ok': False, 'status': 'scan_payload_budget_exceeded',
                    'message': 'Scan evidence exceeded its response budget; no complete scan result was supplied.'}
        return deepcopy(result)
    if (name == 'review_other_market_ranges' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('other_range_followup'), dict)):
        out = deepcopy(result)
        out['market_context'] = _voice_market_context(out.get('market_context'), evidence_ref='#/review')
        out['voice_view'] = {'kind': 'other_range_followup', 'detail_omitted': True,
                            'character_budget': SHIFT_SYNOPSIS_TARGET_CHARS,
                            'note': 'Follow mode and named ranges through shift end; keep phase identities and discussion bridge. Retrieval is not discussion.'}
        if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS:
            # The same selected-range narrative also exists in structured
            # active context and the top-level follow-up summary. Keep phase
            # evidence (including official double-purge confirmation) rather
            # than paying for the second full prose rendering.
            active = out.get('review', {}).get('other_range_followup', {}).get('active_range_context')
            if isinstance(active, dict) and active.get('spoken_summary'):
                active.pop('spoken_summary')
                out['voice_view']['secondary_prose_omitted'] = 'Active-range prose; structured chronology and follow-up summary remain.'
        if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS:
            # Reuse the existing lossless in-payload references. No range,
            # actor/parent relation, objective or qualification is discarded.
            _factor_review(out)
            out['voice_view']['reference_format'] = 'same_evidence_as resolves to an in-payload JSON pointer.'
        if _encoded_size(out) > SHIFT_SYNOPSIS_TARGET_CHARS:
            return _bounded_error({'ok': False, 'status': 'voice_other_ranges_budget_exceeded',
                'asset': out.get('asset'), 'market_context': _voice_market_context(out.get('market_context')),
                'message': 'Remaining range evidence exceeds the voice budget. Request one exact range; no follow-up was supplied.'},
                SHIFT_SYNOPSIS_TARGET_CHARS)
        return out
    if (name == 'review_current_market' and isinstance(result, dict)
            and result.get('review', {}).get('mode') == 'current_market'):
        return current_voice_overview(result)
    if (name == 'review_market_session' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('shift_story'), dict)):
        return shift_voice_synopsis(result)
    if (name == 'review_market_crt' and isinstance(result, dict)
            and isinstance(result.get('review', {}).get('candle_lifecycle'), dict)):
        from gbop_voice_web.voice_detail import crt_voice_detail
        return crt_voice_detail(result)
    from gbop_voice_web.journal_presentation import journal_tool_payload, JOURNAL_PRESENTATION_TOOLS
    if name in JOURNAL_PRESENTATION_TOOLS:
        return journal_tool_payload(name, result)
    return compact_voice_tool_result(name, result)
