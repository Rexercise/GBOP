"""Bounded, read-only relationships between explicitly scoped market reviews.

This is not a second trading engine. It names geometric facts from complete
anchors and copies only matching, verified engine edges. A missing edge, missing
history or unknown source never establishes that a market relationship is absent.
"""
from itertools import combinations
from math import isfinite

from gbop_voice_web.candle_evidence import next_boundary, parse_time, timeframe


MAX_MEMBERS = 4
MAX_FINDINGS = 8
_LIMITS = (
    'Every range retains its own objectives and invalidation; neither invalidates the other.',
    'Geometry and chronology establish no causal draw, Butterfly, SMT, probability or member execution.',
    'Unverified means insufficient supplied evidence, not an absent setup or relationship.',
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value, limit):
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def _time(value):
    try:
        return parse_time(value) if isinstance(value, str) and value else None
    except (ValueError, TypeError, OverflowError):
        return None


def _tf(value):
    try:
        return timeframe(value) if isinstance(value, str) and value else None
    except (ValueError, TypeError):
        return None


def _price(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        return False


def _source(source):
    source = _dict(source)
    return {key: source.get(key) for key in ('asset', 'source_namespace', 'broker_id')} | {
        'symbol': source.get('symbol', source.get('broker_symbol'))}


def _source_status(left, right):
    keys = ('asset', 'symbol', 'source_namespace')
    if any(not isinstance(s.get(k), str) or not s[k] for s in (left, right) for k in keys):
        return 'unverified_source_identity'
    if left['source_namespace'] != 'configured_market_bridge' or right['source_namespace'] != 'configured_market_bridge':
        return 'unverified_source_namespace'
    if any(left[k] != right[k] for k in keys):
        return 'incomparable_sources'
    if left.get('broker_id') != right.get('broker_id'):
        return 'incomparable_broker_identity'
    return 'same_configured_feed' if not left.get('broker_id') else 'same_reported_source'


def _story(review):
    if isinstance(review.get('shift_story'), dict):
        return review['shift_story']
    return review if 'hourly_progression' in review and 'range_transitions' in review else {}


def _anchor_key(anchor):
    anchor = _dict(anchor)
    return _time(anchor.get('start_ny')), _tf(anchor.get('timeframe'))


def _select(member):
    """Never substitute a default shift range for the requested anchor."""
    scope, review = _dict(member.get('scope')), _dict(member.get('review'))
    if review.get('source') and _source_status(_source(member.get('source')), _source(review['source'])) not in {
            'same_configured_feed', 'same_reported_source'}:
        return None, 'review_source_does_not_match_member'
    key = _time(scope.get('anchor_start_ny')), _tf(scope.get('anchor_timeframe'))
    cutoff = _time(scope.get('through_ny'))
    if None in key or cutoff is None:
        return None, 'explicit_anchor_and_cutoff_required'
    candidates = [(review, 'review')]
    candidates += [(r, 'review.shift_story.ranges') for r in _rows(_story(review).get('ranges'), 4)]
    candidates += [(_dict(r.get('evidence')), 'review.observations.evidence')
                   for r in _rows(review.get('observations'), 2)]
    candidates += [(r, 'review.nodes') for r in _rows(review.get('nodes'), 12)]
    candidates += [(r, 'review.ancestor_path') for r in _rows(review.get('ancestor_path'), 3)]
    matches = [(r, path) for r, path in candidates if _anchor_key(r.get('anchor')) == key]
    if not matches:
        return None, 'requested_anchor_not_present_in_review'
    row, path = matches[0]
    anchor = row['anchor']
    # Conflicting duplicate identities are not resolved by arbitrary list order.
    signature = lambda a: tuple(a.get(k) for k in ('end_ny', 'complete', 'high', 'low'))
    if any(signature(r['anchor']) != signature(anchor) for r, _ in matches[1:]):
        return None, 'conflicting_anchor_evidence'
    end = _time(anchor.get('end_ny'))
    try:
        expected_end = next_boundary(key[0], key[1])
    except (ValueError, OverflowError):
        return None, 'invalid_anchor_boundary'
    if end != expected_end or end > cutoff:
        return None, 'anchor_boundary_outside_requested_scope'
    if anchor.get('complete') is not True or anchor.get('forming') is True:
        return None, 'unverified_incomplete_anchor'
    if not _price(anchor.get('low')) or not _price(anchor.get('high')) or anchor['low'] > anchor['high']:
        return None, 'unverified_anchor_prices'
    if row.get('source') and _source_status(_source(member.get('source')), _source(row['source'])) not in {
            'same_configured_feed', 'same_reported_source'}:
        return None, 'anchor_source_does_not_match_member'
    return {'row': row, 'anchor': anchor, 'start': key[0], 'end': end, 'tf': key[1],
            'cutoff': cutoff, 'path': path}, None


def _reference(selected):
    anchor = selected['anchor']
    return {'anchor_start_ny': anchor['start_ny'], 'anchor_end_ny': anchor['end_ny'],
            'anchor_timeframe': selected['tf']}


def _fact(kind, status='observed', **evidence):
    return {'kind': kind, 'status': status, 'evidence': evidence}


def _event_limit(members, selected):
    if selected[0]['cutoff'] != selected[1]['cutoff']:
        return 'different_review_cutoffs_inhibit_event_comparison'
    for member, anchor in zip(members, selected):
        review = member['review']
        reported = review.get('reviewed_through_ny') or _story(review).get('end_ny')
        if not reported and 'anchor' in review:
            reported = _dict(review.get('observation_coverage')).get('end_ny')
        if reported is not None and _time(reported) != anchor['cutoff']:
            return 'review_cutoff_does_not_match_member_scope'
    return None


def _transitions(members, selected):
    """Require the matching closed-hour ledger, not just a transition-shaped row."""
    if any(a['tf'] != 'H1' for a in selected):
        return _fact('shift_ledger_transition', 'unverified', reason='H1_shift_ledger_not_applicable')
    for owner, member in enumerate(members):
        story = _story(member['review'])
        # A ledger from another snapshot must not supply prices/edges for these
        # anchors merely because their opening timestamps happen to match.
        ranges = _rows(story.get('ranges'), 4)
        if not all(any(_anchor_key(row.get('anchor')) == (item['start'], item['tf']) and
                       all(_dict(row.get('anchor')).get(k) == item['anchor'].get(k)
                           for k in ('low', 'high', 'complete')) for row in ranges) for item in selected):
            continue
        for edge in _rows(story.get('range_transitions'), 3):
            for first, second in ((0, 1), (1, 0)):
                before, after = selected[first], selected[second]
                if (_time(edge.get('from_anchor_ny')), _time(edge.get('to_anchor_ny'))) != (before['start'], after['start']):
                    continue
                confirmed = _time(edge.get('confirmed_at_ny'))
                close = edge.get('close')
                if (edge.get('reason') != 'hourly_close_outside_selected_range' or
                        confirmed != after['end'] or confirmed > after['cutoff'] or
                        not _price(close) or close != after['anchor'].get('close') or
                        before['anchor']['low'] <= close <= before['anchor']['high']):
                    continue
                verified = any(row.get('complete') is True and
                    row.get('status') == 'invalidated_by_hourly_close' and
                    _time(row.get('anchor_start_ny')) == before['start'] and
                    _time(row.get('candle_start_ny')) == after['start'] and
                    _time(row.get('candle_end_ny')) == confirmed and row.get('close') == close
                    for row in _rows(story.get('hourly_progression'), 3))
                if verified:
                    return _fact('shift_ledger_transition', from_context_id=members[first]['context_id'],
                        to_context_id=members[second]['context_id'], confirmed_at_ny=edge['confirmed_at_ny'],
                        reason=edge['reason'], source_evidence_id=members[owner]['evidence_id'],
                        source_path=('review.shift_story' if 'shift_story' in member['review'] else 'review') +
                                    '.range_transitions + hourly_progression')
    return _fact('shift_ledger_transition', 'unverified', reason='matching_verified_shift_transition_not_supplied')


def _lineage(members, selected):
    """Only the engine's actual edge and exact endpoint identities can link CRTs."""
    for owner, member in enumerate(members):
        review = member['review']
        if review.get('version') != 'crt-fractal-lineage-2026-10-03':
            continue
        if _source_status(_source(member['source']), _source(review.get('source'))) not in {
                'same_configured_feed', 'same_reported_source'}:
            continue
        nodes = _rows(review.get('nodes'), 12) + _rows(review.get('ancestor_path'), 3)
        by_id = {node.get('node_id'): node for node in nodes if isinstance(node.get('node_id'), str)}
        for edge in _rows(review.get('relations'), 12):
            if edge.get('kind') != 'assigned_body_purge_selected_as_child_crt' or edge.get('lineage_status') != 'verified_body_purge_identity':
                continue
            parent, child = by_id.get(edge.get('parent_node_id')), by_id.get(edge.get('child_node_id'))
            if not parent or not child or child.get('parent_node_id') != parent.get('node_id'):
                continue
            if any(_source_status(_source(member['source']), _source(node.get('source'))) not in {
                    'same_configured_feed', 'same_reported_source'} or
                    _time(node.get('reviewed_through_ny')) != selected[owner]['cutoff']
                    for node in (parent, child)):
                continue
            for first, second in ((0, 1), (1, 0)):
                if (_anchor_key(parent.get('anchor')), _anchor_key(child.get('anchor'))) != (
                        (selected[first]['start'], selected[first]['tf']),
                        (selected[second]['start'], selected[second]['tf'])):
                    continue
                # Do not import a matching timestamp from conflicting node prices.
                if any(node['anchor'].get(k) != selected[index]['anchor'].get(k)
                       for node, index in ((parent, first), (child, second)) for k in ('low', 'high', 'complete')):
                    continue
                return _fact('engine_verified_lineage', parent_context_id=members[first]['context_id'],
                    child_context_id=members[second]['context_id'], parent_node_id=parent['node_id'],
                    child_node_id=child['node_id'], engine_kind=edge['kind'],
                    lineage_status=edge['lineage_status'], eligibility=(edge.get('eligibility')
                        if edge.get('eligibility') in {'unverified_missing_parent_history',
                            'at_parent_invalidating_close', 'formed_within_parent_valid_window'}
                        else 'unverified_engine_eligibility'),
                    source_evidence_id=members[owner]['evidence_id'],
                    parent_invalidation_propagates_to_child=False, identity_equivalence_established=False)
    return _fact('engine_verified_lineage', 'unverified', reason='matching_engine_relation_not_supplied')


def _pair(left, right):
    members = (left, right)
    result = {'context_ids': [m.get('context_id') for m in members],
              'evidence_ids': [m.get('evidence_id') for m in members],
              'status': 'unverified', 'kind': 'range_pair', 'evidence': [], 'limits': list(_LIMITS)}
    def unavailable(kind, reason, status='unverified'):
        result['status'] = status
        result['evidence'] = [_fact(kind, status, reason=reason)]
        return result
    if any(m.get('ok') is not True or not isinstance(m.get('review'), dict) for m in members):
        return unavailable('member_evidence', 'one_or_more_members_unavailable')
    if any(not isinstance(m.get(k), str) or not m[k] for m in members for k in ('context_id', 'evidence_id')):
        return unavailable('member_identity', 'explicit_context_and_evidence_ids_required')
    scopes = [_dict(m.get('scope')) for m in members]
    sources = [_source(m.get('source')) for m in members]
    if any(not scope.get('asset') or scope.get('asset') != source.get('asset') for scope, source in zip(scopes, sources)):
        return unavailable('source_identity', 'source_asset_does_not_match_scope')
    if scopes[0]['asset'] != scopes[1]['asset']:
        result['limits'].append('Cross-asset SMT requires review_market_smt with the same explicit anchor, timeframe and cutoff.')
        return unavailable('cross_asset_requires_paired_smt', 'cross_asset_prices_are_not_geometrically_comparable', 'no_supported_relation')
    source_status = _source_status(*sources)
    if source_status not in {'same_configured_feed', 'same_reported_source'}:
        return unavailable('source_identity', source_status)
    if source_status == 'same_configured_feed':
        result['limits'].append('Same configured feed/symbol only; broker continuity is unverified and no cross-provider price claim is supported.')
    extracted = [_select(m) for m in members]
    if any(reason for _, reason in extracted):
        return unavailable('anchor_evidence', '; '.join(reason for _, reason in extracted if reason))
    selected = [item for item, _ in extracted]
    a, b = selected
    if ((a['start'], a['tf'], a['cutoff']) == (b['start'], b['tf'], b['cutoff']) and
            left['evidence_id'] == right['evidence_id'] and
            any(a['anchor'].get(key) != b['anchor'].get(key) for key in ('low', 'high', 'open', 'close'))):
        return unavailable('member_identity', 'conflicting_prices_for_same_snapshot_identity')
    facts = result['evidence']
    refs = [_reference(item) for item in selected]
    if (a['start'], a['tf']) == (b['start'], b['tf']):
        same_snapshot = a['cutoff'] == b['cutoff'] and left['evidence_id'] == right['evidence_id']
        facts.append(_fact('same_range_snapshot' if same_snapshot else 'same_range_distinct_snapshots',
                           references=refs, through_ny=[scope['through_ny'] for scope in scopes]))
    elif a['end'] <= b['start'] or b['end'] <= a['start']:
        earlier = 0 if a['end'] <= b['start'] else 1
        facts.append(_fact('chronological_independent_ranges', earlier_context_id=members[earlier]['context_id'],
                           later_context_id=members[1-earlier]['context_id'], references=refs))
    lo, hi = max(a['anchor']['low'], b['anchor']['low']), min(a['anchor']['high'], b['anchor']['high'])
    bounds = [{'context_id': member['context_id'], **ref, 'low': item['anchor']['low'], 'high': item['anchor']['high']}
              for member, ref, item in zip(members, refs, selected)]
    if lo > hi:
        facts.append(_fact('price_geometry', 'no_supported_relation', geometry='disjoint_closed_price_intervals', ranges=bounds))
    else:
        contains = [m['context_id'] for m, item, other in ((left, a, b), (right, b, a))
                    if item['anchor']['low'] <= other['anchor']['low'] and item['anchor']['high'] >= other['anchor']['high']]
        geometry = 'equal_price_intervals' if len(contains) == 2 else 'containment' if contains else 'boundary_touch' if lo == hi else 'overlap'
        facts.append(_fact('price_geometry', geometry=geometry, containing_context_ids=contains,
                           intersection={'low': lo, 'high': hi}, ranges=bounds))
    shared = [{'liquidity_side': side, 'level': a['anchor'][key], 'references': refs}
              for key, side in (('high', 'buy-side'), ('low', 'sell-side'))
              if a['anchor'][key] == b['anchor'][key]]
    if shared:
        facts.append(_fact('same_named_liquidity_level', levels=shared,
                           meaning='Exact same-source price equality only; no common causal draw is established.'))
    event_limit = _event_limit(members, selected)
    if event_limit:
        facts.append(_fact('event_comparison', 'unverified', reason=event_limit,
                           through_ny=[scope['through_ny'] for scope in scopes]))
        result['limits'].append('Different or mismatched cutoffs leave only the explicitly scoped closed-anchor geometry/chronology comparable.')
    else:
        facts.extend((_transitions(members, selected), _lineage(members, selected)))
    result['status'] = ('observed' if any(f['status'] == 'observed' for f in facts) else
                        'unverified' if any(f['status'] == 'unverified' for f in facts) else 'no_supported_relation')
    result['evidence'] = facts[:MAX_FINDINGS]
    return result


def summarize_relationships(members):
    """Return up to six pair summaries, each with at most eight typed findings.

    Members contain verified raw ``review`` values, explicit ``scope`` and
    ``source``, plus context/evidence IDs. Inputs are never mutated. Pair status
    says whether any scoped fact is observed; inspect each finding's status for
    unverified engine relationships. No-supported-relation is narrowly scoped to
    the tested kind and never means no setup exists.
    """
    if not isinstance(members, (list, tuple)) or len(members) > MAX_MEMBERS or any(not isinstance(m, dict) for m in members):
        raise ValueError('Supply at most four market review member objects.')
    return [_pair(left, right) for left, right in combinations(members, 2)]
