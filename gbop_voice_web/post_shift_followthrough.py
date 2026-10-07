"""Append-only, candle-backed outcomes after a frozen GTOP shift cutoff.

Only a context already qualified with full DOL pending at that cutoff can be
followed. Later candles cannot establish its earlier qualification, change its
parent/phase, supply a member execution, or rewrite the original shift verdict.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math

from gbop_voice_web.candle_evidence import (
    h1_anchor, interval, missing_source_intervals, parse_time, stamp, summarize,
)
from gbop_voice_web.shift_availability import shift_bounds


VERSION = 'post-shift-followthrough-v1'
TOOL_NAME = 'review_post_shift_followthrough'
MAX_WINDOW_SECONDS = 90 * 86400
CONTRACT = (
    'The frozen shift outcome is unchanged. This is a separately bounded later '
    'evidence appendix for the exact same asset, broker symbol, H1 parent and '
    'original/double-purge phase. Qualification was already known at the shift '
    'cutoff; later candles cannot establish an entry or hindsight tradeability. '
    'Stop the structural path at its first verified full delivery or invalidation. '
    'A physical touch after invalidation is not valid delivery. Missing candles '
    'leave coverage and ordering unverified; never say never. Source intervals '
    'are candle evidence, not exact tick times or member fills, exits, P/L or R.'
)


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _objective(value, source_name, level):
    # Preserve the exact cutoff status and source interval without transporting
    # a duplicate target-approach/variant tree in every continuation request.
    return {'source_objective': source_name, 'level': level,
            'status': value.get('status'),
            'evidence': deepcopy(value.get('evidence')),
            'boundary_observations': deepcopy(value.get('boundary_observations', []))}


def _frozen_contexts(review, asset, symbol):
    story = review.get('shift_story') or {}
    _, cutoff = shift_bounds(review['date_ny'], review['shift'])
    if parse_time(story.get('end_ny', '')) != cutoff:
        raise ValueError('Follow-through requires the original exact GTOP shift cutoff.')
    step = review.get('source_resolution_seconds')
    if step not in (60, 300):
        raise ValueError('Follow-through requires the verified original M1 or M5 source precision.')
    graph = story.get('chronological_context') or {}
    active = set((graph.get('at_shift_end') or {}).get('active_context_ids', []))
    rows = list(story.get('ranges', [])) + [o['evidence'] for o in review.get('observations', [])
                                          if isinstance(o.get('evidence'), dict)]
    result = {}
    for context in graph.get('contexts', []):
        if (context.get('context_id') not in active
                or context.get('status') != 'active_full_DOL_pending'
                or context.get('phase') not in ('original', 'double_purge')
                or context.get('direction') not in ('bullish', 'bearish')
                or context.get('objective_order_known') is not True
                or context.get('retired_at_ny')
                or not context.get('established_at_ny')
                or parse_time(context['established_at_ny']) > cutoff
                or not context.get('evidence_through_ny')
                or parse_time(context['evidence_through_ny']) < cutoff):
            continue
        row = next((r for r in rows if r.get('anchor', {}).get('start_ny') == context['range_id']), None)
        if not row:
            continue
        anchor = row['anchor']
        if (not anchor.get('complete') or anchor.get('timeframe') != 'H1'
                or row.get('invalidated_at_ny') or row.get('hourly_evidence_conflict')
                or parse_time(anchor['end_ny']) > cutoff
                or not all(_finite(anchor.get(k)) for k in ('open', 'high', 'low', 'close', 'midpoint'))
                or anchor['high'] <= anchor['low']):
            continue
        direction = context['direction']
        full_level = anchor['high'] if direction == 'bullish' else anchor['low']
        if context.get('remaining_DOL', {}).get('level') != full_level:
            continue
        if context['phase'] == 'double_purge':
            double = row.get('double_purge') or {}
            phase = double.get('reversal_thesis') or {}
            if (not double.get('observed') or not double.get('confirmed_at_ny')
                    or parse_time(double['confirmed_at_ny']) > cutoff
                    or phase.get('direction') != direction):
                continue
            targets = phase.get('objectives') or {}
            full_name = 'original_side'
        else:
            qualification = row.get('context_qualification') or {}
            if not qualification.get('selected_tf_return_confirmed'):
                continue
            phase = row.get('directional_outcome') or {}
            targets = phase
            full_name = 'opposing_liquidity'
        if not targets.get(full_name):
            continue
        frozen = {
            'version': VERSION, 'asset': asset, 'symbol': symbol,
            'date_ny': review['date_ny'], 'shift': review['shift'],
            'cutoff_ny': stamp(cutoff), 'source_resolution_seconds': step,
            'parent': {k: deepcopy(anchor[k]) for k in
                       ('start_ny', 'end_ny', 'timeframe', 'open', 'high', 'low', 'close', 'midpoint')},
            'context': deepcopy(context), 'phase_outcome': phase.get('status'),
            'original_directional_outcome': (row.get('directional_outcome') or {}).get('status'),
            'qualification_provenance': {
                'context_qualification': deepcopy(row.get('context_qualification', {})),
                'initiating_identity': deepcopy((row.get('directional_outcome') or {}).get('initiating_identity')),
                'first_source_return_inside': deepcopy((row.get('directional_outcome') or {}).get('first_source_return_inside')),
                'purges': [{k: deepcopy(event[k]) for k in ('kind', 'level', 'observed_price',
                    'bar_open_ny', 'bar_close_ny', 'precision_seconds', 'assigned_purge') if k in event}
                    for event in row.get('events', []) if event.get('kind') in ('buy_side_purge', 'sell_side_purge')],
                **({'double_purge_sequence': {k: deepcopy(row['double_purge'].get('sequence', {}).get(k))
                    for k in ('first_purge', 'opposing_purge', 'source_return_inside',
                              'assigned_return_inside', 'selected_timeframe_return_inside', 'known_at_ny')}}
                   if context['phase'] == 'double_purge' else {}),
            },
            'objectives': {
                'midpoint': _objective(targets.get('midpoint', {}), 'midpoint', anchor['midpoint']),
                'full_objective': _objective(targets[full_name], full_name, full_level)},
            'tradeability_at_cutoff': 'not_assessed', 'member_execution': 'not_assessed',
        }
        encoded = json.dumps(frozen, sort_keys=True, separators=(',', ':'), allow_nan=False)
        frozen['source_scope_id'] = hashlib.sha256(encoded.encode()).hexdigest()
        result[context['context_id']] = frozen
    return result


def continuation_candidates(review, asset, symbol):
    """Server-created navigation; no caller may invent a qualified context."""
    return [{
        'anchor_start_ny': value['parent']['start_ny'],
        'phase': value['context']['phase'], 'direction': value['context']['direction'],
        'full_objective_level': value['objectives']['full_objective']['level'],
        'source_scope_id': value['source_scope_id'],
    } for value in _frozen_contexts(review, asset, symbol).values()]


def _coverage(bars, start, end, step):
    value = summarize(bars, start, end, step)
    out = {k: value[k] for k in ('start_ny', 'end_ny', 'complete', 'bar_count',
        'missing_bar_count', 'source_resolution_seconds', 'coverage_note')}
    out.update(missing_source_intervals(bars, start, end, step))
    cursor = start
    for bar in bars:
        if bar['time'] < start or bar['time'] + step > end:
            continue
        if bar['time'] != cursor:
            break
        cursor += step
    out['contiguous_evidence_through_ny'] = stamp(cursor)
    return out


def _later_bars(bars, cutoff, through, step):
    rows = sorted((deepcopy(b) for b in bars if cutoff <= b['time']
                   and b['time'] + step <= through), key=lambda b: b['time'])
    seen = set()
    for bar in rows:
        opening = bar['time']
        values = [bar.get(k) for k in ('open', 'high', 'low', 'close')]
        if (type(opening) is not int or opening % step or opening in seen
                or not all(_finite(v) for v in values)
                or not bar['low'] <= min(bar['open'], bar['close'])
                <= max(bar['open'], bar['close']) <= bar['high']):
            raise ValueError('Later candle evidence is malformed or has conflicting duplicate openings.')
        seen.add(opening)
    return rows


def build_followthrough(review, bars, *, asset, symbol, anchor_start_ny, phase,
                        expected_scope_id, through, as_of, native_h1=None):
    """Pure bounded appendix; frozen input and all source bars stay unchanged."""
    if not isinstance(symbol, str) or not symbol:
        raise ValueError('Follow-through requires the verified broker symbol.')
    contexts = _frozen_contexts(review, asset, symbol)
    context_id = stamp(parse_time(anchor_start_ny)) + '/' + str(phase)
    frozen = contexts.get(context_id)
    if frozen is None:
        return {'ok': False, 'status': 'range_not_qualified_pending_at_shift_cutoff',
                'error': 'That exact range and phase had no qualified, still-pending full objective at the frozen shift cutoff.',
                'candidates': continuation_candidates(review, asset, symbol)}
    if expected_scope_id is None:
        return {'ok': True, 'asset': asset, 'symbol': symbol,
                'status': 'frozen_followthrough_ready', 'frozen_cutoff': deepcopy(frozen),
                'next_tool': TOOL_NAME, 'next_arguments': {
                    'asset': asset, 'date_ny': frozen['date_ny'], 'shift': frozen['shift'],
                    'anchor_start_ny': frozen['parent']['start_ny'], 'phase': frozen['context']['phase'],
                    'expected_scope_id': frozen['source_scope_id']},
                'instruction': 'The original cutoff snapshot is now read. Append later evidence with exactly these arguments; through_ny may remain null. No additional member input is needed.'}
    if not isinstance(expected_scope_id, str) or expected_scope_id != frozen['source_scope_id']:
        return {'ok': False, 'status': 'frozen_followthrough_scope_changed',
                'error': 'The original cutoff evidence, asset, broker symbol, source precision or phase differs. Read the original shift again; no earlier outcome was replaced.'}
    cutoff = parse_time(frozen['cutoff_ny'])
    if type(through) is not int or type(as_of) is not int or not cutoff <= through <= as_of:
        raise ValueError('The follow-through horizon must be after the frozen cutoff and no later than now.')
    if through - cutoff > MAX_WINDOW_SECONDS:
        raise ValueError('Retained follow-through reads are bounded to 90 days after the original cutoff.')
    step = frozen['source_resolution_seconds']
    rows = _later_bars(bars, cutoff, through, step)
    coverage = _coverage(rows, cutoff, through, step)
    anchor, direction = frozen['parent'], frozen['context']['direction']
    invalidation = None
    conflict = None
    unknown_hour = None
    for opening in range(cutoff, through - 3599, 3600):
        candle = h1_anchor(rows, opening, step, native_h1, through)
        if candle.get('native_h1_status') == 'conflicting_ohlc':
            conflict = {'start_ny': stamp(opening), 'end_ny': stamp(opening + 3600),
                        'status': 'native_source_ohlc_conflict'}
            break
        if not candle.get('complete'):
            unknown_hour = unknown_hour or stamp(opening)
            continue
        if candle['close'] > anchor['high'] or candle['close'] < anchor['low']:
            prefix = _coverage(rows, cutoff, opening + 3600, step)
            invalidation = {
                'kind': 'range_invalidated_after_cutoff', 'bar_open_ny': stamp(opening),
                'known_at_ny': stamp(opening + 3600), 'timeframe': 'H1', 'close': candle['close'],
                # Proven closed native H1 can establish an own-timeframe close
                # without fabricating its missing M1/M5 path or target order.
                'first_invalidation_verified': not unknown_hour,
                'source_path_through_close_complete': prefix['complete'],
                **{k: deepcopy(candle[k]) for k in ('ohlc_basis', 'source_coverage_complete',
                                                   'native_ohlc_provenance') if k in candle}}
            break
    invalid_at = parse_time(invalidation['known_at_ny']) if invalidation else None
    conflict_at = parse_time(conflict['start_ny']) if conflict else None
    objectives = {}
    events = []
    delivered_statuses = {'observed_after_purge', 'observed_after_confirmation',
                          'objective_complete_while_range_valid'}
    for name, original in frozen['objectives'].items():
        item = {'level': original['level'], 'cutoff_status': original['status'],
                'status': 'already_delivered_at_shift_cutoff' if original['status'] in delivered_statuses
                else 'not_observed_in_bounded_later_window' if coverage['complete']
                else 'unverified_incomplete_later_coverage', 'evidence': None}
        if original['status'] not in delivered_statuses:
            hit = next((bar for bar in rows if (bar['high'] >= original['level']
                if direction == 'bullish' else bar['low'] <= original['level'])), None)
            if hit:
                known = hit['time'] + step
                prefix = _coverage(rows, cutoff, known, step)
                status = ('physical_touch_after_range_invalidation' if invalid_at and hit['time'] >= invalid_at
                    else 'touch_in_invalidating_bar_order_unresolved' if invalid_at and known == invalid_at
                    else 'touch_validity_unverified' if not prefix['complete']
                         or conflict_at is not None and known > conflict_at
                    else 'delivered_after_cutoff_while_range_valid')
                item.update(status=status, evidence={**interval(hit, step), 'known_at_ny': stamp(known),
                    'observed_price': hit['high'] if direction == 'bullish' else hit['low'],
                    'coverage_through_touch_complete': prefix['complete']})
                events.append({'kind': name + '_touch', 'status': status, 'level': original['level'],
                               **deepcopy(item['evidence'])})
        objectives[name] = item
    full = objectives['full_objective']
    delivered = full['status'] == 'delivered_after_cutoff_while_range_valid'
    terminal = ({'kind': 'full_objective_delivered', 'known_at_ny': full['evidence']['known_at_ny']}
                if delivered else {'kind': 'range_invalidated', 'known_at_ny': invalidation['known_at_ny'],
                                   'first_invalidation_verified': invalidation['first_invalidation_verified']}
                if invalidation else None)
    if delivered:
        # A later outside close cannot reopen or reverse the terminal delivery.
        invalidation = None
        events = [e for e in events if parse_time(e['known_at_ny']) <= parse_time(terminal['known_at_ny'])]
    elif invalidation:
        events.append(deepcopy(invalidation))
        for name, item in objectives.items():
            if item['status'] == 'already_delivered_at_shift_cutoff':
                continue
            physical = next((bar for bar in rows if bar['time'] >= invalid_at
                and (bar['high'] >= item['level'] if direction == 'bullish' else bar['low'] <= item['level'])), None)
            if physical:
                evidence = {**interval(physical, step), 'known_at_ny': stamp(physical['time'] + step),
                            'observed_price': physical['high'] if direction == 'bullish' else physical['low']}
                item['post_invalidation_physical_touch'] = evidence
                if (item.get('evidence') or {}).get('bar_open_ny') != evidence['bar_open_ny']:
                    events.append({'kind': name + '_physical_touch_after_invalidation',
                        'status': 'physical_touch_after_range_invalidation', 'level': item['level'], **evidence})
    outcome = ('full_objective_delivered_after_cutoff' if delivered else
               'invalidated_after_cutoff_without_verified_full_delivery' if invalidation else
               'unverified_later_evidence' if not coverage['complete'] or conflict
                    or full['status'] == 'touch_validity_unverified' else
               'pending_at_followthrough_cutoff')
    events.sort(key=lambda e: (parse_time(e['known_at_ny']), e['kind']))
    label = frozen['context']['name']
    summary = (f"At {frozen['cutoff_ny']}, {label}'s {direction} full objective was still pending. ")
    if delivered:
        source = full['evidence']
        summary += (f"Later evidence records full delivery in {source['bar_open_ny']} to {source['bar_close_ny']}. "
                    'The structural follow-through ends at that delivery.')
    elif invalidation:
        summary += f"A later H1 close outside the same range is observed at {invalidation['known_at_ny']}."
        if not invalidation['first_invalidation_verified']:
            summary += ' Missing earlier candles leave the first invalidation time unverified.'
        if full['status'] == 'physical_touch_after_range_invalidation':
            summary += ' The later target touch occurred after invalidation and is not valid delivery.'
        elif full['status'] == 'touch_in_invalidating_bar_order_unresolved':
            summary += ' The target touch shares the invalidating source bar; valid delivery order is unresolved.'
            if full.get('post_invalidation_physical_touch'):
                summary += ' A separate later physical target touch is recorded after invalidation, not as valid delivery.'
    elif outcome == 'pending_at_followthrough_cutoff':
        summary += f"No full-objective touch is observed from {stamp(cutoff)} through {stamp(through)} in complete supplied candles."
    else:
        summary += f"Later evidence through {stamp(through)} is incomplete or conflicting; the bounded outcome is unverified."
    summary += ' The original shift outcome and entry tradeability assessment are unchanged.'
    return {'ok': True, 'asset': asset, 'symbol': symbol, 'review': {
        'version': VERSION, 'mode': 'post_shift_followthrough', 'frozen_cutoff': deepcopy(frozen),
        'appendix': {'window_start_ny': stamp(cutoff), 'through_ny': stamp(through),
            'as_of_ny': stamp(as_of), 'last_observed_bar_close_ny': stamp(rows[-1]['time'] + step) if rows else None,
            'coverage': coverage, 'status': outcome, 'objectives': objectives, 'events': events,
            'terminal': terminal, 'invalidation': invalidation, 'hourly_evidence_conflict': conflict,
            'structural_path_stops_at_first_terminal': True,
            'later_evidence_cannot_establish_cutoff_tradeability': True,
            'member_execution': 'not_assessed', 'member_result': 'not_assessed'},
        'spoken_summary': summary, 'response_contract': CONTRACT}}


def review_post_shift_followthrough(db, feed, args, now):
    """Read the original precision and a bounded later window from retained data."""
    from gbop_voice_web.market_data import (
        _history_sets, _select_shift_history, history_native_h1, session_review,
    )
    opening, cutoff = shift_bounds(args['date_ny'], args['shift'])
    if now <= cutoff:
        return {'ok': False, 'status': 'shift_not_finished',
                'error': 'The frozen shift cutoff has not passed; no post-shift evidence is available.'}
    requested = parse_time(args['through_ny']) if args.get('through_ny') else now // 60 * 60
    if not cutoff < requested <= now or requested - cutoff > MAX_WINDOW_SECONDS:
        raise ValueError('Use a follow-through cutoff after shift end, no later than now, within the retained 90-day read window.')
    captured = datetime.fromisoformat(feed['captured_at_utc'])
    if captured.tzinfo is None:
        raise ValueError('The market feed capture requires a verified timezone.')
    observed_limit = min(requested, int(captured.timestamp()))
    native = history_native_h1(db, feed, opening - 7200, observed_limit)
    sets = _history_sets(db, feed, opening - 7200, requested)
    sets = {step: [b for b in rows if b['time'] + step <= observed_limit] for step, rows in sets.items()}
    original_bars, step = _select_shift_history(sets, args['date_ny'], args['shift'], cutoff, native)
    frozen = session_review(original_bars, args['date_ny'], args['shift'], step, native_h1=native)
    # Keep exactly the source precision used by the original review. Finer
    # later data cannot be spliced into the earlier candle-order proof.
    through = requested if args.get('through_ny') else max(
        [b['time'] + step for b in sets[step] if b['time'] >= cutoff]
        + [b['time'] + 3600 for b in native if b['time'] >= cutoff], default=cutoff)
    result = build_followthrough(frozen, sets[step], asset=feed['asset'], symbol=feed['symbol'],
        anchor_start_ny=args['anchor_start_ny'], phase=args['phase'],
        expected_scope_id=args.get('expected_scope_id'), through=through, as_of=now, native_h1=native)
    result['source_snapshot'] = {k: deepcopy(feed[k]) for k in
        ('captured_at_utc', 'received_at_utc', 'feed_health', 'is_live') if k in feed}
    if result.get('status') == 'frozen_followthrough_ready':
        result['next_arguments']['through_ny'] = args.get('through_ny')
    if result.get('review'):
        result['review']['appendix'].update(
            horizon_basis='explicit_through_ny' if args.get('through_ny') else 'latest_available_closed_source',
            requested_through_ny=args.get('through_ny'))
    return result


def _context_followthrough_arguments(context, arguments):
    """A remembered evidence hash cannot authorize a different conversation scope."""
    from gbop_voice_web.market_data import asset_name

    args = {k: v for k, v in arguments.items() if not k.startswith('_')}
    selected, requested, pending = (getattr(context, key, None) or {}
                                    for key in ('selected', 'requested', 'pending'))
    intent = context.intent or {}
    unresolved = (getattr(context, '_comparison_asset_ambiguous', False)
        or getattr(context, '_multi_focus_required', False)
        or getattr(context, '_multi_request', None) is not None
        or getattr(context, '_required_current', False)
        or getattr(context, '_scan_request', None) is not None
        or intent.get('action') == 'ambiguous'
        or intent.get('action') in ('latest', 'last_night', 'reset') and not pending)
    if unresolved:
        return None, {'ok': False, 'status': 'market_context_selection_required',
            'error': 'Resolve and select the requested market context before following its frozen shift outcome.'}

    def normalized(key, value):
        return asset_name(value) if key == 'asset' else parse_time(value) if key == 'anchor_start_ny' else value

    keys = ('asset', 'date_ny', 'shift', 'anchor_start_ny', 'phase')
    try:
        # A typed switch first establishes its requested evidence through the
        # normal selector. Neither an old hash nor a null-hash read may skip it.
        if selected and any(scope.get(key) is not None
                and normalized(key, scope[key]) != normalized(key, selected.get(key))
                for scope in (requested, pending) for key in keys):
            return None, {'ok': False, 'status': 'market_context_selection_required',
                'error': 'The requested context differs from the selected evidence. Select or review that exact context first.'}
        for scope in (selected, requested, pending, intent.get('fields') or {}):
            if scope.get('review_mode') in ('current_market', 'current_pending'):
                return None, {'ok': False, 'status': 'market_context_selection_required',
                    'error': 'Select the completed shift context before requesting its post-shift outcome.'}
            for key in keys:
                if scope.get(key) is None:
                    continue
                if args.get(key) is not None and normalized(key, args[key]) != normalized(key, scope[key]):
                    return None, {'ok': False, 'status': 'market_context_mismatch',
                        'error': 'Follow-through must retain the selected asset, date, shift, focused range and phase.'}
                args[key] = scope[key]
        return args, None
    except (ValueError, TypeError, KeyError):
        return None, {'ok': False, 'status': 'market_context_mismatch',
            'error': 'The selected or requested follow-through scope is incomplete or invalid.'}


def run_context_followthrough(context, arguments, runner, generation):
    """Optional transport integration: read only, preserve selection, fence barge-in.

    The expected_scope_id binds the exact frozen range/phase. This operation
    must never replace the conversation's original selected cutoff or evidence.
    """
    with context._lock:
        if not context.current(generation):
            return context._stale()
        args, denial = _context_followthrough_arguments(context, arguments)
        if denial:
            return denial
    result = runner(TOOL_NAME, args)
    with context._lock:
        if not context.current(generation):
            return context._stale()
        _, denial = _context_followthrough_arguments(context, args)
        return denial or result
