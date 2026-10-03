"""On-demand, bounded CRT lineage. No orders, inferred trades or shared member state.

A body-purging assigned candle can be selected as another CRT anchor. Its
subsequent validity and objectives are evaluated independently of its parent.
This is a tree of evidenced relations, never an assertion that lower Model 1
and higher Super Soup are interchangeable identities.
"""
from copy import deepcopy
from hashlib import sha256
import json

from gbop_voice_web.candle_evidence import (
    ASSIGNED, interval, next_boundary, parse_time, stamp, summarize, timeframe,
)
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.super_soup_evidence import assigned_rows, model_lifecycle

VERSION = 'crt-fractal-lineage-2026-10-03'
MAX_DEPTH = 3
MAX_NODES = 12
MAX_PAGE = 4
MAX_WINDOW = 90 * 86400 + 3600
COVERAGE_KEYS = ('start_ny', 'end_ny', 'complete', 'source_resolution_seconds',
                 'bar_count', 'missing_bar_count', 'coverage_note')
CONTRACT = ('On-demand thesis/price analysis only. Each node is a separately selected CRT. '
    'A parent failure never erases its identified child or automatically invalidates that child. '
    'Name the specific range/timeframe for every objective. Same-timeframe CSD and Super Soup '
    'are separate from the node\'s assigned-timeframe Model 1 candles. Relations require actual '
    'containment, matching direction and pre-CSD evidence; they never establish identity equivalence. '
    'Missing history, gaps and unmapped timeframes stay explicit. No trade/entry/stop is inferred. '
    'The default shift recap remains chronological and never expands this tree.')


def _hash(prefix, value):
    return prefix + sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


def source_identity(asset, symbol, broker_id=None):
    if not asset or not symbol:
        raise ValueError('Lineage requires an asset and the observed broker symbol.')
    return {'asset': str(asset).upper(), 'broker_symbol': str(symbol),
            'source_namespace': 'configured_market_bridge', 'broker_id': broker_id,
            'broker_identity_status': 'reported' if broker_id else 'not_reported_by_bridge',
            'source_continuity_limit': 'A same-symbol provider change cannot be detected without a reported source identity.'}


def _source_key(source):
    return [source[k] for k in ('asset', 'source_namespace', 'broker_symbol', 'broker_id')]


def node_id(source, tf, start, parent_node_id=None):
    tf = timeframe(tf)
    # A physical candle can be a Model 1 of more than one valid parent. Keep
    # those lineage occurrences distinct without changing its candle identity.
    value = [_source_key(source), tf, start, next_boundary(start, tf)]
    if parent_node_id:
        value.append(parent_node_id)
    return _hash('crt_', value)


def _coverage(bars, start, end, step):
    value = summarize(bars, start, end, step)
    return {k: value[k] for k in COVERAGE_KEYS}


def _bounded_int(value, default, maximum, name, minimum=0):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer from {minimum} to {maximum}.')
    return value


def _objectives(bars, anchor, direction, purge, invalid_at, end, step):
    out = {}
    for key in ('midpoint', 'opposing_liquidity'):
        level = (anchor.get('midpoint') if key == 'midpoint' else
                 anchor.get('low') if direction == 'bearish' else
                 anchor.get('high') if direction == 'bullish' else None)
        row = dict(level=level, status='direction_unresolved', evidence=None)
        if direction:
            row.update(objective_identity(key, direction, anchor))
        if direction and purge is not None:
            stop = invalid_at or end
            later = [b for b in bars if purge['time'] <= b['time'] and b['time'] + step <= stop]
            same = next((b for b in later if b['time'] == purge['time'] and b['low'] <= level <= b['high']), None)
            # An ambiguous touch in the purge bar must not conceal a later
            # independently ordered delivery of the same named objective.
            hit = next((b for b in later if b['time'] > purge['time'] and b['low'] <= level <= b['high']), None) or same
            row['same_purge_bar_order_unknown'] = bool(same)
            complete = _coverage(bars, purge['time'], stop, step)['complete']
            row['status'] = ('same_purge_bar_order_unresolved' if hit and hit['time'] == purge['time'] else
                             'touch_in_invalidating_bar_order_unresolved' if hit and hit['time'] + step == invalid_at else
                             'observed_after_purge' if hit else
                             'unverified_incomplete_coverage' if not complete else
                             'not_observed_before_invalidation' if invalid_at else 'not_observed_by_cutoff')
            if hit:
                row['evidence'] = interval(hit, step)
        out[key] = row
    return out


