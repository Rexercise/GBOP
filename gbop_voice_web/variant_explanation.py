"""Presentation-only variant reasons and conditional paths from cutoff evidence.

The established classifier is authoritative. Candidates never enter its labels,
never predict delivery, and never change selection, invalidation or execution.
"""
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time, stamp, summarize, timeframe
from gbop_voice_web.candle_naming import closure_label
from gbop_voice_web.crt_variant_clock import variant_clock, containing_index

NAMES = {'V1': 'Textbook', 'V2': 'Pattern Trader’s Kryptonite', 'V3': 'extended distribution',
         'V4': 'one inside bar', 'V5': 'multiple inside bars', 'V6': 're-soup'}


def clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _compact(result):
    # The authoritative classifier already retains unresolved/failure reasons.
    # Do not repeat null/empty values and boilerplate in every transport view.
    return {k: v for k, v in result.items() if v is not None and v != []
            and not (k == 'reason' and result['status'] in ('unverified', 'not_established'))}


def variant_explanation(row, bars, end, step, *, candle_timeframe=None):
    """Describe supported labels or bounded possibilities, without classifying."""
    if row.get('validity_evidence_through_ny'):
        end = min(end, parse_time(row['validity_evidence_through_ny']))
    evidence = row.get('variant_evidence', {})
    labels = evidence.get('labels', [])
    anchor = row['anchor']
    start = parse_time(anchor['start_ny'])
    tf = timeframe(candle_timeframe or 'H1')
    clock_rows = variant_clock(start, end, tf)
    result = {'status': 'unverified', 'candidates': [], 'reason': '', 'known_at_ny': None}
    opening = datetime.fromisoformat(anchor['start_ny'])
    if not anchor.get('complete') or (candle_timeframe is None and tf == 'H1' and (opening.minute or opening.second)):
        result['reason'] = f'Complete aligned {tf} range evidence is missing.'
        return _compact(result)
    bars = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end), key=lambda b: b['time'])
    purges = sorted((e for e in row.get('events', []) if e['kind'].endswith('_side_purge')
                    and parse_time(e['bar_close_ny']) <= end), key=lambda e: e['bar_open_ny'])
    direction = row.get('direction_observed', row.get('observed_direction'))
    if not purges or not direction:
        result['reason'] = 'No ordered directional purge establishes a variant path.'
        return _compact(result)
    first = purges[0]
    manipulation_index = containing_index(clock_rows, parse_time(first['bar_open_ny']))
    if manipulation_index is None or manipulation_index == 0:
        result['reason'] = 'No post-range manipulation is available at the review cutoff.'
        return _compact(result)
    manipulation, closes = clock_rows[manipulation_index]
    # Use the complete prefix for absence/timing claims. Later bars after a gap
    # cannot rule out an earlier distribution or prove an inside-bar sequence.
    prefix = parse_time(anchor['end_ny'])
    for bar in (b for b in bars if b['time'] >= prefix):
        if bar['time'] != prefix:
            break
        prefix += step
    internal_gap = any(b['time'] > prefix for b in bars)
    inside = [summarize(bars, t, stop, step) for t, stop in clock_rows[1:manipulation_index]]
    clean_inside = all(c['complete'] and c['high'] <= anchor['high'] and c['low'] >= anchor['low'] for c in inside)
    invalid = row.get('invalidated_at_ny')
    invalid = invalid if invalid and parse_time(invalid) <= end else None
    target = next((o.get('evidence') for o in row.get('objectives', [])
                   if o['objective'] == 'opposing_liquidity' and o['status'] == 'observed_after_purge'), None)
    if target and (parse_time(target['bar_close_ny']) > end or
                   (invalid and parse_time(target['bar_close_ny']) >= parse_time(invalid))):
        target = None
    distribution_index = containing_index(clock_rows, parse_time(target['bar_open_ny'])) if target else None
    distribution = clock_rows[distribution_index][0] if distribution_index is not None else None
    distribution_end = clock_rows[distribution_index][1] if distribution_index is not None else None
    count = distribution_index + 1 if distribution_index is not None else None
    man = summarize(bars, manipulation, closes, step)
    man_complete = closes <= end and man['complete']
    man_inside = man_complete and anchor['low'] <= man['close'] <= anchor['high']
    range_name, man_name = clock(anchor['start_ny']), clock(stamp(manipulation))
    pending_resoup = None
    if man_inside and not invalid and not internal_gap and not any(v['code'] == 'V6' for v in labels):
        full_touch = next((o.get('evidence') for o in row.get('objectives', []) if o['objective'] == 'opposing_liquidity'), None)
        completion_start = parse_time(full_touch['bar_open_ny']) if full_touch and parse_time(full_touch['bar_close_ny']) <= end else float('inf')
        for t, stop in clock_rows[manipulation_index + 1:]:
            if t >= min(prefix, end):
                break
            partial = summarize(bars, t, min(stop, prefix, end), step)
            swept = (partial.get('high', man['high']) > man['high'] if direction == 'bearish'
                     else partial.get('low', man['low']) < man['low'])
            if swept and stop > prefix and completion_start >= stop:
                pending_resoup = {'code': 'V6', 'name': NAMES['V6'],
                    'requires': f"{clock(stamp(t))} {tf} must close inside before full opposing-liquidity delivery after sweeping {man_name}'s extreme."}
                break
    reasons = {}
    if labels:
        dist_name = clock(stamp(distribution)) if distribution is not None else None
        reasons['V1'] = f'{range_name} range, {man_name} manipulation back inside, {dist_name} distribution'
        reasons['V2'] = f'{man_name} {tf} manipulated then distributed the {range_name} range'
        reasons['V3'] = f'{dist_name} distributed the {range_name} range on candle {count}'
        inside_names = '/'.join(clock(c['start_ny']) for c in inside)
        reasons['V4'] = f'{inside_names} was inside before {man_name} manipulation returned inside'
        reasons['V5'] = f'{inside_names} were {len(inside)} inside candles before {man_name} manipulation returned inside'
        resoup = evidence.get('resoup_hour_ny')
        reasons['V6'] = (f'{clock(resoup)} swept {man_name}\'s extreme and closed inside before full opposing-liquidity delivery'
                         if resoup else '')
        result['reason'] = '; '.join(reasons[v['code']] for v in labels if reasons.get(v['code']))
        result['status'] = 'completed' if target and evidence.get('status') == 'distribution_observed' else 'structure_observed'
        known = [distribution_end for v in labels if v['code'] in ('V1', 'V2', 'V3') and distribution is not None]
        known += [closes for v in labels if v['code'] in ('V4', 'V5')]
        if any(v['code'] == 'V6' for v in labels) and resoup:
            known.append(clock_rows[containing_index(clock_rows, parse_time(resoup))][1])
        result['known_at_ny'] = stamp(max(known))
        if tf != 'H1':
            result['known_timeframe'] = tf
            result['known_candle_open_ny'] = stamp(next(t for t, stop in clock_rows if stop == max(known)))
        if result['status'] != 'completed':
            result['remaining'] = (
                'Opposing liquidity observed; gaps leave completed distribution classification unverified.'
                if target and parse_time(target['bar_close_ny']) > prefix else
                f'Opposing liquidity delivered; complete {clock(stamp(distribution))} {tf} evidence still needed.'
                if target else 'Range invalidated before full delivery.' if invalid else
                'Ordered opposing-liquidity delivery remains pending or unverified.')
        if pending_resoup:
            result['candidates'].append(pending_resoup)
        return _compact(result)
    if invalid:
        result.update(status='not_established', reason='The range invalidated before a variant was established.')
        return _compact(result)
    full_objective = next((o for o in row.get('objectives', []) if o['objective'] == 'opposing_liquidity'), {})
    if full_objective.get('status') in ('same_bar_order_unknown', 'touch_in_invalidating_bar_order_unresolved'):
        result['reason'] = 'The initial opposing touch has unresolved source-bar order.'
        return _compact(result)
    if (prefix < parse_time(first['bar_close_ny']) or not clean_inside
            or internal_gap):
        result['reason'] = 'Missing or non-inside intervening candles leave the variant path unverified.'
        return _compact(result)
    result['status'] = 'pending'
    result['observed_through_ny'] = stamp(prefix)
    def candidate(code, requires):
        result['candidates'].append({'code': code, 'name': NAMES[code], 'requires': requires})
    if inside:
        result['reason'] = (f'{len(inside)} complete inside {tf} ' + ('candle' if len(inside) == 1 else 'candles')
                            + f" preceded {man_name}'s purge")
        candidate('V4' if len(inside) == 1 else 'V5', f'{man_name} {tf} must close back inside; '
                  + ('opposing delivery is already observed.' if target else 'full delivery remains unconfirmed.'))
    elif target and parse_time(target['bar_close_ny']) <= prefix:
        if manipulation_index == 1 and (count == 2 or man_inside):
            code = 'V2' if count == 2 else 'V1' if count == 3 else 'V3'
            result['reason'] = f'{man_name} manipulated; opposing liquidity was reached in candle {count}'
            candidate(code, f'Complete {clock(stamp(distribution))} {tf} evidence through its own closure.')
    elif manipulation_index == 1:
        result['reason'] = f'{man_name} purged the {range_name} range' + (' and closed back inside' if man_inside else f'; its {tf} close is unverified')
        if not man_complete:
            candidate('V2', f'Ordered opposing delivery in candle 2 with complete {tf} evidence.')
        if not man_complete or man_inside:
            if len(clock_rows) < 3 or prefix < clock_rows[2][1]:
                candidate('V1', ('Ordered opposing delivery' if man_inside else 'Inside manipulation close, then opposing delivery') + f' in candle 3 with complete {tf} evidence.')
            candidate('V3', ('Ordered opposing delivery' if man_inside else 'Inside manipulation close, then opposing delivery') + f' after candle 3 with complete {tf} evidence.')
    # Do not offer hypothetical future re-soups without the actual extreme sweep.
    if pending_resoup:
        result['candidates'].append(pending_resoup)
        result['reason'] += '; ' + pending_resoup['requires'].split(f' {tf} must')[0] + f" swept {man_name}'s extreme"
    if not result['candidates']:
        result.update(status='unverified', reason='Available candles do not establish a unique supported variant path.')
    elif prefix < end:
        result['coverage_limit'] = 'Missing or unfinished candles leave later development unverified.'
    return _compact(result)


