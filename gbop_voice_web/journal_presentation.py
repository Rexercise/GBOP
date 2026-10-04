"""Bounded journal tool presentation; durable member records are never edited.

Member reports, logging time and market provenance remain separate. Summaries
explicitly identify omitted text/evidence, and shortened pages keep their cursor
on the first unsupplied record rather than skipping it.
"""
from copy import deepcopy
import json
import math

JOURNAL_PRESENTATION_MAX_CHARS = 28000
JOURNAL_PRESENTATION_TOOLS = {
    'record_trade_feeling', 'save_journal_entry', 'edit_journal', 'get_journal_history', 'find_journal_setups',
}


def _size(value):
    return len(json.dumps(value, separators=(',', ':'), ensure_ascii=True))


def _exact(value, keys):
    """Never truncate an identifier, timestamp, price or execution fact."""
    result = {}
    for key in keys:
        if key not in value:
            continue
        item = value[key]
        if (item is None or isinstance(item, (bool, int))
                or isinstance(item, float) and math.isfinite(item)
                or isinstance(item, str) and len(item) <= 512):
            result[key] = deepcopy(item)
    return result


def _objective(value):
    result = _exact(value or {}, ('level', 'status', 'relative_to_model1_invalidation',
        'same_purge_bar_order_unknown', 'scope', 'reference_open_ny', 'reference_timeframe'))
    source = ((value or {}).get('evidence') or (value or {}).get('first_touch')
              or (value or {}).get('source_interval'))
    if isinstance(source, dict):
        result['source_interval'] = _exact(source, ('bar_open_ny', 'bar_close_ny',
            'precision_seconds', 'exact_tick_time_known'))
    return result


def _review(value):
    if not isinstance(value, dict):
        return None
    result = _exact(value, ('version', 'scope_id', 'evidence_id', 'source_tool',
        'recorded_at', 'matching_candle_count', 'candle_selection_required'))
    result['selection'] = _exact(value.get('selection') or {}, ('asset', 'date_ny',
        'shift', 'anchor_start_ny', 'anchor_timeframe', 'through_ny',
        'review_mode', 'as_of_ny', 'assigned_timeframe', 'evidence_status'))
    source = value.get('selected_candle')
    if isinstance(source, dict):
        candle = _exact(source, ('anchor_start_ny', 'bar_open_ny', 'bar_close_ny',
            'timeframe', 'purge_type', 'direction', 'purged_side', 'open', 'high', 'low', 'close'))
        csd = source.get('csd') or {}
        candle['csd'] = _exact(csd, ('status', 'reference_level', 'reference_boundary',
            'reference', 'rule'))
        confirmation = csd.get('evidence') or {}
        if isinstance(confirmation, dict) and confirmation:
            candle['csd']['evidence'] = _exact(confirmation, ('bar_open_ny', 'bar_close_ny',
                'confirmed_at_ny', 'timeframe', 'close', 'exact_tick_time_known'))
        structure = source.get('super_soup_structure') or {}
        if isinstance(structure, dict) and structure:
            candle['super_soup_structure'] = _exact(structure, ('structure_status',
                'structural_quality', 'occurrence_type', 'local_crt_outcome',
                'local_function_outcome', 'parent_function_outcome',
                'local_crt_invalidated_at_ny', 'local_function_window_end_ny'))
            targets = structure.get('local_function_objectives') or {}
            candle['super_soup_structure']['local_function_objectives'] = {
                name: _objective(targets[name]) for name in ('midpoint', 'opposing_liquidity')
                if isinstance(targets.get(name), dict)}
        invalid = source.get('model1_crt_invalidating_close')
        if isinstance(invalid, dict):
            candle['model1_crt_invalidating_close'] = _exact(invalid,
                ('bar_open_ny', 'bar_close_ny', 'close', 'complete'))
        result['selected_candle'] = candle
    else:
        result['selected_candle'] = None
    result['detail_omitted'] = True
    result['limits'] = ('Saved market evidence, not member execution. Detailed source evidence '
        'is omitted from this presentation; omission does not establish absence.')
    return result