def _model_fact(row, parent, mapped, step):
    if not row['complete']:
        return None
    side = ('buy' if row['open'] <= parent['high'] < row['close'] else
            'sell' if row['close'] < parent['low'] <= row['open'] else None)
    if side is None:
        return None
    level = parent['high'] if side == 'buy' else parent['low']
    purge = next(b for b in row['_bars']
                 if (b['high'] > level if side == 'buy' else b['low'] < level))
    return {'identity': 'Model 1 candle', 'timeframe': mapped,
            'bar_open_ny': row['start_ny'], 'bar_close_ny': row['end_ny'],
            'identified_at_ny': row['end_ny'], 'complete': True,
            **{k: row[k] for k in ('open', 'high', 'low', 'close')},
            'direction': 'bearish' if side == 'buy' else 'bullish',
            'purged_side': side, 'purged_level': level,
            'purged_range_start_ny': parent['start_ny'], 'purged_range_end_ny': parent['end_ny'],
            'body_cross_and_close_through_level': True,
            'purge_source_interval': interval(purge, step),
            'source_resolution_seconds': step, 'exact_tick_time_known': False}


class _Review:
    """Request-local evidence only; no cache or member-global graph selection."""
    def __init__(self, bars, start, end, tf, step, source, as_of):
        self.start, self.requested_end, self.end = start, end, min(end, as_of)
        self.tf, self.step, self.source = timeframe(tf), step, source
        if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
            raise ValueError('Source resolution must be a positive integer.')
        if not 0 < end - start <= MAX_WINDOW:
            raise ValueError('Request a positive lineage window no longer than 90 days.')
        self.bars = sorted((deepcopy(b) for b in bars
                            if start <= b['time'] and b['time'] + step <= self.end), key=lambda b: b['time'])
        if len({b['time'] for b in self.bars}) != len(self.bars):
            raise ValueError('Duplicate source timestamps cannot establish lineage.')
        self.scope = _hash('fractal_', [VERSION, _source_key(source), self.tf, start, end])
        self.cache = {}

    def analyze(self, start, tf, parent=None, identity=None, path=()):
        nid = node_id(self.source, tf, start, parent['node_id'] if parent else None)
        if nid in self.cache:
            return self.cache[nid]
        anchor_end = next_boundary(start, tf)
        source = [b for b in self.bars if b['time'] >= start]
        anchor = summarize(source, start, min(anchor_end, max(start, self.end)), self.step)
        anchor.update(timeframe=tf, end_ny=stamp(anchor_end))
        if anchor_end > self.end:
            anchor.update(complete=False, forming=True)
        mapped = ASSIGNED.get(tf)
        result = {'node_id': nid, 'candle_id': node_id(self.source, tf, start), 'parent_node_id': parent['node_id'] if parent else None,
                  'node_path': list(path), 'anchor': anchor, 'assigned_timeframe': mapped,
                  'model1_identity_in_parent': deepcopy(identity), 'source': self.source,
                  'status': 'insufficient_closed_anchor', 'observed_direction': None,
                  'invalidated_at_ny': None, 'range_validity': 'unverified_incomplete_anchor',
                  'source_coverage': {'anchor': {k: anchor[k] for k in COVERAGE_KEYS},
                                      'observation': _coverage(source, min(anchor_end, self.end), self.end, self.step)},
                  'same_timeframe_lifecycle': {'status': 'not_applicable_without_parent_model1_identity'},
                  'objectives': {}, 'identified_child_count': 0,
                  'child_discovery_status': 'unverified_incomplete_anchor',
                  'reviewed_through_ny': stamp(self.end), 'execution_status': 'not_assessed'}
        result['_models'] = []
        self.cache[nid] = result
        if not anchor['complete']:
            return result
        duration = next_boundary(anchor_end, tf) - anchor_end
        if duration < self.step or duration % self.step:
            result.update(status='resolution_unavailable', child_discovery_status='resolution_unavailable')
            return result
        rows = assigned_rows(source, anchor_end, self.end, tf, self.step)
        invalid_row = next((r for r in rows if r['complete'] and not anchor['low'] <= r['close'] <= anchor['high']), None)
        invalid_at = invalid_row['_end'] if invalid_row else None
        covered = _coverage(source, anchor_end, invalid_at or self.end, self.step)
        result['source_coverage']['validity_window'] = covered
        result.update(invalidated_at_ny=stamp(invalid_at) if invalid_at else None,
                      range_validity=('invalidated_by_observed_close' if invalid_at else
                                      'no_invalidation_in_available_closed_candles' if covered['complete'] else
                                      'unverified_missing_or_forming_candles'),
                      invalidating_close_is_first_verified=bool(invalid_at and covered['complete']))
        following = [b for b in source if anchor_end <= b['time'] and b['time'] + self.step <= (invalid_at or self.end)]
        buy = next((b for b in following if b['high'] > anchor['high']), None)
        sell = next((b for b in following if b['low'] < anchor['low']), None)
        purge = min((b for b in (buy, sell) if b is not None), key=lambda b: b['time'], default=None)
        direction = ('bearish' if purge is buy else 'bullish') if purge else None
        if buy and sell and buy['time'] == sell['time']:
            direction = None
        result.update(status='invalidated_by_close' if invalid_at else 'range_sweep_candidate' if purge else
                      'no_sweep_observed' if covered['complete'] else 'incomplete_observation_window',
                      observed_direction=direction,
                      objectives=_objectives(source, anchor, direction, purge, invalid_at, self.end, self.step))
        if identity:
            # Deliberately no ancestor invalidation cutoff. These are this node's
            # own same-timeframe facts, not reassigned parent-range outcomes.
            life = model_lifecycle(identity, parent['anchor'], rows, self.end, self.step)
            soup = deepcopy(life['super_soup'])
            for key in ('parent_range_objectives', 'parent_function_outcome'):
                soup.pop(key, None)
            result['same_timeframe_lifecycle'] = {
                'status': 'assessed', 'timeframe': tf, 'csd': life['csd'], 'super_soup': soup,
                'model1_crt_invalidating_close': life['model1_crt_invalidating_close'],
                'source_coverage_complete': life['source_coverage_complete'],
                'body_reference_retest_after_csd': life['body_reference_retest_after_csd'],
                'following_candles': life['following_candles'][:4],
                'next_detail_start_ny': (life['following_candles'][4]['bar_open_ny']
                                         if len(life['following_candles']) > 4 else life['next_detail_start_ny']),
                'following_detail_tool': 'inspect_market_fractal_node with this exact root/path/scope and following_from_ny',
                'ancestor_invalidation_limits_this_node': False}
        if not mapped:
            result['child_discovery_status'] = 'assigned_timeframe_required'
            return result
        mapped_duration = next_boundary(anchor_end, mapped) - anchor_end
        if mapped_duration < self.step or mapped_duration % self.step:
            result['child_discovery_status'] = 'resolution_unavailable'
            return result
        child_rows = assigned_rows(source, anchor_end, invalid_at or self.end, mapped, self.step)
        result['_models'] = [m for r in child_rows if (m := _model_fact(r, anchor, mapped, self.step))]
        result['identified_child_count'] = len(result['_models'])
        child_complete = all(r['complete'] for r in child_rows)
        result['child_discovery_status'] = ('identified' if result['_models'] else
            'not_observed_in_complete_window' if child_complete and self.end > anchor_end else
            'unverified_missing_or_forming_candles')
        result['child_observation_complete'] = child_complete
        return result

    def relation(self, parent, child, identity):
        start, end = parse_time(identity['bar_open_ny']), parse_time(identity['bar_close_ny'])
        before = _coverage(self.bars, parse_time(parent['anchor']['end_ny']), end, self.step)
        inv = parent['invalidated_at_ny']
        eligibility = ('unverified_missing_parent_history' if not before['complete'] else
                       'at_parent_invalidating_close' if inv and parse_time(inv) == end else
                       'formed_within_parent_valid_window')
        relation = {'parent_node_id': parent['node_id'], 'child_node_id': child['node_id'],
                    'kind': 'assigned_body_purge_selected_as_child_crt',
                    'lineage_status': 'verified_body_purge_identity', 'eligibility': eligibility,
                    'parent_objectives_ref': parent['node_id'] + '.objectives',
                    'child_objectives_ref': child['node_id'] + '.objectives',
                    'parent_invalidation_propagates_to_child': False,
                    'identity_equivalence_established': False}
        life = parent['same_timeframe_lifecycle']
        csd = life.get('csd', {}).get('evidence')
        csd_end = parse_time(csd['bar_close_ny']) if csd else None
        soup = life.get('super_soup', {})
        event = soup.get('event')
        aligned = bool(parent['model1_identity_in_parent'] and
                       parent['model1_identity_in_parent']['direction'] == identity['direction'])
        contains = bool(event and parse_time(event['bar_open_ny']) <= start and end <= parse_time(event['bar_close_ny']))
        status = ('not_assessed_without_parent_model1_identity' if not parent['model1_identity_in_parent'] else
                  'ineligible_opposite_direction' if not aligned else
                  'ineligible_after_parent_csd' if csd_end and start >= csd_end else
                  'same_parent_csd_close_order_unresolved' if csd_end == end else
                  'unverified_parent_coverage' if not before['complete'] or
                      (not event and not life.get('source_coverage_complete')) else
                  'contained_aligned_pre_csd_purge' if contains and soup.get('pre_csd') else
                  'awaiting_or_missing_parent_same_timeframe_soup')
        relation['higher_timeframe_super_soup_relation'] = {
            'status': status, 'direction_aligned': aligned, 'contained_in_observed_soup_candle': contains,
            'parent_csd_confirmed_at_ny': stamp(csd_end) if csd_end else None,
            'parent_csd_status': life.get('csd', {}).get('status', 'not_assessed'),
            'parent_pre_csd_window_start_ny': parent['anchor']['end_ny'],
            'parent_super_soup_event': deepcopy(event),
            'child_purge_source_interval': identity['purge_source_interval'],
            'not_identity_equivalence': True}
        return relation

    def focus(self, path, expected_node_id):
        root = self.analyze(self.start, self.tf)
        current, ancestors, relations = root, [], []
        if not isinstance(path, (list, tuple)) or len(path) > MAX_DEPTH:
            raise ValueError(f'node_path must contain at most {MAX_DEPTH} selected child opening timestamps.')
        for i, opening in enumerate(path):
            selected = parse_time(opening)
            identity = next((m for m in current['_models'] if parse_time(m['bar_open_ny']) == selected), None)
            if identity is None:
                raise ValueError('Selected child is not an identified Model 1 of this exact parent/source/window.')
            ancestors.append(current)
            child = self.analyze(selected, identity['timeframe'], current, identity, path[:i+1])
            relations.append(self.relation(current, child, identity))
            current = child
        if expected_node_id and current['node_id'] != expected_node_id:
            raise ValueError('Node ID does not match this asset/source/root/path. Do not reuse cross-scope lineage.')
        return root, current, ancestors, relations