def _display_name(variant):
    """Use the canonical V2 name without changing codes or saved evidence."""
    if variant['code'] == 'V2':
        return 'V2 — ' + NAMES['V2']
    return variant['code'] + ' ' + variant['name']


def variant_clause(variant, *, include_known=False):
    """One brief explanatory clause; candidates are explicitly conditional."""
    detail = variant.get('explanation', {})
    labels = variant.get('labels', [])
    reason = detail.get('reason')
    if labels:
        text = '/'.join(_display_name(v) for v in labels)
        text += ' because ' + reason if reason else ''
        if detail.get('status') == 'structure_observed':
            text += '; ' + detail['remaining'].rstrip('.').lower()
        elif include_known and detail.get('known_at_ny'):
            text += '; established on ' + closure_label(
                detail.get('known_candle_open_ny') or stamp(parse_time(detail['known_at_ny']) - 3600),
                detail.get('known_timeframe', 'H1'))
        for pending in detail.get('candidates', []):
            text += f"; {_display_name(pending)} pending: {pending['requires'].rstrip('.')}"
        return text
    candidates = detail.get('candidates', [])
    if candidates:
        names = '/'.join(_display_name(v) for v in candidates)
        needs = ' '.join(v['code'] + ': ' + v['requires'] for v in candidates)
        text = f'{names} pending because {reason}; {needs.rstrip(".")}'
        if detail.get('coverage_limit'):
            text += '; later candles are unverified'
        return text
    status = detail.get('status', variant.get('status'))
    return 'variant ' + ('not established' if status == 'not_established' else 'unverified' if status == 'unverified' else 'pending')
