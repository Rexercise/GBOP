"""Compact selected-range continuity from the authoritative shift ledger.

An hour's candle body is distinct from its own independent CRT attempt. Delivery
concludes an objective; only a recorded transition replaces the selected range.
"""
from copy import deepcopy
from datetime import datetime

from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.candle_naming import candle_label, source_timeframe


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _interval(event):
    return {k: event[k] for k in ('bar_open_ny', 'bar_close_ny', 'precision_seconds') if k in event}


def _event_label(event):
    return candle_label(event['bar_open_ny'], source_timeframe(event.get('precision_seconds')))


def selected_range_story(story, row, fact):
    """Keep one selected anchor through its real hourly development and outcome."""
    anchor = row['anchor_start_ny']
    if row.get('role') != 'selected_range':
        return None
    next_range = next((t for t in story.get('range_transitions', [])
                       if t['from_anchor_ny'] == anchor), None)
    next_range = ({k: next_range[k] for k in ('to_anchor_ny', 'confirmed_at_ny', 'reason')}
                  if next_range else None)
    if next_range:
        next_range['at_review_cutoff'] = next_range['confirmed_at_ny'] == story['end_ny']
    by_hour = {r['anchor_start_ny']: r['anchor'] for r in story.get('ranges', [])}
    purges = [e for e in row.get('events', []) if e['kind'].endswith('_side_purge')]
    first = min(purges, key=lambda e: e['bar_open_ny']) if purges else None
    side = 'buy' if fact.get('direction') == 'bearish' else 'sell' if fact.get('direction') == 'bullish' else None
    # Paired direction may differ from local purge direction. Do not reassign a
    # local manipulation to the opposite boneless thesis.
    if first and (side is None or first['kind'] != side + '_side_purge'):
        first = None
    returned = next((s for s in row.get('sweep_detail', [])
                     if s['side'] == side and s.get('first_source_close_back_inside_ny')), None) if first else None
    return_interval = None
    if returned:
        end, precision = returned['first_source_close_back_inside_ny'], returned['precision_seconds']
        return_interval = {'bar_open_ny': stamp(parse_time(end) - precision),
                           'bar_close_ny': end, 'precision_seconds': precision}
    development, clauses = [], []
    name = f"{_clock(anchor)} H1 range"
    for hour in story.get('hourly_progression', []):
        if hour['anchor_start_ny'] != anchor:
            continue
        item = {k: deepcopy(hour[k]) for k in ('candle_start_ny', 'candle_end_ny', 'complete',
                'status', 'candle_science', 'close') if k in hour}
        candle = by_hour.get(hour['candle_start_ny'], {})
        body = None
        if hour.get('complete') and candle.get('open') is not None and candle.get('close') is not None:
            body = ('bullish' if candle['close'] > candle['open'] else
                    'bearish' if candle['close'] < candle['open'] else 'doji')
        item['candle_body_direction'] = body
        lo, hi = parse_time(hour['candle_start_ny']), parse_time(hour['candle_end_ny'])
        actions = []
        if first and lo <= parse_time(first['bar_open_ny']) < hi:
            item['purge'] = {'side': side, **_interval(first)}
            actions.append(f"swept the {name}'s {side}-side in {_event_label(first)}")
        if return_interval and lo < parse_time(return_interval['bar_close_ny']) <= hi:
            item['return_inside'] = deepcopy(return_interval)
            actions.append(f"returned inside in {_event_label(return_interval)}")
        for key, label in (('midpoint', '50%'), ('opposing_liquidity',
                'sell-side' if fact.get('direction') == 'bearish' else 'buy-side')):
            objective = fact.get(key, {})
            event = objective.get('source_interval') or {}
            if (objective.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
                    and event.get('bar_open_ny') and lo <= parse_time(event['bar_open_ny']) < hi):
                item[key] = deepcopy(event)
                actions.append(f"delivered the {name}'s {label} in {_event_label(event)}")
        if not hour.get('complete') or not hour.get('candle_science'):
            actions.append('has incomplete evidence; its close and progression are unverified')
        elif not any(k in item for k in ('midpoint', 'opposing_liquidity')):
            state = {'wick_above': 'back inside', 'wick_below': 'back inside',
                     'both_sides_wicked': 'inside', 'inside_range': 'inside',
                     'close_above': 'above', 'close_below': 'below'}[hour['candle_science']]
            actions.append(f"closed {body + ' ' if body else ''}{state} the {name}")
        if actions:
            clauses.append(f"The {_clock(hour['candle_start_ny'])} H1 candle " + ', then '.join(actions) + '.')
        development.append(item)
    full = fact.get('opposing_liquidity', {})
    delivered = full.get('status') in ('observed_after_purge', 'objective_complete_while_range_valid')
    invalid = fact.get('invalidated_at_ny')
    conclusion = {'status': 'opposing_liquidity_delivered' if delivered else
                  'invalidated' if invalid else 'unverified' if fact.get('outcome') == 'unverified' else
                  'pending_at_review_cutoff',
                  'source_interval': deepcopy(full.get('source_interval')) if delivered else None,
                  'known_at_ny': (full.get('source_interval') or {}).get('bar_close_ny') if delivered else invalid}
    if row.get('selected_at_ny') and parse_time(row['selected_at_ny']) >= parse_time(story['end_ny']):
        conclusion = {'status': 'pending_at_review_cutoff', 'source_interval': None, 'known_at_ny': None}
    variant = row.get('variant_evidence', {})
    codes = [v['code'] for v in fact.get('variant', {}).get('labels', [])]
    distribution = variant.get('distribution_hour_ny')
    variant_known = stamp(parse_time(distribution) + 3600) if distribution and codes else None
    selection_start = row.get('selected_at_ny')
    if selection_start and parse_time(selection_start) >= parse_time(story['end_ny']):
        intro = f"The {name} became selected at the {_clock(story['end_ny'])} cutoff; no later setup or delivery evidence is available."
    else:
        through = ', '.join(_clock(h['candle_start_ny']) for h in development)
        verified = all(h.get('candle_science') for h in development)
        intro = f"The {name} stayed selected" + (f" through the {through} H1 candles." if through else '.')
        if not verified:
            intro = f"The {name} was selected; later progression has missing or unfinished evidence."
    text = ' '.join([intro] + clauses)
    if codes:
        text += ' The completed H1 sequence supports ' + '/'.join(codes)
        text += f" at the {_clock(variant_known)} close." if variant_known else ' at this review cutoff.'
    elif conclusion['status'] == 'pending_at_review_cutoff':
        text += ' Its directional outcome and variant remain pending at the cutoff.'
    elif conclusion['status'] == 'unverified':
        text += ' Missing evidence leaves its outcome unverified.'
    if invalid:
        text += f" It was invalidated at {_clock(invalid)}" + (' after recorded delivery.' if delivered else '.')
    if next_range:
        text += (f" The next selected range was {_clock(next_range['to_anchor_ny'])} H1, confirmed at "
                 f"{_clock(next_range['confirmed_at_ny'])}.")
    elif story.get('progression_complete'):
        text += ' No later selected-range transition occurred before the cutoff.'
    else:
        text += ' A later selected-range transition is unverified.'
    verified_through = next((h['candle_end_ny'] for h in reversed(development) if h.get('candle_science')), selection_start)
    return {'anchor_start_ny': anchor, 'selected_at_ny': selection_start,
            'selected_through_ny': next_range['confirmed_at_ny'] if next_range else verified_through,
            'still_selected_at_cutoff': (story.get('active_anchor_ny') == anchor
                                        if story.get('progression_complete') else None),
            'direction': fact.get('direction'), 'hourly_development': development,
            'invalidated_at_ny': invalid,
            'conclusion': conclusion, 'variant': deepcopy(fact.get('variant', {})),
            'variant_known_at_ny': variant_known,
            'next_selected_range': next_range, 'spoken_summary': text}


def shift_end_state(story):
    """Actual final close plus the newly closed range's independent evidence limit."""
    hours = story.get('hourly_progression', [])
    final = hours[-1] if hours else {}
    return {'through_ny': story['end_ny'], 'active_anchor_ny': story.get('active_anchor_ny'),
            'progression_complete': story.get('progression_complete', False),
            'final_hour': {k: deepcopy(final[k]) for k in ('candle_start_ny', 'candle_end_ny',
                'anchor_start_ny', 'complete', 'close', 'candle_science', 'status') if k in final},
            'newly_closed_range_observation': ('no_post_close_evidence_at_cutoff'
                if final.get('complete') and final.get('candle_end_ny') == story['end_ny']
                else 'final_range_unfinished_or_unverified'),
            'spoken_summary': story.get('recap', {}).get('closing',
                f"Review ends at {_clock(story['end_ny'])} New York.")}
