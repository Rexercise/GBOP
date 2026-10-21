"""Saved setup evidence and independent journal review dimensions.

No trading verdict is calculated here. Source observations, member adherence,
and reported results cannot prove one another. Evidence values are copied whole
or explicitly marked omitted; timestamps/prices are never shortened.
"""
from copy import deepcopy
import json
import math


RANGE_FIELDS = (
    'anchor_start_ny', 'anchor_timeframe', 'label', 'role', 'play',
    'assigned_timeframe', 'status', 'setup_status', 'direction_observed',
    'observed_direction', 'direction', 'direction_scope',
    'invalidated_at_ny', 'validity_evidence_through_ny', 'hourly_evidence_conflict',
    'coverage_complete', 'double_purge', 'events', 'objectives', 'midpoint',
    'opposing_liquidity', 'primary_target', 'first_purge', 'anchor',
    'observation_coverage', 'young_lefty_context', 'directional_outcome',
    'outcome', 'delivery_continuation', 'physical_path_audit',
)
MAX_RANGES = 12
MAX_FIELD_CHARS = 24000
MAX_HISTORY_CHARS = 7000


def _object(value):
    return value if isinstance(value, dict) else {}


def _purge_chronology(value):
    """Keep source/assigned/own-timeframe order; do not repeat derived prose."""
    if not isinstance(value, dict) or 'sequence' not in value:
        return value
    keep = ('status', 'observed', 'range_start_ny', 'range_timeframe', 'review_cutoff_ny',
            'source_resolution_seconds', 'exact_tick_time_known', 'developing',
            'confirmation_status', 'confirmed_at_ny', 'original_first_purged_side',
            'reverse_direction', 'original_completion_preserved', 'range_invalidated_at_ny',
            'validity_cutoff_ny', 'sequence')
    result = {key: value[key] for key in keep if key in value}
    original = _object(value.get('original_outcome'))
    result['original_outcome'] = {key: original[key] for key in
        ('direction', 'status', 'range_invalidated_at_ny', 'delivery_before_later_invalidation') if key in original}
    return result


def snapshot_ranges(review):
    """Project each source range separately; bound storage without fake precision."""
    rows = ((review.get('shift_story') or {}).get('ranges')
            or review.get('ranges') or [review])
    history, omissions, used = [], [], 0
    for index, source in enumerate(rows[:MAX_RANGES]):
        if not isinstance(source, dict):
            omissions.append(f'ranges.{index}: invalid source')
            continue
        row = {}
        for key in RANGE_FIELDS:
            if key not in source:
                continue
            value = _purge_chronology(source[key]) if key == 'double_purge' else source[key]
            if value != source[key]:
                omissions.append(f'ranges.{index}.{key}: chronology projection; additional source details omitted')
            try:
                size = len(json.dumps(value, ensure_ascii=True, allow_nan=False)) + len(key) + 8
            except (ValueError, TypeError, OverflowError):
                omissions.append(f'ranges.{index}.{key}: invalid source')
                continue
            if size > MAX_FIELD_CHARS or used + size > MAX_HISTORY_CHARS:
                omissions.append(f'ranges.{index}.{key}: size limit')
                continue
            row[key] = deepcopy(value)
            used += size
        if row:
            history.append(row)
    if len(rows) > MAX_RANGES:
        omissions.append(f'ranges.{MAX_RANGES} onward: range limit')
    return {'range_history': history, 'range_history_source_count': len(rows),
            'range_history_omissions': omissions,
            'range_history_note': 'Copied chronology fields for separate ranges; omitted or unreported evidence is unknown. No setup grade or member execution inferred.'}


def review_dimensions(row):
    """Read-only separation of source evidence, explicit adherence and outcome."""
    meta = _object(row.get('metadata'))
    review = _object(meta.get('market_review'))
    ranges = review.get('range_history')
    legacy = not isinstance(ranges, list)
    if legacy:
        ranges = _object(review.get('market_facts')).get('range_outcomes') or []
    if not isinstance(ranges, list):
        ranges = []
    summaries = []
    for source in ranges[:MAX_RANGES]:
        if not isinstance(source, dict):
            continue
        # Detailed objective/purge sequences remain in the paged saved record.
        summary = {key: deepcopy(source[key]) for key in
            ('anchor_start_ny', 'anchor_timeframe', 'label', 'role', 'play',
             'status', 'setup_status', 'direction_observed', 'observed_direction',
             'invalidated_at_ny', 'coverage_complete')
            if key in source and (source[key] is None or isinstance(source[key], (bool, int, float))
                                  or isinstance(source[key], str) and len(source[key]) <= 512)}
        anchor = _object(source.get('anchor'))
        summary.setdefault('anchor_start_ny', anchor.get('start_ny'))
        summary['saved_detail_available'] = any(key in source for key in
            ('objectives', 'directional_outcome', 'events', 'double_purge', 'physical_path_audit'))
        summaries.append(summary)
    adherence = meta.get('adherence') or row.get('rule_adherence')
    reported = meta.get('reported_outcome')
    result_r = row.get('result_r')
    if isinstance(result_r, bool) or not isinstance(result_r, (int, float)) or not math.isfinite(result_r):
        result_r = None
    return {
        'setup_validity': {'assessment': 'not_assessed', 'ranges': summaries,
            'scope_id': review.get('scope_id'), 'evidence_id': review.get('evidence_id'),
            'association_status': _object(meta.get('provenance')).get('association_status'),
            'history_incomplete': bool(review.get('range_history_omissions')) or legacy,
            'basis': 'Saved market observations only. Delivery, invalidation and double purge retain their own chronology; none establishes the member outcome or a setup grade.'},
        'plan_adherence': {'value': adherence if adherence in ('yes', 'no', 'partial', 'unknown') else 'unknown',
            'basis': 'Explicit recorded adherence only; never inferred from profit, loss or setup delivery.'},
        'actual_outcome': {'reported_outcome': reported if isinstance(reported, str) else 'unknown', 'result_r': result_r,
            'record_status': _object(row.get('trade_facts')).get('status'),
            'basis': 'Member-reported outcome and saved trade status. Unknown R remains null; outcome wording may predate the latest status.'},
    }