def metadata_summary(value, result_r=None):
    """Return core reported facts and a bounded, explicitly partial audit view."""
    if not isinstance(value, dict):
        return value
    if (value.get('presentation') or {}).get('kind') == 'bounded_journal_metadata':
        return deepcopy(value)
    result = _exact(value, ('asset', 'direction', 'play', 'entry_model', 'tier', 'session',
        'trade_date', 'reported_entry_at', 'reported_exit_at', 'reported_outcome',
        'entry_price', 'exit_price', 'stop_price', 'target_price', 'pnl', 'risk',
        'exit_reason', 'kind', 'adherence', 'emotion'))
    feelings = value.get('feeling_history')
    if isinstance(feelings, list):
        result['feeling_history'] = [_exact(report, ('id', 'stage', 'feeling', 'reported_at',
            'recorded_at', 'source', 'correction_of')) for report in feelings[-6:] if isinstance(report, dict)]
        result['feeling_history_count'] = len(feelings)
        result['feeling_history_details_omitted'] = len(feelings) > 6
        result['feeling_history_note'] = 'Member reports only. Full append-only history, including corrections, remains available through send_journal_history. Unknown reported time is not logging time.'
    if 'self_grade' in value:
        from gbop_voice_web.trade_self_grades import self_grade_summary
        result['self_grade'] = self_grade_summary(value, result_r)
    sources = value.get('source_attachments')
    if isinstance(sources, list):
        result['source_attachment_count'] = len(sources)
        result['source_attachments'] = [_exact(v, ('photo_id', 'entry_index', 'legacy_journal_id'))
                                        for v in sources[:5] if isinstance(v, dict)]
        result['source_attachment_details_omitted'] = len(sources) > 5
    legacy = value.get('legacy_history')
    if isinstance(legacy, dict):
        result['legacy_history'] = _exact(legacy, ('needs_clarification',))
        result['legacy_history']['entry_count'] = len(legacy.get('journal_ids') or [])
        result['legacy_history']['conflicting_fields'] = [str(v) for v in (legacy.get('conflicting_fields') or [])[:8]]
        result['legacy_history']['full_history_preserved'] = True
    if isinstance(value.get('labels'), list):
        result['labels'] = [v for v in value['labels'][:8] if isinstance(v, str) and len(v) <= 80]
    if isinstance(value.get('market_review'), dict):
        result['market_review'] = _review(value['market_review'])
    provenance = value.get('provenance')
    if isinstance(provenance, dict):
        summary = _exact(provenance, ('version', 'association_status'))
        for key in ('context_defaults', 'member_reported'):
            if isinstance(provenance.get(key), list):
                summary[key] = [v for v in provenance[key][:32] if isinstance(v, str) and len(v) <= 80]
        corrections = provenance.get('corrections')
        if isinstance(corrections, list):
            summary['correction_count'] = len(corrections)
            if corrections and isinstance(corrections[-1], dict):
                latest = corrections[-1]
                summary['latest_correction'] = _exact(latest, ('recorded_at', 'result_corrected'))
                if isinstance(latest.get('fields'), dict):
                    summary['latest_correction']['fields'] = list(latest['fields'])[:32]
                summary['correction_details_omitted'] = True
        else:
            # A read path may already have supplied a summary. Do not turn
            # omitted correction history into an apparent count of zero.
            summary.update(_exact(provenance, ('correction_count', 'correction_details_omitted')))
            latest = provenance.get('latest_correction')
            if isinstance(latest, dict):
                summary['latest_correction'] = _exact(latest, ('recorded_at', 'result_corrected'))
                if isinstance(latest.get('fields'), list):
                    summary['latest_correction']['fields'] = [
                        item for item in latest['fields'][:32] if isinstance(item, str) and len(item) <= 80]
        result['provenance'] = summary
    result['presentation'] = {
        'kind': 'bounded_journal_metadata',
        'metadata_omitted_fields': sorted(set(value) - set(result)),
        'saved_metadata_unchanged': True,
        'note': 'Member-reported timestamps are separate from record creation/logging time. '
                'Omitted text or source evidence remains saved; do not infer it was absent.',
    }
    return result


def _update_preview(value):
    result = deepcopy(value)
    previews = []
    for key in ('details','note','entry_invalidation'):
        if isinstance(result.get(key), str) and len(result[key]) > 400:
            result[key] = result[key][:400]
            previews.append(key)
    for field, change in (result.get('changes') or {}).items():
        for side in ('before','after'):
            if isinstance(change.get(side), str) and len(change[side]) > 250:
                change[side] = change[side][:250]
                previews.append(field + '.' + side)
    if previews:
        result['text_previews'] = previews
        result['text_preview_note'] = 'Partial update prose; complete text is preserved and available through send_journal_history.'
    return result


def _record(value, preview_chars=600):
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    previews = []
    for field in ('description', 'summary', 'study_note', 'rule_adherence'):
        if isinstance(result.get(field), str) and len(result[field]) > preview_chars:
            result[field] = result[field][:preview_chars]
            previews.append(field)
    if isinstance(result.get('metadata'), dict):
        result['metadata'] = metadata_summary(result['metadata'], result.get('result_r'))
    updates = result.get('updates')
    if isinstance(updates, list):
        result['updates'] = [_update_preview(update) for update in updates[-3:]]
        result['update_count'] = len(updates)
        if len(updates) > 3:
            result['updates_details_omitted'] = True
            result['updates_note'] = 'Only the latest update previews are shown. Full earlier execution, management and journal history is preserved for send_journal_history.'
    facts = result.get('trade_facts')
    if isinstance(facts, dict):
        for field in ('objective','thesis_invalidation','close_note'):
            if isinstance(facts.get(field), str) and len(facts[field]) > preview_chars:
                facts[field] = facts[field][:preview_chars]
                previews.append('trade_facts.' + field)
    history = result.get('legacy_history')
    if isinstance(history, list):
        result['legacy_history'] = [_record(row, preview_chars=min(200, preview_chars)) for row in history[:3]]
        result['legacy_history_count'] = len(history)
        if len(history) > 3:
            result['legacy_history_details_omitted'] = True
            result['legacy_history_note'] = ('Full original history remains saved and is available '
                'through send_journal_history; these are only the first historical previews.')
    if previews:
        result['text_previews'] = previews
        result['text_preview_note'] = ('These fields are partial previews. Full saved journal text '
            'is available through send_journal_history; no saved text was changed.')
    return result