def _public(node, detail=False):
    value = {k: deepcopy(v) for k, v in node.items() if not k.startswith('_')}
    if not detail:
        value['anchor'] = {k: v for k, v in value['anchor'].items() if k in {
            'start_ny', 'end_ny', 'timeframe', 'complete', 'forming', 'open', 'high', 'low', 'close', 'midpoint'}}
        life = value['same_timeframe_lifecycle']
        if life.get('status') == 'assessed':
            soup = life['super_soup']
            value['same_timeframe_lifecycle'] = {
                'status': 'assessed', 'timeframe': life['timeframe'],
                'csd': life['csd'],
                'super_soup': {k: soup[k] for k in ('structure_status', 'structural_quality',
                    'structure_known_at_ny', 'local_crt_outcome', 'local_function_outcome',
                    'local_crt_invalidated_at_ny') if k in soup},
                'source_coverage_complete': life['source_coverage_complete'],
                'ancestor_invalidation_limits_this_node': False}
    return value


def review_fractal(bars, start, end, tf, step, asset, symbol, *, broker_id=None,
                   as_of=None, node_path=None, expected_node_id=None, expected_scope_id=None, max_depth=1,
                   page_size=3, page_from_ny=None, following_from_ny=None, detail=False):
    """Explicit root + verified opening path make expansion stateless and scoped.

    Pages contain at most four siblings and twelve analyzed nodes. Paths can
    select ANY evidenced child, including one beyond a returned sibling page.
    """
    max_depth = _bounded_int(max_depth, 1, MAX_DEPTH, 'max_depth')
    page_size = _bounded_int(page_size, 3, MAX_PAGE, 'page_size', 1)
    source = source_identity(asset, symbol, broker_id)
    ctx = _Review(bars, start, end, tf, step, source, end if as_of is None else as_of)
    if expected_scope_id and expected_scope_id != ctx.scope:
        raise ValueError('Scope ID does not match this asset/source/root/cutoff. Do not reuse cross-scope lineage.')
    if following_from_ny is not None and not detail:
        raise ValueError('Use inspect_market_fractal_node to page same-timeframe sequel candles.')
    path = [] if node_path is None else node_path
    root, focus, ancestors, relations = ctx.focus(path, expected_node_id)
    cursor = parse_time(page_from_ny) if page_from_ny else None
    if cursor is not None and not parse_time(focus['anchor']['end_ny']) <= cursor <= ctx.end:
        raise ValueError('Child page cursor is outside the selected node observation window.')
    nodes, queue = [], [(focus, 0, cursor)]
    while queue and len(nodes) < MAX_NODES:
        current, depth, first = queue.pop(0)
        item = _public(current, detail=detail and current is focus)
        if detail and current is focus:
            opening = parse_time(current['anchor']['end_ny'])
            requested = parse_time(following_from_ny) if following_from_ny else opening
            if following_from_ny and not opening <= requested <= ctx.end:
                raise ValueError('Sequel cursor is outside this node observation window.')
            while opening < requested:
                opening = next_boundary(opening, current['anchor']['timeframe'])
            if opening != requested:
                raise ValueError('Sequel cursor must preserve this node candle boundaries.')
            stop = opening
            for _ in range(page_size):
                stop = next_boundary(stop, current['anchor']['timeframe'])
            rows = assigned_rows(ctx.bars, opening, min(stop, ctx.end), current['anchor']['timeframe'], step)
            candles = [{k: row[k] for k in ('start_ny', 'end_ny', 'complete', 'forming',
                        'open', 'high', 'low', 'close') if k in row} for row in rows]
            next_open = stamp(stop) if stop < ctx.end else None
            item['following_candle_page'] = {'timeframe': current['anchor']['timeframe'],
                'node_id': current['node_id'], 'scope_id': ctx.scope, 'candles': candles,
                'next_following_from_ny': next_open,
                'detail_tool': 'inspect_market_fractal_node',
                'limits': 'Factual sequel OHLC only. Gaps do not establish absent events or continuity.'}
            life = item['same_timeframe_lifecycle']
            life.pop('following_candles', None)
            life.pop('next_detail_start_ny', None)
        candidates = [m for m in current['_models'] if first is None or parse_time(m['bar_open_ny']) >= first]
        page = candidates[:page_size]
        item['children'] = []
        for model in page:
            child_start = parse_time(model['bar_open_ny'])
            child_path = current['node_path'] + [model['bar_open_ny']]
            child_id = node_id(source, model['timeframe'], child_start, current['node_id'])
            child_ref = {'node_id': child_id, 'candle_id': node_id(source, model['timeframe'], child_start), 'parent_node_id': current['node_id'], 'scope_id': ctx.scope, 'anchor_start_ny': model['bar_open_ny'],
                         'anchor_timeframe': model['timeframe'], 'node_path': child_path,
                         'expansion_status': 'depth_limit', 'identity': deepcopy(model)}
            if depth < max_depth and len(path) + depth < MAX_DEPTH and len(nodes) + len(queue) + 1 < MAX_NODES:
                child = ctx.analyze(child_start, model['timeframe'], current, model, child_path)
                child_ref['expansion_status'] = 'expanded'
                relations.append(ctx.relation(current, child, model))
                queue.append((child, depth + 1, None))
            elif depth < max_depth:
                child_ref['expansion_status'] = 'node_or_lineage_depth_limit'
            item['children'].append(child_ref)
        item['next_child_from_ny'] = candidates[page_size]['bar_open_ny'] if len(candidates) > page_size else None
        item['detail_tool'] = 'inspect_market_fractal_node'
        nodes.append(item)
    fingerprint = _hash('evidence_', [ctx.scope, ctx.end, step, ctx.bars])
    return {'version': VERSION, 'scope_id': ctx.scope, 'evidence_id': fingerprint,
            'root_node_id': root['node_id'], 'focus_node_id': focus['node_id'],
            'root_anchor': {'start_ny': stamp(start), 'timeframe': ctx.tf},
            'source': source, 'requested_through_ny': stamp(end), 'reviewed_through_ny': stamp(ctx.end),
            'cutoff_status': 'future_request_clamped_to_as_of' if end > ctx.end else 'requested_cutoff',
            'ancestor_path': [_public(n) for n in ancestors], 'nodes': nodes, 'relations': relations,
            'limits': {'max_depth': max_depth, 'max_nodes': MAX_NODES, 'page_size': page_size,
                       'max_lineage_depth': MAX_DEPTH, 'max_window_days': 90},
            'response_contract': CONTRACT,
            'expansion_instruction': 'Preserve exact root asset, anchor, timeframe and through_ny. '
                'Select returned node_path and expected_node_id with inspect_market_fractal_node. '
                'Continue siblings using next_child_from_ny. Missing mappings need an explicit supported rule, not a guessed M1.'}
