"""Presentation-only variant reasons and conditional paths from cutoff evidence.

The established classifier is authoritative. Candidates never enter its labels,
never predict delivery, and never change selection, invalidation or execution.
"""
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time, stamp, summarize

NAMES = {'V1': 'Textbook', 'V2': 'Pattern Trader’s Kryptonite', 'V3': 'extended distribution',
         'V4': 'one inside bar', 'V5': 'multiple inside bars', 'V6': 're-soup'}


def clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _compact(result):
    # The authoritative classifier already retains unresolved/failure reasons.
    # Do not repeat null/empty values and boilerplate in every transport view.
    return {k: v for k, v in result.items() if v is not None and v != []
            and not (k == 'reason' and result['status'] in ('unverified', 'not_established'))}


def variant_explanation(row, bars, end, step):
    """Describe supported labels or bounded possibilities, without classifying."""
    evidence = row.get('variant_evidence', {})
    labels = evidence.get('labels', [])
    anchor = row['anchor']
    start = parse_time(anchor['start_ny'])
    result = {'status': 'unverified', 'candidates': [], 'reason': '', 'known_at_ny': None}
    opening = datetime.fromisoformat(anchor['start_ny'])
    if not anchor.get('complete') or opening.minute or opening.second:
        result['reason'] = 'Complete aligned H1 range evidence is missing.'
        return _compact(result)
    bars = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end), key=lambda b: b['time'])
    purges = sorted((e for e in row.get('events', []) if e['kind'].endswith('_side_purge')
                    and parse_time(e['bar_close_ny']) <= end), key=lambda e: e['bar_open_ny'])
    direction = row.get('direction_observed', row.get('observed_direction'))
    if not purges or not direction:
        result['reason'] = 'No ordered directional purge establishes a variant path.'
        return _compact(result)
    first = purges[0]
    manipulation = parse_time(first['bar_open_ny']) // 3600 * 3600
    closes = manipulation + 3600
    # Use the complete prefix for absence/timing claims. Later bars after a gap
    # cannot rule out an earlier distribution or prove an inside-bar sequence.
    prefix = start
    for bar in bars:
        if bar['time'] != prefix:
            break
        prefix += step
    internal_gap = any(b['time'] > prefix for b in bars)
    inside = [summarize(bars, t, t + 3600, step) for t in range(start + 3600, manipulation, 3600)]
    clean_inside = all(c['complete'] and c['high'] <= anchor['high'] and c['low'] >= anchor['low'] for c in inside)
    invalid = row.get('invalidated_at_ny')
    invalid = invalid if invalid and parse_time(invalid) <= end else None
    target = next((o.get('evidence') for o in row.get('objectives', [])
                   if o['objective'] == 'opposing_liquidity' and o['status'] == 'observed_after_purge'), None)
    if target and (parse_time(target['bar_close_ny']) > end or
                   (invalid and parse_time(target['bar_close_ny']) >= parse_time(invalid))):
        target = None
    distribution = parse_time(target['bar_open_ny']) // 3600 * 3600 if target else None
    count = (distribution - start) // 3600 + 1 if target else None
    man = summarize(bars, manipulation, closes, step)
    man_complete = closes <= end and man['complete']
    man_inside = man_complete and anchor['low'] <= man['close'] <= anchor['high']
    range_name, man_name = clock(anchor['start_ny']), clock(stamp(manipulation))
    pending_resoup = None
    if man_inside and not invalid and not internal_gap and not any(v['code'] == 'V6' for v in labels):
        midpoint = next((o.get('evidence') for o in row.get('objectives', []) if o['objective'] == 'midpoint'), None)
        midpoint_start = parse_time(midpoint['bar_open_ny']) if midpoint and parse_time(midpoint['bar_close_ny']) <= end else float('inf')
        for t in range(closes, min(prefix, end), 3600):
            partial = summarize(bars, t, min(t + 3600, prefix, end), step)
            swept = (partial.get('high', man['high']) > man['high'] if direction == 'bearish'
                     else partial.get('low', man['low']) < man['low'])
            if swept and t + 3600 > prefix and midpoint_start >= t + 3600:
                pending_resoup = {'code': 'V6', 'name': NAMES['V6'],
                    'requires': f"{clock(stamp(t))} H1 must close inside before midpoint delivery after sweeping {man_name}'s extreme."}
                break
    reasons = {}
    if labels:
        dist_name = clock(stamp(distribution)) if distribution is not None else None
        reasons['V1'] = f'{range_name} range, {man_name} manipulation back inside, {dist_name} distribution'
        reasons['V2'] = f'{man_name} H1 manipulated then distributed the {range_name} range'
        reasons['V3'] = f'{dist_name} distributed the {range_name} range on candle {count}'
        inside_names = '/'.join(clock(c['start_ny']) for c in inside)
        reasons['V4'] = f'{inside_names} was inside before {man_name} manipulation returned inside'
        reasons['V5'] = f'{inside_names} were {len(inside)} inside candles before {man_name} manipulation returned inside'
        resoup = evidence.get('resoup_hour_ny')
        reasons['V6'] = (f'{clock(resoup)} swept {man_name}\'s extreme and closed inside before midpoint delivery'
                         if resoup else '')
        result['reason'] = '; '.join(reasons[v['code']] for v in labels if reasons.get(v['code']))
        result['status'] = 'completed' if target and evidence.get('status') == 'distribution_observed' else 'structure_observed'
        known = [distribution + 3600 for v in labels if v['code'] in ('V1', 'V2', 'V3') and distribution is not None]
        known += [closes for v in labels if v['code'] in ('V4', 'V5')]
        if any(v['code'] == 'V6' for v in labels) and resoup:
            known.append(parse_time(resoup) + 3600)
        result['known_at_ny'] = stamp(max(known))
        if result['status'] != 'completed':
            result['remaining'] = (
                'Opposing liquidity observed; gaps leave completed distribution classification unverified.'
                if target and parse_time(target['bar_close_ny']) > prefix else
                f'Opposing liquidity delivered; complete {clock(stamp(distribution))} H1 evidence still needed.'
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
        result['reason'] = (f'{len(inside)} complete inside H1 ' + ('candle' if len(inside) == 1 else 'candles')
                            + f" preceded {man_name}'s purge")
        candidate('V4' if len(inside) == 1 else 'V5', f'{man_name} H1 must close back inside; '
                  + ('opposing delivery is already observed.' if target else 'full delivery remains unconfirmed.'))
    elif target and parse_time(target['bar_close_ny']) <= prefix:
        if manipulation == start + 3600 and (count == 2 or man_inside):
            code = 'V2' if count == 2 else 'V1' if count == 3 else 'V3'
            result['reason'] = f'{man_name} manipulated; opposing liquidity was reached in candle {count}'
            candidate(code, f'Complete {clock(stamp(distribution))} H1 evidence through its {clock(stamp(distribution + 3600))} close.')
    elif manipulation == start + 3600:
        result['reason'] = f'{man_name} purged the {range_name} range' + (' and closed back inside' if man_inside else '; its H1 close is unverified')
        if not man_complete:
            candidate('V2', 'Ordered opposing delivery in candle 2 with complete H1 evidence.')
        if not man_complete or man_inside:
            if prefix < start + 3 * 3600:
                candidate('V1', ('Ordered opposing delivery' if man_inside else 'Inside manipulation close, then opposing delivery') + ' in candle 3 with complete H1 evidence.')
            candidate('V3', ('Ordered opposing delivery' if man_inside else 'Inside manipulation close, then opposing delivery') + ' after candle 3 with complete H1 evidence.')
    # Do not offer hypothetical future re-soups without the actual extreme sweep.
    if pending_resoup:
        result['candidates'].append(pending_resoup)
        result['reason'] += '; ' + pending_resoup['requires'].split(' H1 must')[0] + f" swept {man_name}'s extreme"
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
            text += f"; established at {clock(detail['known_at_ny'])} H1 close"
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