def journal_tool_payload(name, result):
    """Bound default journal replies while keeping accurate success and paging."""
    if name not in JOURNAL_PRESENTATION_TOOLS or not isinstance(result, dict):
        return result
    if ((result.get('journal_view') or {}).get('kind') in
            ('bounded_saved_journal_summary', 'journal_details_omitted')
            and _size(result) <= JOURNAL_PRESENTATION_MAX_CHARS):
        return deepcopy(result)
    out = _record(result)
    field = 'journals' if name == 'get_journal_history' else 'entries' if name == 'find_journal_setups' else None
    rows = result.get(field) if field else None
    if isinstance(rows, list):
        out[field] = [_record(row) for row in rows]
    out['journal_view'] = {'kind': 'bounded_saved_journal_summary',
        'character_budget': JOURNAL_PRESENTATION_MAX_CHARS,
        'saved_records_unchanged': True,
        'note': 'Core member reports and market provenance only. Omitted source details are not absent.'}
    if _size(out) <= JOURNAL_PRESENTATION_MAX_CHARS:
        return out
    # History supplies an exact cursor; find_setups has a fixed ten-result page.
    # Retain a prefix and place the next cursor at the first unsupplied record.
    next_offset = result.get('next_offset')
    if isinstance(rows, list) and rows and type(next_offset) is int:
        start = next_offset - (len(rows) if name == 'get_journal_history' else 10)
        if start >= 0:
            available = len(rows)
            while len(out[field]) > 1 and _size(out) > JOURNAL_PRESENTATION_MAX_CHARS - 400:
                out[field].pop()
            if len(out[field]) < available:
                out['has_more'] = True
                out['next_offset'] = start + len(out[field])
                out['journal_view'].update(records_omitted_from_this_page=available-len(out[field]),
                    next_page_note='Continue from next_offset with the same filters; unsupplied records were not skipped.')
            if _size(out) <= JOURNAL_PRESENTATION_MAX_CHARS:
                return out
    # Never convert a successful mutation into failure merely because its
    # presentation is too large: that could induce an unsafe repeat save.
    minimal = _exact(result, ('ok', 'saved', 'updated', 'journal_id', 'journal_number',
        'trade_id', 'trade_number', 'legacy_journal_number', 'is_legacy', 'record_kind',
        'canonical_record_exists', 'virtual_trade_record', 'legacy_history_count', 'update_count', 'needs_clarification',
        'result_r', 'final_result_r', 'journal_count', 'canonical_journal_count',
        'legacy_journal_count', 'preserved_legacy_history_count', 'stored_journal_entry_count', 'trade_count',
        'open_trade_count', 'closed_trade_count', 'error', 'status', 'rule_adherence',
        'risk_flags', 'recorded_thesis_risk', 'advisory_status'))
    minimal['journal_view'] = {'kind': 'journal_details_omitted',
        'character_budget': JOURNAL_PRESENTATION_MAX_CHARS, 'saved_records_unchanged': True,
        'note': 'Details exceed one reply and were not supplied. Check a smaller history page '
                'or request private journal text; do not repeat a successful save.'}
    if isinstance(rows, list):
        minimal['records_in_requested_page'] = len(rows)
    # Risk/adherence advisories must not disappear because an unrelated field
    # exceeded the budget. Preserve bounded warnings exactly and mark any excess.
    advisory_keys = ('warnings', 'warning', 'risk_warning', 'risk_warnings', 'advisories')
    for key in advisory_keys:
        warnings = result.get(key)
        if warnings is None:
            continue
        if isinstance(warnings, str):
            if _size(warnings) <= 4096 and _size({**minimal, key: warnings}) <= JOURNAL_PRESENTATION_MAX_CHARS - 1000:
                minimal[key] = warnings
            else:
                minimal[key + '_details_omitted'] = True
        elif isinstance(warnings, list):
            retained = []
            for warning in warnings:
                candidate = {**minimal, key: retained + [warning]}
                if _size(warning) > 4096 or _size(candidate) > JOURNAL_PRESENTATION_MAX_CHARS - 1000:
                    break
                retained.append(deepcopy(warning))
            minimal[key] = retained
            if len(retained) < len(warnings):
                minimal[key + '_details_omitted'] = True
                minimal[key + '_count'] = len(warnings)
        elif _size(warnings) <= 4096 and _size({**minimal, key: warnings}) <= JOURNAL_PRESENTATION_MAX_CHARS - 1000:
            minimal[key] = deepcopy(warnings)
        else:
            minimal[key + '_details_omitted'] = True
    if any(minimal.get(key + '_details_omitted') for key in advisory_keys):
        minimal['advisory_note'] = 'Additional risk/adherence warning details were not supplied; their omission does not mean no warning.'
    return minimal
