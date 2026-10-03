"""Matched, closed-bar range SMT evidence; never an execution detector.

Only the selected anchors are compared. Later swing-to-swing SMT, inverse pairs,
and discretionary entry models are intentionally outside this detector's scope.
"""
from datetime import datetime
from math import lcm
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def stamp(t):
    return datetime.fromtimestamp(t, NY).isoformat()


def _aggregate(bars, start, end, source_step, step):
    """Downsample complete groups only; never interpolate missing fine bars."""
    indexed = {}
    for bar in bars:
        t = bar['time']
        if start <= t < end:
            if t in indexed:
                raise ValueError('Duplicate candle timestamp in SMT evidence.')
            indexed[t] = bar
    result = {}
    for t in range(start, end, step):
        times = list(range(t, t + step, source_step))
        if t + step > end or not all(s in indexed for s in times):
            continue
        rows = [indexed[s] for s in times]
        result[t] = {'time': t, 'open': rows[0]['open'],
                     'high': max(b['high'] for b in rows),
                     'low': min(b['low'] for b in rows), 'close': rows[-1]['close']}
    return result


def _anchor(rows):
    values = list(rows.values())
    high, low = max(b['high'] for b in values), min(b['low'] for b in values)
    return {'high': high, 'low': low, 'midpoint': (high + low) / 2}


def _targets(rows, anchor, event_time, step, bearish):
    targets = {'midpoint': anchor['midpoint'],
               'opposing_liquidity': anchor['low' if bearish else 'high']}
    result = {}
    for name, level in targets.items():
        touches = [t for t, b in sorted(rows.items()) if t >= event_time and
                   (b['low'] <= level if bearish else b['high'] >= level)]
        later = next((t for t in touches if t > event_time), None)
        same = event_time in touches
        result[name] = {'level': level, 'same_event_bar_touch': same,
                        'first_observed_later_touch_ny': stamp(later) if later is not None else None,
                        'status': 'observed_after_divergence' if later is not None else
                                  'same_bar_order_unknown' if same else 'not_observed',
                        'precision_seconds': step}
    return result


def compare_ranges(left, right, anchor_start, anchor_end, through,
                   left_step=60, right_step=60, left_asset='XAUUSD', right_asset='XAGUSD'):
    """Compare the same anchor/time intervals for a positively related pair.

    A positive result uses complete anchors and an unbroken matched observation
    prefix. A later gap or boundary breach cannot erase an earlier observation.
    Both instruments sweeping in one source bar has unknown intrabar ordering.
    """
    if left_asset == right_asset:
        raise ValueError('SMT requires two different instruments.')
    if left_step not in (60, 300) or right_step not in (60, 300):
        raise ValueError('SMT requires closed M1 or M5 source candles.')
    step = lcm(left_step, right_step)
    if not (0 < anchor_start < anchor_end < through and through - anchor_start <= 90 * 86400):
        raise ValueError('Specify a complete anchor and a positive observation window up to 90 days.')
    if any(t % step for t in (anchor_start, anchor_end, through)):
        raise ValueError('SMT boundaries must align to both source resolutions.')
    grids = [_aggregate(b, anchor_start, through, s, step)
             for b, s in ((left, left_step), (right, right_step))]
    expected_anchor = list(range(anchor_start, anchor_end, step))
    result = {'status': 'insufficient_matched_data', 'events': [],
              'pair': [left_asset, right_asset], 'relationship': 'positive',
              'anchor_start_ny': stamp(anchor_start), 'anchor_end_ny': stamp(anchor_end),
              'through_ny': stamp(through), 'precision_seconds': step,
              'entry_confirmed': False, 'historical_observations_preserved': True,
              'scope': 'Selected-range liquidity divergence only; not all swing SMT or an entry signal.'}
    if any(any(t not in g for t in expected_anchor) for g in grids):
        result['reason'] = 'At least one selected anchor is incomplete. No SMT conclusion is supported.'
        return result
    anchors = [_anchor({t: g[t] for t in expected_anchor}) for g in grids]
    result['anchors'] = dict(zip(result['pair'], anchors))
    observations = [{t: b for t, b in g.items() if t >= anchor_end} for g in grids]
    seen = {'buy_side': [False, False], 'sell_side': [False, False]}
    reported = set()
    matched_through = anchor_end
    for t in range(anchor_end, through, step):
        if any(t not in g for g in observations):
            break
        matched_through = t + step
        rows = [g[t] for g in observations]
        for side, bearish in (('buy_side', True), ('sell_side', False)):
            for i, (bar, anchor) in enumerate(zip(rows, anchors)):
                swept = bar['high'] > anchor['high'] if bearish else bar['low'] < anchor['low']
                seen[side][i] = seen[side][i] or swept
            flags = seen[side]
            if side in reported or flags[0] == flags[1]:
                continue
            reported.add(side)
            swept_i = 0 if flags[0] else 1
            other_i = 1 - swept_i
            at = datetime.fromtimestamp(t, NY)
            anchor_at = datetime.fromtimestamp(anchor_start, NY)
            nine_context = (anchor_end - anchor_start == 3600 and
                            anchor_at.hour in (8, 20) and anchor_at.minute == 0 and
                            at.hour == anchor_at.hour + 1 and at.date() == anchor_at.date())
            event = {'direction': 'bearish' if bearish else 'bullish', 'side': side,
                     'context': '9ate8 SMT' if nine_context else 'selected-range SMT',
                     'start_ny': stamp(t), 'end_ny': stamp(t + step),
                     'sweeping_asset': result['pair'][swept_i],
                     'nonconfirming_asset': result['pair'][other_i],
                     'matched_candles': dict(zip(result['pair'], rows)),
                     'other_anchor_side_not_swept_through_event': True,
                     'targets': {asset: _targets(g, a, t, step, bearish)
                                 for asset, g, a in zip(result['pair'], observations, anchors)}}
            bound = anchors[other_i]['high' if bearish else 'low']
            later_sweep = next((s for s, bar in sorted(observations[other_i].items()) if s > t and
                                (bar['high'] > bound if bearish else bar['low'] < bound)), None)
            event['other_asset_later_sweep_ny'] = stamp(later_sweep) if later_sweep is not None else None
            event['interpretation'] = ('The matched divergence was observed. Later range outcomes or '
                                       'a later sweep do not erase it; entry and trade outcome remain separate.')
            result['events'].append(event)
    result['matched_contiguous_through_ny'] = stamp(matched_through)
    result['complete'] = matched_through == through
    if result['events']:
        result['status'] = 'observed'
        first = result['events'][0]
        result['spoken_summary'] = (
            f"{first['direction'].capitalize()} {first['context']} was observed in the "
            f"{first['start_ny']} to {first['end_ny']} candle: {first['sweeping_asset']} "
            f"swept its anchor {first['side']}, while {first['nonconfirming_asset']} did not. "
            'This verifies the divergence, not a member execution.')
    elif result['complete']:
        result['status'] = 'not_observed'
        result['reason'] = ('No selected-range divergence was established at this source resolution. '
                            'Same-bar ordering and other swing anchors are not resolved.')
    else:
        result['reason'] = 'Matched observation coverage has a gap; absence of SMT cannot be concluded.'
    return result
